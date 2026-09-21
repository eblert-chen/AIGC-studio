from __future__ import annotations

from datetime import datetime

from typing import Any

from sqlalchemy import BigInteger, Integer, String, case, func, literal, select, true
from sqlalchemy.orm import Session

from ..models import (
    BillingUnit,
    Company,
    CompanyPointLedgerEntry,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    PointLedgerKind,
    TaskStatus,
    WalletAccount,
)
from .company_points_billing import CompanyPointBillingService
from .errors import ConflictError, InsufficientBalanceError, NotFoundError


MAX_MONEY_CENTS = 9_000_000_000_000_000


class WalletService:
    @staticmethod
    def _locked_account(session: Session, company_id: str) -> WalletAccount:
        # Row locks serialize writers, but do not refresh an ORM object loaded
        # before waiting for that lock. Flush our own changes before replacing
        # a pre-lock projection (production sessions use autoflush=False).
        session.flush()
        account = session.scalar(
            select(WalletAccount)
            .where(WalletAccount.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise NotFoundError("公司钱包不存在")
        return account

    @staticmethod
    def _existing(
        session: Session,
        *,
        company_id: str,
        idempotency_key: str,
        expected_kind: LedgerKind,
        expected_amount: int | None,
        task_id: str | None,
        expected_note: str | None = None,
    ) -> LedgerEntry | None:
        entry = session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.company_id == company_id,
                LedgerEntry.idempotency_key == idempotency_key,
            )
        )
        if entry and (
            entry.kind != expected_kind
            or (
                expected_amount is not None
                and entry.amount_cents != expected_amount
            )
            or entry.task_id != task_id
            or (expected_note is not None and entry.note != expected_note)
        ):
            raise ConflictError("幂等键已被另一笔不同的账务操作使用")
        return entry

    @classmethod
    def recharge(
        cls,
        session: Session,
        *,
        company_id: str,
        amount_cents: int,
        idempotency_key: str,
        note: str = "",
    ) -> tuple[WalletAccount, LedgerEntry, bool]:
        if amount_cents <= 0 or amount_cents > MAX_MONEY_CENTS:
            raise ConflictError("充值金额必须大于 0 分")
        session.flush()
        company = session.scalar(
            select(Company)
            .where(Company.id == company_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if company is None:
            raise NotFoundError("公司不存在")
        if company.billing_version != 1:
            raise ConflictError("积分计费企业不能写入旧币种充值账")
        account = cls._locked_account(session, company_id)
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RECHARGE,
            expected_amount=amount_cents,
            task_id=None,
            expected_note=note,
        )
        if existing:
            return account, existing, False
        if account.available_cents > MAX_MONEY_CENTS - amount_cents:
            raise ConflictError("充值后公司余额超出系统上限")
        account.available_cents += amount_cents
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.RECHARGE,
            amount_cents=amount_cents,
            available_delta_cents=amount_cents,
            reserved_delta_cents=0,
            idempotency_key=idempotency_key,
            note=note,
        )
        session.add(entry)
        session.flush()
        return account, entry, True

    @staticmethod
    def recharge_page(
        session: Session,
        *,
        company_id: str,
        page: int,
        page_size: int,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        statement = select(LedgerEntry).where(
            LedgerEntry.company_id == company_id,
            LedgerEntry.kind == LedgerKind.RECHARGE,
        )
        if start_time is not None:
            statement = statement.where(LedgerEntry.created_at >= start_time)
        if end_time is not None:
            statement = statement.where(LedgerEntry.created_at < end_time)
        filtered = statement.subquery("filtered_recharges")
        summary = (
            select(
                func.count().label("recharge_total"),
                func.coalesce(func.sum(filtered.c.amount_cents), 0).label(
                    "recharge_total_amount_cents"
                ),
            )
            .select_from(filtered)
            .cte("recharge_summary")
        )
        page_rows = (
            select(filtered)
            .order_by(
                filtered.c.created_at.desc(),
                filtered.c.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .cte("recharge_page")
        )
        result_rows = list(
            session.execute(
                select(summary, page_rows).select_from(
                    summary.outerjoin(page_rows, true())
                )
            ).mappings()
        )
        if not result_rows:
            return 0, 0, []
        total = int(result_rows[0]["recharge_total"] or 0)
        total_amount_cents = int(
            result_rows[0]["recharge_total_amount_cents"] or 0
        )
        items = [
            {key: row[key] for key in page_rows.c.keys()}
            for row in result_rows
            if row["id"] is not None
        ]
        return total, total_amount_cents, items

    @staticmethod
    def funding_page(
        session: Session,
        *,
        company_id: str,
        page: int,
        page_size: int,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[
        int,
        int,
        int,
        list[dict[str, Any]],
        set[tuple[str, int]],
    ]:
        """Return legacy recharges and point funding without mixing units.

        The page carries two independent totals.  In particular, point
        credits are never multiplied by the nominal purchase anchor and
        reported as cash.
        """

        legacy = select(
            LedgerEntry.id.label("id"),
            LedgerEntry.company_id.label("company_id"),
            literal(BillingUnit.CNY_CENT.value, type_=String(16)).label(
                "billing_unit"
            ),
            literal(1, type_=Integer()).label("billing_version"),
            literal(LedgerKind.RECHARGE.value, type_=String(24)).label("kind"),
            LedgerEntry.amount_cents.label("amount_cents"),
            LedgerEntry.available_delta_cents.label("available_delta_cents"),
            LedgerEntry.reserved_delta_cents.label("reserved_delta_cents"),
            literal(None, type_=BigInteger()).label("amount_points"),
            literal(None, type_=BigInteger()).label("available_delta_points"),
            literal(None, type_=BigInteger()).label("reserved_delta_points"),
            LedgerEntry.idempotency_key.label("idempotency_key"),
            LedgerEntry.task_id.label("task_id"),
            LedgerEntry.note.label("note"),
            LedgerEntry.created_at.label("created_at"),
        ).where(
            LedgerEntry.company_id == company_id,
            LedgerEntry.kind == LedgerKind.RECHARGE,
        )
        points = select(
            CompanyPointLedgerEntry.id.label("id"),
            CompanyPointLedgerEntry.company_id.label("company_id"),
            literal(BillingUnit.POINT.value, type_=String(16)).label(
                "billing_unit"
            ),
            literal(2, type_=Integer()).label("billing_version"),
            case(
                (
                    CompanyPointLedgerEntry.kind == PointLedgerKind.MIGRATION,
                    literal(PointLedgerKind.MIGRATION.value),
                ),
                else_=literal(PointLedgerKind.CREDIT.value),
            ).label("kind"),
            literal(None, type_=BigInteger()).label("amount_cents"),
            literal(None, type_=BigInteger()).label("available_delta_cents"),
            literal(None, type_=BigInteger()).label("reserved_delta_cents"),
            CompanyPointLedgerEntry.amount_points.label("amount_points"),
            CompanyPointLedgerEntry.available_delta_points.label(
                "available_delta_points"
            ),
            CompanyPointLedgerEntry.reserved_delta_points.label(
                "reserved_delta_points"
            ),
            CompanyPointLedgerEntry.idempotency_key.label("idempotency_key"),
            CompanyPointLedgerEntry.task_id.label("task_id"),
            CompanyPointLedgerEntry.note.label("note"),
            CompanyPointLedgerEntry.created_at.label("created_at"),
        ).where(
            CompanyPointLedgerEntry.company_id == company_id,
            CompanyPointLedgerEntry.kind.in_(
                (PointLedgerKind.MIGRATION, PointLedgerKind.CREDIT)
            ),
        )
        if start_time is not None:
            legacy = legacy.where(LedgerEntry.created_at >= start_time)
            points = points.where(CompanyPointLedgerEntry.created_at >= start_time)
        if end_time is not None:
            legacy = legacy.where(LedgerEntry.created_at < end_time)
            points = points.where(CompanyPointLedgerEntry.created_at < end_time)

        filtered = legacy.union_all(points).subquery("filtered_funding")
        summary = session.execute(
            select(
                func.count().label("entry_count"),
                func.coalesce(func.sum(filtered.c.amount_cents), 0).label(
                    "total_amount_cents"
                ),
                func.coalesce(func.sum(filtered.c.amount_points), 0).label(
                    "total_amount_points"
                ),
            ).select_from(filtered)
        ).one()
        unit_versions = {
            (str(unit), int(version))
            for unit, version in session.execute(
                select(
                    filtered.c.billing_unit,
                    filtered.c.billing_version,
                ).distinct()
            ).all()
        }
        rows = [
            dict(row)
            for row in session.execute(
                select(filtered)
                .order_by(filtered.c.created_at.desc(), filtered.c.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).mappings()
        ]
        return (
            int(summary.entry_count or 0),
            int(summary.total_amount_cents or 0),
            int(summary.total_amount_points or 0),
            rows,
            unit_versions,
        )

    @classmethod
    def reserve(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        amount_cents: int | None = None,
        amount_points: int | None = None,
        idempotency_key: str,
    ) -> tuple[object, object]:
        billing_task = session.scalar(
            select(GenerationTask).where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
        )
        if billing_task is None:
            raise NotFoundError("当前公司下不存在该任务")
        if billing_task.billing_unit == BillingUnit.POINT:
            if amount_cents is not None:
                raise ConflictError("积分任务不能使用旧币种预占")
            resolved_points = (
                amount_points if amount_points is not None else billing_task.quote_points
            )
            if resolved_points is None:
                raise ConflictError("积分任务报价快照无效")
            return CompanyPointBillingService.reserve(
                session,
                company_id=company_id,
                task_id=task_id,
                amount_points=resolved_points,
                idempotency_key=idempotency_key,
            )
        if amount_points is not None:
            raise ConflictError("旧币种任务不能使用积分预占")
        if amount_cents is None:
            raise ConflictError("旧币种任务报价快照无效")
        if amount_cents <= 0 or amount_cents > MAX_MONEY_CENTS:
            raise ConflictError("预占金额必须大于 0 分")
        account = cls._locked_account(session, company_id)
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RESERVE,
            expected_amount=amount_cents,
            task_id=task_id,
        )
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
        if existing:
            return account, existing
        if task.status != TaskStatus.DRAFT or task.reserved_cents != 0:
            raise ConflictError("只有未预占的草稿任务可以预占额度")
        if task.quote_cents != amount_cents:
            raise ConflictError("预占金额必须等于任务报价")
        if account.available_cents < amount_cents:
            raise InsufficientBalanceError()

        account.available_cents -= amount_cents
        account.reserved_cents += amount_cents
        task.reserved_cents = amount_cents
        task.status = TaskStatus.QUEUED
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.RESERVE,
            amount_cents=amount_cents,
            available_delta_cents=-amount_cents,
            reserved_delta_cents=amount_cents,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        return account, entry

    @classmethod
    def settle_success(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        actual_cost_cents: int | None = None,
        actual_cost_points: int | None = None,
        idempotency_key: str,
        allow_terminal: bool = False,
    ) -> tuple[object, object]:
        billing_task = session.scalar(
            select(GenerationTask).where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
        )
        if billing_task is None:
            raise NotFoundError("当前公司下不存在该任务")
        if billing_task.billing_unit == BillingUnit.POINT:
            if actual_cost_cents is not None:
                raise ConflictError("积分任务不能使用旧币种结算")
            resolved_points = actual_cost_points
            if resolved_points is None:
                resolved_points = billing_task.quote_points
            if resolved_points is None:
                raise ConflictError("积分任务报价快照无效")
            return CompanyPointBillingService.settle_success(
                session,
                company_id=company_id,
                task_id=task_id,
                actual_cost_points=resolved_points,
                idempotency_key=idempotency_key,
                allow_terminal=allow_terminal,
            )
        if actual_cost_points is not None:
            raise ConflictError("旧币种任务不能使用积分结算")
        if actual_cost_cents is None:
            raise ConflictError("旧币种任务报价快照无效")
        if actual_cost_cents < 0 or actual_cost_cents > MAX_MONEY_CENTS:
            raise ConflictError("实际成本不能小于 0 分")
        account = cls._locked_account(session, company_id)
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.SETTLE,
            expected_amount=actual_cost_cents,
            task_id=task_id,
        )
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
        if existing:
            return account, existing
        if allow_terminal:
            if task.status != TaskStatus.SUCCEEDED:
                raise ConflictError("补偿结算只支持已经成功的任务")
        elif task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("任务当前状态不能成功结算")
        reserved = task.reserved_cents
        if reserved <= 0 or actual_cost_cents > reserved:
            raise ConflictError("实际成本必须在已预占额度范围内")

        refund = reserved - actual_cost_cents
        account.available_cents += refund
        account.reserved_cents -= reserved
        task.reserved_cents = 0
        task.actual_cost_cents = actual_cost_cents
        task.status = TaskStatus.SUCCEEDED
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.SETTLE,
            amount_cents=actual_cost_cents,
            available_delta_cents=refund,
            reserved_delta_cents=-reserved,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        return account, entry

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
    ) -> tuple[object, object]:
        if terminal_status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ConflictError("额度释放只支持失败或取消终态")
        billing_task = session.scalar(
            select(GenerationTask).where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
        )
        if billing_task is None:
            raise NotFoundError("当前公司下不存在该任务")
        if billing_task.billing_unit == BillingUnit.POINT:
            return CompanyPointBillingService.release_failure(
                session,
                company_id=company_id,
                task_id=task_id,
                idempotency_key=idempotency_key,
                failure_reason=failure_reason,
                terminal_status=terminal_status,
            )
        account = cls._locked_account(session, company_id)
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
        release_amount = task.reserved_cents
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RELEASE,
            expected_amount=task.quote_cents,
            task_id=task_id,
            expected_note=failure_reason[:240],
        )
        if existing:
            if task.status != terminal_status:
                raise ConflictError("额度释放幂等重放的任务终态不一致")
            return account, existing
        if allow_terminal:
            if task.status != terminal_status:
                raise ConflictError("补偿释放的任务终态与预期不一致")
        elif task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("任务当前状态不能释放额度")
        if release_amount <= 0:
            raise ConflictError("任务没有可释放的预占额度")

        account.available_cents += release_amount
        account.reserved_cents -= release_amount
        task.reserved_cents = 0
        task.status = terminal_status
        task.failure_reason = failure_reason
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.RELEASE,
            amount_cents=release_amount,
            available_delta_cents=release_amount,
            reserved_delta_cents=-release_amount,
            idempotency_key=idempotency_key,
            task_id=task_id,
            note=failure_reason[:240],
        )
        session.add(entry)
        session.flush()
        return account, entry
