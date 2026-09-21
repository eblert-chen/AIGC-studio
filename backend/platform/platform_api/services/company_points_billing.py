from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyInvoice,
    CompanyModelGrant,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointPriceVersion,
    CompanyPointWalletAccount,
    EnterpriseBillingCycleStatus,
    EnterpriseContractStatus,
    EnterpriseInvoiceStatus,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    PointLotSourceKind,
    PointPriceVersionStatus,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskPointLotAllocation,
    TaskStatus,
    WalletAccount,
    new_id,
    utcnow,
)
from .errors import ConflictError, InsufficientBalanceError, NotFoundError


POINT_VALUE_CENTS = 10
COMPANY_POINTS_BILLING_VERSION = 2
MAX_POINTS = 9_000_000_000_000_000
MAX_IDEMPOTENCY_KEY_LENGTH = 120
_ACTIVE_TASK_STATUSES = {
    TaskStatus.DRAFT,
    TaskStatus.QUEUED,
    TaskStatus.PROCESSING,
}


@dataclass(frozen=True)
class PointMigrationConversion:
    source_cents: int
    converted_points: int
    legacy_points: int
    rounding_remainder_cents: int
    rounding_grant_points: int
    rounding_subsidy_cents: int


@dataclass(frozen=True)
class CompanyPointMigrationResult:
    company: Company
    wallet: CompanyPointWalletAccount
    conversion: PointMigrationConversion
    changed: bool


def cents_to_points_migration(amount_cents: int) -> PointMigrationConversion:
    """Convert legacy cents without silently losing a remainder.

    The legacy lot carries only complete 10-cent units.  A remainder receives
    one separately classified migration-rounding point whose cash basis and
    platform subsidy remain explicit for later revenue reporting.
    """

    if isinstance(amount_cents, bool) or amount_cents < 0:
        raise ConflictError("迁移余额不能小于 0 分")
    legacy_points, remainder = divmod(amount_cents, POINT_VALUE_CENTS)
    rounding_grant = 1 if remainder else 0
    return PointMigrationConversion(
        source_cents=amount_cents,
        converted_points=legacy_points + rounding_grant,
        legacy_points=legacy_points,
        rounding_remainder_cents=remainder,
        rounding_grant_points=rounding_grant,
        rounding_subsidy_cents=(POINT_VALUE_CENTS - remainder if remainder else 0),
    )


def _point_price_candidate_revision(
    *, grant: CompanyModelGrant, mode: str, source_cents: int, candidate_points: int
) -> str:
    canonical = json.dumps(
        {
            "schema_version": 1,
            "company_id": grant.company_id,
            "model_id": grant.model_id,
            "grant_id": grant.id,
            "mode": mode,
            "source_cents": source_cents,
            "candidate_points": candidate_points,
            "conversion_divisor_cents": POINT_VALUE_CENTS,
            "approval_status": "pending",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _migration_lot_idempotency_key(parent_key: str, lot_kind: str) -> str:
    derived = f"{parent_key}:{lot_kind}"
    if len(derived) <= MAX_IDEMPOTENCY_KEY_LENGTH:
        return derived
    digest = hashlib.sha256(derived.encode("utf-8")).hexdigest()
    return f"migration-lot:{lot_kind}:{digest}"


class CompanyPointBillingService:
    @staticmethod
    def _value_slice(
        lot: CompanyPointLot,
        *,
        settled_before: int,
        settled_points: int,
    ) -> tuple[int, int, int]:
        end = settled_before + settled_points
        cash = (
            lot.cash_basis_cents * end // lot.original_points
            - lot.cash_basis_cents * settled_before // lot.original_points
        )
        receivable = (
            lot.receivable_basis_cents * end // lot.original_points
            - lot.receivable_basis_cents * settled_before // lot.original_points
        )
        subsidy = settled_points * POINT_VALUE_CENTS - cash - receivable
        if subsidy < 0:
            raise ConflictError("企业积分批次价值分摊无效")
        return cash, receivable, subsidy

    @staticmethod
    def _locked_company(session: Session, company_id: str) -> Company:
        session.flush()
        company = session.scalar(
            select(Company)
            .where(Company.id == company_id)
            # Serialize company state without blocking the FK KEY SHARE lock
            # needed by concurrent immutable ledger inserts (PostgreSQL).
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if company is None:
            raise NotFoundError("公司不存在")
        return company

    @staticmethod
    def _locked_wallet(
        session: Session, company_id: str
    ) -> CompanyPointWalletAccount:
        # Production sessions disable autoflush. Preserve this transaction's
        # pending work before replacing any pre-lock ORM projection.
        session.flush()
        wallet = session.scalar(
            select(CompanyPointWalletAccount)
            .where(CompanyPointWalletAccount.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if wallet is None:
            raise NotFoundError("公司积分钱包不存在")
        return wallet

    @staticmethod
    def _existing_entry(
        session: Session,
        *,
        company_id: str,
        idempotency_key: str,
        expected_kind: PointLedgerKind,
        expected_amount: int | None,
        task_id: str | None,
        expected_note: str | None = None,
    ) -> CompanyPointLedgerEntry | None:
        entry = session.scalar(
            select(CompanyPointLedgerEntry).where(
                CompanyPointLedgerEntry.company_id == company_id,
                CompanyPointLedgerEntry.idempotency_key == idempotency_key,
            )
        )
        if entry is not None and (
            entry.kind != expected_kind
            or (expected_amount is not None and entry.amount_points != expected_amount)
            or entry.task_id != task_id
            or (expected_note is not None and entry.note != expected_note)
        ):
            raise ConflictError("幂等键已被另一笔不同的积分账务操作使用")
        return entry

    @staticmethod
    def _locked_point_task(
        session: Session, *, company_id: str, task_id: str
    ) -> GenerationTask:
        session.flush()
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            raise NotFoundError("当前公司下不存在该任务")
        if (
            task.billing_unit != BillingUnit.POINT
            or task.billing_version != COMPANY_POINTS_BILLING_VERSION
            or task.personal_workspace_id is not None
        ):
            raise ConflictError("任务不是企业积分计费任务")
        return task

    @classmethod
    def migrate(
        cls,
        session: Session,
        *,
        company_id: str,
        expected_available_cents: int,
        idempotency_key: str,
    ) -> CompanyPointMigrationResult:
        if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ConflictError("迁移幂等键无效")
        company = cls._locked_company(session, company_id)
        point_wallet = session.scalar(
            select(CompanyPointWalletAccount)
            .where(CompanyPointWalletAccount.company_id == company_id)
            .with_for_update()
        )
        if company.billing_version == COMPANY_POINTS_BILLING_VERSION:
            if point_wallet is None:
                raise ConflictError("企业计费版本与积分钱包不一致")
            if point_wallet.migration_idempotency_key != idempotency_key:
                raise ConflictError("企业已经通过另一笔操作迁移到积分计费")
            if point_wallet.migrated_from_available_cents != expected_available_cents:
                raise ConflictError("迁移重放的旧余额快照不一致")
            return CompanyPointMigrationResult(
                company=company,
                wallet=point_wallet,
                conversion=cents_to_points_migration(
                    point_wallet.migrated_from_available_cents
                ),
                changed=False,
            )
        if company.billing_version != 1 or point_wallet is not None:
            raise ConflictError("企业计费状态不允许执行积分迁移")

        cents_wallet = session.scalar(
            select(WalletAccount)
            .where(WalletAccount.company_id == company_id)
            .with_for_update()
        )
        if cents_wallet is None:
            raise NotFoundError("公司旧币种钱包不存在")
        if cents_wallet.available_cents != expected_available_cents:
            raise ConflictError("旧余额已变化，请刷新后重试迁移")
        if cents_wallet.reserved_cents != 0:
            raise ConflictError("公司仍有旧币种预占，不能迁移")
        ledger_projection = session.execute(
            select(
                func.coalesce(func.sum(LedgerEntry.available_delta_cents), 0),
                func.coalesce(func.sum(LedgerEntry.reserved_delta_cents), 0),
            ).where(LedgerEntry.company_id == company_id)
        ).one()
        if (
            int(ledger_projection[0]) != cents_wallet.available_cents
            or int(ledger_projection[1]) != cents_wallet.reserved_cents
        ):
            raise ConflictError("旧币种钱包与不可变账本无法对账，不能迁移")
        active_task = session.scalar(
            select(GenerationTask.id)
            .where(
                GenerationTask.company_id == company_id,
                GenerationTask.status.in_(_ACTIVE_TASK_STATUSES),
            )
            .limit(1)
            .with_for_update()
        )
        unsettled_task = session.scalar(
            select(GenerationTask.id)
            .where(
                GenerationTask.company_id == company_id,
                GenerationTask.reserved_cents > 0,
            )
            .limit(1)
            .with_for_update()
        )
        malformed_terminal_task = session.scalar(
            select(GenerationTask.id)
            .where(
                GenerationTask.company_id == company_id,
                or_(
                    (
                        (GenerationTask.status == TaskStatus.SUCCEEDED)
                        & (GenerationTask.actual_cost_cents.is_(None))
                    ),
                    (
                        GenerationTask.status.in_(
                            {TaskStatus.FAILED, TaskStatus.CANCELLED}
                        )
                        & (GenerationTask.reserved_cents != 0)
                    ),
                ),
            )
            .limit(1)
            .with_for_update()
        )
        if (
            active_task is not None
            or unsettled_task is not None
            or malformed_terminal_task is not None
        ):
            raise ConflictError("公司仍有在途或未释放的旧币种任务，不能迁移")

        unsettled_outbox = session.scalar(
            select(RelaySubmissionOutbox.id)
            .where(
                RelaySubmissionOutbox.company_id == company_id,
                RelaySubmissionOutbox.status.in_(
                    {
                        RelayOutboxStatus.PENDING,
                        RelayOutboxStatus.PROCESSING,
                        RelayOutboxStatus.RETRY,
                        RelayOutboxStatus.RECONCILIATION_REQUIRED,
                    }
                ),
            )
            .limit(1)
            .with_for_update()
        )
        if unsettled_outbox is not None:
            raise ConflictError("公司仍有待派发或待对账的 Relay 提交，不能迁移")

        legacy_tasks = list(
            session.scalars(
                select(GenerationTask)
                .where(GenerationTask.company_id == company_id)
                .order_by(GenerationTask.id)
                .with_for_update()
            ).all()
        )
        task_ledger_entries = list(
            session.scalars(
                select(LedgerEntry)
                .where(
                    LedgerEntry.company_id == company_id,
                    LedgerEntry.task_id.is_not(None),
                )
                .order_by(LedgerEntry.task_id, LedgerEntry.created_at, LedgerEntry.id)
                .with_for_update()
            ).all()
        )
        entries_by_task: dict[str, list[LedgerEntry]] = {}
        for entry in task_ledger_entries:
            if entry.task_id is None:
                continue
            entries_by_task.setdefault(entry.task_id, []).append(entry)
        task_ids = {task.id for task in legacy_tasks}
        if any(task_id not in task_ids for task_id in entries_by_task):
            raise ConflictError("旧币种任务账本存在无法归属的任务，不能迁移")

        for task in legacy_tasks:
            if (
                task.billing_unit != BillingUnit.CNY_CENT
                or task.billing_version != 1
                or task.quote_cents is None
                or task.quote_cents <= 0
                or task.quote_points is not None
                or task.reserved_points != 0
                or task.actual_cost_points is not None
            ):
                raise ConflictError("旧币种任务计费合同异常，不能迁移")
            entries = entries_by_task.get(task.id, [])
            reserve_entries = [
                entry for entry in entries if entry.kind == LedgerKind.RESERVE
            ]
            settle_entries = [
                entry for entry in entries if entry.kind == LedgerKind.SETTLE
            ]
            release_entries = [
                entry for entry in entries if entry.kind == LedgerKind.RELEASE
            ]
            if len(reserve_entries) != 1 or len(entries) != (
                len(reserve_entries) + len(settle_entries) + len(release_entries)
            ):
                raise ConflictError("旧币种任务账本操作序列异常，不能迁移")
            reserve_entry = reserve_entries[0]
            if (
                reserve_entry.amount_cents != task.quote_cents
                or reserve_entry.available_delta_cents != -task.quote_cents
                or reserve_entry.reserved_delta_cents != task.quote_cents
            ):
                raise ConflictError("旧币种任务预占分录无法对账，不能迁移")

            if task.status == TaskStatus.SUCCEEDED:
                if (
                    len(settle_entries) != 1
                    or release_entries
                    or task.reserved_cents != 0
                    or task.actual_cost_cents is None
                    or task.actual_cost_cents < 0
                    or task.actual_cost_cents > task.quote_cents
                ):
                    raise ConflictError("旧币种成功任务结算异常，不能迁移")
                settle_entry = settle_entries[0]
                if (
                    settle_entry.amount_cents != task.actual_cost_cents
                    or settle_entry.available_delta_cents
                    != task.quote_cents - task.actual_cost_cents
                    or settle_entry.reserved_delta_cents != -task.quote_cents
                ):
                    raise ConflictError("旧币种成功任务分录无法对账，不能迁移")
            elif task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                if (
                    settle_entries
                    or len(release_entries) != 1
                    or task.reserved_cents != 0
                    or task.actual_cost_cents not in {None, 0}
                ):
                    raise ConflictError("旧币种失败或取消任务结算异常，不能迁移")
                release_entry = release_entries[0]
                if (
                    release_entry.amount_cents != task.quote_cents
                    or release_entry.available_delta_cents != task.quote_cents
                    or release_entry.reserved_delta_cents != -task.quote_cents
                ):
                    raise ConflictError("旧币种失败或取消任务分录无法对账，不能迁移")
            else:
                raise ConflictError("公司仍有非终态旧币种任务，不能迁移")

        conversion = cents_to_points_migration(cents_wallet.available_cents)
        point_wallet = CompanyPointWalletAccount(
            company_id=company_id,
            available_points=conversion.converted_points,
            reserved_points=0,
            migration_idempotency_key=idempotency_key,
            migrated_from_available_cents=conversion.source_cents,
            migration_remainder_cents=conversion.rounding_remainder_cents,
            migration_rounding_grant_points=conversion.rounding_grant_points,
        )
        session.add(point_wallet)
        if conversion.legacy_points:
            session.add(
                CompanyPointLot(
                    company_id=company_id,
                    source_kind=PointLotSourceKind.LEGACY,
                    original_points=conversion.legacy_points,
                    available_points=conversion.legacy_points,
                    reserved_points=0,
                    settled_points=0,
                    cash_basis_cents=conversion.legacy_points * POINT_VALUE_CENTS,
                    subsidy_cents=0,
                    idempotency_key=_migration_lot_idempotency_key(
                        idempotency_key, "legacy"
                    ),
                )
            )
        if conversion.rounding_grant_points:
            session.add(
                CompanyPointLot(
                    company_id=company_id,
                    source_kind=PointLotSourceKind.MIGRATION_REMAINDER,
                    original_points=1,
                    available_points=1,
                    reserved_points=0,
                    settled_points=0,
                    cash_basis_cents=conversion.rounding_remainder_cents,
                    subsidy_cents=conversion.rounding_subsidy_cents,
                    idempotency_key=_migration_lot_idempotency_key(
                        idempotency_key, "rounding"
                    ),
                )
            )
        session.add(
            CompanyPointLedgerEntry(
                company_id=company_id,
                kind=PointLedgerKind.MIGRATION,
                amount_points=conversion.converted_points,
                available_delta_points=conversion.converted_points,
                reserved_delta_points=0,
                idempotency_key=idempotency_key,
                task_id=None,
                note=(
                    f"legacy_cents={conversion.source_cents};"
                    f"remainder_cents={conversion.rounding_remainder_cents};"
                    f"rounding_subsidy_cents={conversion.rounding_subsidy_cents}"
                ),
            )
        )

        grants = list(
            session.scalars(
                select(CompanyModelGrant)
                .where(CompanyModelGrant.company_id == company_id)
                .order_by(CompanyModelGrant.id)
                .with_for_update()
            ).all()
        )
        for grant in grants:
            if grant.price_per_second_cents is not None:
                candidate_points = (
                    grant.price_per_second_cents + POINT_VALUE_CENTS - 1
                ) // POINT_VALUE_CENTS
                grant.point_price_candidate_per_second = candidate_points
                grant.point_price_candidate_per_item = None
                grant.point_price_candidate_revision = _point_price_candidate_revision(
                    grant=grant,
                    mode="per_second",
                    source_cents=grant.price_per_second_cents,
                    candidate_points=candidate_points,
                )
            elif grant.price_per_item_cents is not None:
                candidate_points = (
                    grant.price_per_item_cents + POINT_VALUE_CENTS - 1
                ) // POINT_VALUE_CENTS
                grant.point_price_candidate_per_item = candidate_points
                grant.point_price_candidate_per_second = None
                grant.point_price_candidate_revision = _point_price_candidate_revision(
                    grant=grant,
                    mode="per_item",
                    source_cents=grant.price_per_item_cents,
                    candidate_points=candidate_points,
                )
            else:
                raise ConflictError("公司模型授权缺少旧币种价格，不能迁移")
            version_id = new_id()
            content_sha256 = grant.point_price_candidate_revision.removeprefix(
                "sha256:"
            )
            session.add(
                CompanyPointPriceVersion(
                    id=version_id,
                    company_id=company_id,
                    grant_id=grant.id,
                    model_id=grant.model_id,
                    status=PointPriceVersionStatus.CANDIDATE,
                    billing_mode=(
                        "per_second"
                        if grant.point_price_candidate_per_second is not None
                        else "per_item"
                    ),
                    unit_price_points=candidate_points,
                    source_price_cents=(
                        grant.price_per_second_cents
                        if grant.price_per_second_cents is not None
                        else grant.price_per_item_cents
                    ),
                    formula_version="legacy-cents-ceil-div-10:v1",
                    content_sha256=content_sha256,
                    supersedes_version_id=None,
                    created_by_user_id=None,
                    created_by_system_key="company-points-migration",
                )
            )
            grant.point_price_candidate_version_id = version_id
            # Conversion creates a review candidate, never a live customer price.
            grant.enabled = False
            grant.price_per_second_cents = None
            grant.price_per_item_cents = None
            grant.price_per_second_points = None
            grant.price_per_item_points = None
            grant.point_price_candidate_created_at = utcnow()

        company.billing_version = COMPANY_POINTS_BILLING_VERSION
        session.flush()
        return CompanyPointMigrationResult(
            company=company,
            wallet=point_wallet,
            conversion=conversion,
            changed=True,
        )

    @classmethod
    def credit(
        cls,
        session: Session,
        *,
        company_id: str,
        amount_points: int,
        source_kind: PointLotSourceKind,
        cash_basis_cents: int,
        subsidy_cents: int,
        receivable_basis_cents: int = 0,
        idempotency_key: str,
        note: str = "",
        expires_at: datetime | None = None,
    ) -> tuple[CompanyPointWalletAccount, CompanyPointLedgerEntry, bool]:
        if amount_points <= 0 or amount_points > MAX_POINTS:
            raise ConflictError("积分入账数量必须大于 0")
        if expires_at is not None:
            raise ConflictError("积分到期结算尚未启用，不能创建带到期日的批次")
        if cash_basis_cents < 0 or receivable_basis_cents < 0 or subsidy_cents < 0:
            raise ConflictError("积分批次现金基础或补贴不能小于 0")
        if (
            cash_basis_cents + receivable_basis_cents + subsidy_cents
            != amount_points * POINT_VALUE_CENTS
        ):
            raise ConflictError("积分批次现金基础与补贴无法对账")
        company = cls._locked_company(session, company_id)
        if company.billing_version != COMPANY_POINTS_BILLING_VERSION:
            raise ConflictError("公司尚未启用积分计费")
        wallet = cls._locked_wallet(session, company_id)
        existing = cls._existing_entry(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=PointLedgerKind.CREDIT,
            expected_amount=amount_points,
            task_id=None,
        )
        if existing is not None:
            lot = session.scalar(
                select(CompanyPointLot).where(
                    CompanyPointLot.company_id == company_id,
                    CompanyPointLot.idempotency_key == idempotency_key,
                )
            )
            existing_expiry = (
                lot.expires_at.isoformat() if lot is not None and lot.expires_at else None
            )
            requested_expiry = expires_at.isoformat() if expires_at else None
            if (
                lot is None
                or lot.source_kind != source_kind
                or lot.original_points != amount_points
                or lot.cash_basis_cents != cash_basis_cents
                or lot.receivable_basis_cents != receivable_basis_cents
                or lot.subsidy_cents != subsidy_cents
                or existing_expiry != requested_expiry
                or existing.note != note
            ):
                raise ConflictError("积分入账幂等键对应的批次意图不一致")
            return wallet, existing, False
        if source_kind != PointLotSourceKind.PROMOTIONAL:
            raise ConflictError("通用积分入账只允许促销批次；其他来源必须走受控专用流程")
        if wallet.available_points > MAX_POINTS - amount_points:
            raise ConflictError("入账后公司积分余额超出系统上限")
        wallet.available_points += amount_points
        session.add(
            CompanyPointLot(
                company_id=company_id,
                source_kind=source_kind,
                original_points=amount_points,
                available_points=amount_points,
                reserved_points=0,
                settled_points=0,
                cash_basis_cents=cash_basis_cents,
                receivable_basis_cents=receivable_basis_cents,
                subsidy_cents=subsidy_cents,
                idempotency_key=idempotency_key,
                expires_at=expires_at,
            )
        )
        entry = CompanyPointLedgerEntry(
            company_id=company_id,
            kind=PointLedgerKind.CREDIT,
            amount_points=amount_points,
            available_delta_points=amount_points,
            reserved_delta_points=0,
            idempotency_key=idempotency_key,
            task_id=None,
            note=note,
        )
        session.add(entry)
        session.flush()
        return wallet, entry, True

    @classmethod
    def reserve(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        amount_points: int,
        idempotency_key: str,
    ) -> tuple[CompanyPointWalletAccount, CompanyPointLedgerEntry]:
        if amount_points <= 0 or amount_points > MAX_POINTS:
            raise ConflictError("预占积分必须大于 0")
        # Keep the enterprise lock order stable across admission, month-end,
        # dunning and settlement.  A billing hold is a credit-risk control: it
        # must stop new CONTRACT exposure without confiscating point lots the
        # customer has already paid for.
        company = cls._locked_company(session, company_id)
        if company.billing_version != COMPANY_POINTS_BILLING_VERSION:
            raise ConflictError("公司尚未启用积分计费")
        billing_account = session.scalar(
            select(CompanyBillingAccount)
            .where(CompanyBillingAccount.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        wallet = cls._locked_wallet(session, company_id)
        existing = cls._existing_entry(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=PointLedgerKind.RESERVE,
            expected_amount=amount_points,
            task_id=task_id,
        )
        if existing is not None:
            return wallet, existing
        if wallet.debt_points > 0:
            raise ConflictError("企业钱包存在拒付债务，暂不能创建新任务")
        task = cls._locked_point_task(session, company_id=company_id, task_id=task_id)
        if task.status != TaskStatus.DRAFT or task.reserved_points != 0:
            raise ConflictError("只有未预占的草稿任务可以预占积分")
        if task.quote_points != amount_points:
            raise ConflictError("预占积分必须等于任务报价")
        if wallet.available_points < amount_points:
            raise InsufficientBalanceError()

        now = utcnow()
        credit_on_hold = billing_account is not None and (
            billing_account.billing_hold
            or session.scalar(
                select(CompanyInvoice.id).where(
                    CompanyInvoice.company_id == company_id,
                    CompanyInvoice.paid_cents < CompanyInvoice.total_cents,
                    or_(
                        CompanyInvoice.status.in_(
                            {EnterpriseInvoiceStatus.OVERDUE, EnterpriseInvoiceStatus.DISPUTED}
                        ),
                        and_(
                            CompanyInvoice.status.in_(
                                {EnterpriseInvoiceStatus.ISSUED, EnterpriseInvoiceStatus.PARTIALLY_PAID}
                            ),
                            CompanyInvoice.due_at <= now,
                        ),
                    ),
                ).limit(1)
            ) is not None
        )
        lot_predicates = [
            CompanyPointLot.company_id == company_id,
            CompanyPointLot.available_points > 0,
            or_(
                CompanyPointLot.expires_at.is_(None),
                CompanyPointLot.expires_at > now,
            ),
        ]
        # CONTRACT lots are durable capacity, not prepaid customer property.
        # A lot may outlive its issuing cycle for historical settlement, but a
        # new reserve must have a currently effective contract and an open
        # accounting period that can receive the new exposure. The company and
        # account locks above serialize this read with cycle/contract changes.
        eligible_contract_ids = []
        if billing_account is not None and not credit_on_hold:
            eligible_contract_ids = list(
                session.scalars(
                    select(CompanyBillingContractVersion.id)
                    .join(
                        CompanyBillingCycle,
                        CompanyBillingCycle.contract_version_id
                        == CompanyBillingContractVersion.id,
                    )
                    .where(
                        CompanyBillingContractVersion.id
                        == billing_account.active_contract_version_id,
                        CompanyBillingContractVersion.company_id == company_id,
                        CompanyBillingContractVersion.status
                        == EnterpriseContractStatus.ACTIVE,
                        CompanyBillingContractVersion.effective_at <= now,
                        or_(
                            CompanyBillingContractVersion.expires_at.is_(None),
                            CompanyBillingContractVersion.expires_at > now,
                        ),
                        CompanyBillingCycle.company_id == company_id,
                        CompanyBillingCycle.status == EnterpriseBillingCycleStatus.OPEN,
                        CompanyBillingCycle.frozen_at.is_(None),
                        CompanyBillingCycle.period_start <= now,
                        CompanyBillingCycle.period_end > now,
                        CompanyBillingCycle.period_start
                        >= CompanyBillingContractVersion.effective_at,
                        or_(
                            CompanyBillingContractVersion.expires_at.is_(None),
                            CompanyBillingCycle.period_end
                            <= CompanyBillingContractVersion.expires_at,
                        ),
                    )
                ).all()
            )
        lot_predicates.append(
            or_(
                CompanyPointLot.source_kind != PointLotSourceKind.CONTRACT,
                CompanyPointLot.contract_version_id.in_(eligible_contract_ids),
            )
        )
        lots = list(
            session.scalars(
                select(CompanyPointLot)
                .where(*lot_predicates)
                .order_by(
                    CompanyPointLot.expires_at.is_(None),
                    CompanyPointLot.expires_at,
                    CompanyPointLot.created_at,
                    CompanyPointLot.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        eligible_available_points = sum(lot.available_points for lot in lots)
        if eligible_available_points < amount_points:
            if credit_on_hold:
                raise ConflictError(
                    "企业月结账户已暂停；非授信积分不足，合同授信积分不能用于新任务"
                )
            if session.scalar(
                select(CompanyPointLot.id).where(
                    CompanyPointLot.company_id == company_id,
                    CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
                    CompanyPointLot.available_points > 0,
                ).limit(1)
            ) is not None:
                raise ConflictError(
                    "合同授信积分缺少当前有效合同或开放账期；非授信积分不足，不能创建新任务"
                )
            raise ConflictError("积分钱包与积分批次余额不一致")
        remaining = amount_points
        for lot in lots:
            allocated = min(lot.available_points, remaining)
            if not allocated:
                continue
            lot.available_points -= allocated
            lot.reserved_points += allocated
            session.add(
                TaskPointLotAllocation(
                    company_id=company_id,
                    task_id=task_id,
                    lot_id=lot.id,
                    allocated_points=allocated,
                    reserved_points=allocated,
                    settled_points=0,
                    released_points=0,
                )
            )
            remaining -= allocated
            if remaining == 0:
                break
        if remaining:
            raise ConflictError("积分钱包与积分批次余额不一致")

        wallet.available_points -= amount_points
        wallet.reserved_points += amount_points
        task.reserved_points = amount_points
        task.status = TaskStatus.QUEUED
        entry = CompanyPointLedgerEntry(
            company_id=company_id,
            kind=PointLedgerKind.RESERVE,
            amount_points=amount_points,
            available_delta_points=-amount_points,
            reserved_delta_points=amount_points,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        return wallet, entry

    @classmethod
    def settle_success(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        actual_cost_points: int,
        idempotency_key: str,
        allow_terminal: bool = False,
    ) -> tuple[CompanyPointWalletAccount, CompanyPointLedgerEntry]:
        """Settle a reserved task.

        `allow_terminal` exists only for reservation recovery: a task can reach
        a terminal status while its reservation is still held, and every
        ordinary path refuses to touch an already-terminal task.
        """
        if actual_cost_points < 0 or actual_cost_points > MAX_POINTS:
            raise ConflictError("实际积分不能小于 0")
        # Match the enterprise-cycle lock order (company -> billing account ->
        # wallet -> task/lots).  Prepaid companies have no billing account and
        # continue through the same point settlement path.
        company = cls._locked_company(session, company_id)
        if company.billing_version != COMPANY_POINTS_BILLING_VERSION:
            raise ConflictError("公司尚未启用积分计费")
        billing_account = session.scalar(
            select(CompanyBillingAccount)
            .where(CompanyBillingAccount.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        wallet = cls._locked_wallet(session, company_id)
        existing = cls._existing_entry(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=PointLedgerKind.SETTLE,
            expected_amount=actual_cost_points,
            task_id=task_id,
        )
        task = cls._locked_point_task(session, company_id=company_id, task_id=task_id)
        if existing is not None:
            return wallet, existing
        if allow_terminal:
            if task.status != TaskStatus.SUCCEEDED:
                raise ConflictError("补偿结算只支持已经成功的任务")
        elif task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("任务当前状态不能成功结算")
        reserved = task.reserved_points
        if (
            reserved <= 0
            or task.quote_points != reserved
            or actual_cost_points != reserved
        ):
            raise ConflictError("首期积分任务必须按完整不可变报价结算")

        allocation_rows = session.execute(
            select(TaskPointLotAllocation, CompanyPointLot)
            .join(CompanyPointLot, CompanyPointLot.id == TaskPointLotAllocation.lot_id)
            .where(
                TaskPointLotAllocation.company_id == company_id,
                TaskPointLotAllocation.task_id == task_id,
                TaskPointLotAllocation.reserved_points > 0,
            )
            .order_by(TaskPointLotAllocation.created_at, TaskPointLotAllocation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if sum(allocation.reserved_points for allocation, _ in allocation_rows) != reserved:
            raise ConflictError("任务预占与积分批次分配不一致")
        remaining_to_settle = actual_cost_points
        value_rows: list[tuple[TaskPointLotAllocation, int, int, int, int]] = []
        for allocation, lot in allocation_rows:
            settled = min(allocation.reserved_points, remaining_to_settle)
            released = allocation.reserved_points - settled
            settled_before = lot.settled_points
            allocation.reserved_points = 0
            allocation.settled_points += settled
            allocation.released_points += released
            lot.reserved_points -= settled + released
            lot.settled_points += settled
            lot.available_points += released
            remaining_to_settle -= settled
            if settled:
                cash, receivable, subsidy = cls._value_slice(
                    lot,
                    settled_before=settled_before,
                    settled_points=settled,
                )
                value_rows.append((allocation, settled, cash, receivable, subsidy))
        if remaining_to_settle:
            raise ConflictError("任务积分批次不足以完成结算")

        receivable_total_cents = sum(row[3] for row in value_rows)
        if receivable_total_cents:
            if billing_account is None:
                raise ConflictError("合同积分结算缺少企业月结账户")
            billing_account.unbilled_receivable_cents += receivable_total_cents

        refund = reserved - actual_cost_points
        wallet.available_points += refund
        wallet.reserved_points -= reserved
        task.reserved_points = 0
        task.actual_cost_points = actual_cost_points
        task.status = TaskStatus.SUCCEEDED
        entry = CompanyPointLedgerEntry(
            company_id=company_id,
            kind=PointLedgerKind.SETTLE,
            amount_points=actual_cost_points,
            available_delta_points=refund,
            reserved_delta_points=-reserved,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        for allocation, settled, cash, receivable, subsidy in value_rows:
            session.add(
                PointLotSettlementValueAllocation(
                    company_id=company_id,
                    personal_workspace_id=None,
                    task_id=task_id,
                    company_task_allocation_id=allocation.id,
                    personal_task_allocation_id=None,
                    company_settle_ledger_id=entry.id,
                    personal_settle_ledger_id=None,
                    settled_points=settled,
                    cash_basis_cents=cash,
                    receivable_basis_cents=receivable,
                    subsidy_cents=subsidy,
                )
            )
        session.flush()
        return wallet, entry

    @classmethod
    def release_failure(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        idempotency_key: str,
        failure_reason: str,
        terminal_status: TaskStatus = TaskStatus.FAILED,
        allow_terminal: bool = False,
    ) -> tuple[CompanyPointWalletAccount, CompanyPointLedgerEntry]:
        if terminal_status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ConflictError("释放积分只能进入失败或取消状态")
        wallet = cls._locked_wallet(session, company_id)
        task = cls._locked_point_task(session, company_id=company_id, task_id=task_id)
        release_amount = task.reserved_points
        existing = cls._existing_entry(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=PointLedgerKind.RELEASE,
            expected_amount=task.quote_points,
            task_id=task_id,
            expected_note=failure_reason[:240],
        )
        if existing is not None:
            if task.status != terminal_status:
                raise ConflictError("释放积分幂等重放的任务终态不一致")
            return wallet, existing
        if allow_terminal:
            if task.status != terminal_status:
                raise ConflictError("补偿释放的任务终态与预期不一致")
        elif task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("任务当前状态不能释放积分")
        if release_amount <= 0:
            raise ConflictError("任务没有可释放的预占积分")

        allocation_rows = session.execute(
            select(TaskPointLotAllocation, CompanyPointLot)
            .join(CompanyPointLot, CompanyPointLot.id == TaskPointLotAllocation.lot_id)
            .where(
                TaskPointLotAllocation.company_id == company_id,
                TaskPointLotAllocation.task_id == task_id,
                TaskPointLotAllocation.reserved_points > 0,
            )
            .order_by(TaskPointLotAllocation.created_at, TaskPointLotAllocation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if sum(allocation.reserved_points for allocation, _ in allocation_rows) != release_amount:
            raise ConflictError("任务预占与积分批次分配不一致")
        for allocation, lot in allocation_rows:
            released = allocation.reserved_points
            allocation.reserved_points = 0
            allocation.released_points += released
            lot.reserved_points -= released
            lot.available_points += released

        wallet.available_points += release_amount
        wallet.reserved_points -= release_amount
        task.reserved_points = 0
        task.status = terminal_status
        task.failure_reason = failure_reason
        entry = CompanyPointLedgerEntry(
            company_id=company_id,
            kind=PointLedgerKind.RELEASE,
            amount_points=release_amount,
            available_delta_points=release_amount,
            reserved_delta_points=-release_amount,
            idempotency_key=idempotency_key,
            task_id=task_id,
            note=failure_reason[:240],
        )
        session.add(entry)
        session.flush()
        return wallet, entry
