from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AccountsReceivableLedgerEntry,
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyInvoice,
    CompanyInvoiceLine,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    EnterpriseBillingCycleStatus,
    EnterpriseContractStatus,
    EnterpriseDunningAction,
    EnterpriseDunningRun,
    EnterpriseDunningRunStatus,
    EnterpriseInvoiceStatus,
    GenerationTask,
    PaymentOrder,
    PaymentDispute,
    PaymentDisputeStatus,
    PaymentPurpose,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentTransaction,
    PaymentTransactionKind,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    PointLotSourceKind,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    new_id,
    utcnow,
)
from .errors import ConflictError, NotFoundError


POINT_VALUE_CENTS = 10
MAX_POINTS = 9_000_000_000_000_000
MAX_IDEMPOTENCY_KEY_LENGTH = 160
DUNNING_IDEMPOTENCY_KEY_LENGTH = 80
DUNNING_STAGE_OVERDUE = 1
DUNNING_HOLD_REASON = "delinquent_invoice"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConflictError(f"{field_name} must include a UTC offset")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata. Platform writes are normalized to UTC,
    # so a naive value read from SQLite is an encoded UTC timestamp.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _utc(value, field_name="timestamp").isoformat() if value else None


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ConflictError(f"{field_name} must not be blank")
    if normalized != value or len(normalized) > max_length:
        raise ConflictError(f"{field_name} is invalid")
    return normalized


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _validate_contract_cycle_period(
    contract: CompanyBillingContractVersion,
    *,
    period_start: datetime,
    period_end: datetime,
) -> None:
    """Bind a half-open cycle to one contract calendar month.

    The contract stores a local cycle *day*, not a wall-clock hour. Existing
    integrations may express the boundary at any stable offset-aware time, so
    the invariant is the local calendar day and the immediately following
    local month. This remains DST-safe without inventing an uncontracted
    midnight requirement.
    """

    try:
        timezone_info = ZoneInfo(contract.timezone_name)
    except ZoneInfoNotFoundError:
        raise ConflictError("企业月结合同账期时区无效") from None
    local_start = period_start.astimezone(timezone_info)
    local_end = period_end.astimezone(timezone_info)
    expected_year, expected_month = _next_month(local_start.year, local_start.month)
    if (
        local_start.day != contract.cycle_day
        or local_end.day != contract.cycle_day
        or (local_end.year, local_end.month) != (expected_year, expected_month)
    ):
        raise ConflictError("企业月结账期必须匹配合同 cycle_day 且覆盖一个自然月")


def _scheduled_next_period(
    contract: CompanyBillingContractVersion,
    *,
    latest_cycle: CompanyBillingCycle | None,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Choose a full contract month without changing an existing boundary hour."""

    if contract.status != EnterpriseContractStatus.ACTIVE:
        return None
    try:
        zone = ZoneInfo(contract.timezone_name)
    except ZoneInfoNotFoundError:
        raise ConflictError("企业月结合同账期时区无效") from None
    local_now = now.astimezone(zone)
    if latest_cycle is None:
        local_start = local_now.replace(
            day=contract.cycle_day, hour=0, minute=0, second=0, microsecond=0
        )
        if local_start > local_now:
            previous_year, previous_month = (
                (local_start.year - 1, 12)
                if local_start.month == 1
                else (local_start.year, local_start.month - 1)
            )
            local_start = local_start.replace(year=previous_year, month=previous_month)
    else:
        local_start = _stored_utc(latest_cycle.period_end).astimezone(zone)
        if local_start > local_now:
            return None
        if local_start.day != contract.cycle_day:
            raise ConflictError("新合同 cycle_day 与已有账期边界不一致，需要显式账期衔接")

    effective = _stored_utc(contract.effective_at)
    if local_start.astimezone(timezone.utc) < effective:
        # A mid-month activation never grants a retroactive partial month.
        local_effective = effective.astimezone(zone)
        local_start = local_start.replace(
            year=local_effective.year, month=local_effective.month
        )
        if local_start.astimezone(timezone.utc) < effective:
            year, month = _next_month(local_start.year, local_start.month)
            local_start = local_start.replace(year=year, month=month)
    if local_start > local_now:
        return None
    year, month = _next_month(local_start.year, local_start.month)
    local_end = local_start.replace(year=year, month=month)
    start, end = (
        local_start.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
    )
    if contract.expires_at is not None and _stored_utc(contract.expires_at) < end:
        return None
    _validate_contract_cycle_period(contract, period_start=start, period_end=end)
    return start, end


def _contract_content_sha256(
    *,
    company_id: str,
    contract_reference: str,
    currency: str,
    timezone_name: str,
    cycle_day: int,
    payment_terms_days: int,
    credit_limit_points: int,
    effective_at: datetime,
    expires_at: datetime | None,
    supersedes_version_id: str | None,
) -> str:
    canonical = json.dumps(
        {
            "schema_version": 1,
            "billing_contract_kind": "POSTPAID",
            "company_id": company_id,
            "contract_reference": contract_reference,
            "currency": currency,
            "timezone_name": timezone_name,
            "cycle_day": cycle_day,
            "payment_terms_days": payment_terms_days,
            "credit_limit_points": credit_limit_points,
            "receivable_per_point_cents": POINT_VALUE_CENTS,
            "effective_at": _iso(effective_at),
            "expires_at": _iso(expires_at),
            "supersedes_version_id": supersedes_version_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _contract_matches(
    contract: CompanyBillingContractVersion,
    *,
    contract_reference: str,
    currency: str,
    timezone_name: str,
    cycle_day: int,
    payment_terms_days: int,
    credit_limit_points: int,
    effective_at: datetime,
    expires_at: datetime | None,
) -> bool:
    return bool(
        contract.status == EnterpriseContractStatus.ACTIVE
        and contract.contract_reference == contract_reference
        and contract.currency == currency
        and contract.timezone_name == timezone_name
        and contract.cycle_day == cycle_day
        and contract.payment_terms_days == payment_terms_days
        and contract.credit_limit_points == credit_limit_points
        and contract.receivable_per_point_cents == POINT_VALUE_CENTS
        and _stored_utc(contract.effective_at) == effective_at
        and (
            (_stored_utc(contract.expires_at) if contract.expires_at else None)
            == expires_at
        )
    )


class EnterpriseBillingService:
    """Enterprise POSTPAID billing over the existing POINT/v2 task contract.

    PREPAID point purchases deliberately do not enter this service. They use
    payment orders and purchased lots. A contract lot issued here represents
    authorised receivable capacity, never captured cash.
    """

    @staticmethod
    def unbilled_receivable_exceptions(
        session: Session,
        *,
        company_id: str,
        as_of: datetime,
    ) -> list[dict[str, object]]:
        """Identify settled credit that no existing invoiceable cycle can collect.

        A late in-flight task keeps its original settlement and receivable.
        Final/expired contracts may have no following period, and an issued
        invoice cannot be amended. The finance reconciliation run persists
        these exact value-level facts as unresolved exceptions for collection
        handling; this read does not backdate settlement or mint a new cycle.
        """
        cutoff = _utc(as_of, field_name="as_of")
        rows = session.execute(
            select(PointLotSettlementValueAllocation, CompanyPointLedgerEntry, CompanyPointLot)
            .join(
                CompanyPointLedgerEntry,
                CompanyPointLedgerEntry.id
                == PointLotSettlementValueAllocation.company_settle_ledger_id,
            )
            .join(
                TaskPointLotAllocation,
                TaskPointLotAllocation.id
                == PointLotSettlementValueAllocation.company_task_allocation_id,
            )
            .join(CompanyPointLot, CompanyPointLot.id == TaskPointLotAllocation.lot_id)
            .where(
                PointLotSettlementValueAllocation.company_id == company_id,
                PointLotSettlementValueAllocation.receivable_basis_cents > 0,
                CompanyPointLedgerEntry.company_id == company_id,
                CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE,
                CompanyPointLedgerEntry.created_at < cutoff,
                ~select(CompanyInvoiceLine.id).where(
                    CompanyInvoiceLine.value_allocation_id
                    == PointLotSettlementValueAllocation.id,
                ).exists(),
            )
            .order_by(CompanyPointLedgerEntry.created_at, PointLotSettlementValueAllocation.id)
        ).all()
        if not rows:
            return []
        cycle_rows = session.execute(
            select(CompanyBillingCycle, CompanyInvoice.id)
            .outerjoin(CompanyInvoice, CompanyInvoice.cycle_id == CompanyBillingCycle.id)
            .where(CompanyBillingCycle.company_id == company_id)
            .order_by(CompanyBillingCycle.period_start, CompanyBillingCycle.id)
        ).all()
        exceptions = []
        for value, settle, lot in rows:
            settled_at = _stored_utc(settle.created_at)
            covering = [
                (cycle, invoice_id) for cycle, invoice_id in cycle_rows
                if _stored_utc(cycle.period_start) <= settled_at < _stored_utc(cycle.period_end)
            ]
            if any(
                invoice_id is None and cycle.status in {
                    EnterpriseBillingCycleStatus.OPEN, EnterpriseBillingCycleStatus.FROZEN,
                }
                for cycle, invoice_id in covering
            ):
                continue
            exceptions.append({
                "company_id": company_id,
                "task_id": value.task_id,
                "value_allocation_id": value.id,
                "settle_ledger_id": settle.id,
                "contract_version_id": lot.contract_version_id,
                "source_cycle_id": lot.billing_cycle_id,
                "covering_cycle_ids": [cycle.id for cycle, _ in covering],
                "receivable_cents": value.receivable_basis_cents,
                "settled_at": settled_at.isoformat(),
                "reason": (
                    "no_covering_billing_cycle"
                    if not covering else "covering_cycle_already_closed"
                ),
            })
        return exceptions

    @staticmethod
    def _locked_point_company(session: Session, company_id: str) -> Company:
        company = session.scalar(
            select(Company)
            .where(Company.id == company_id)
            # Serialize company-state transitions without blocking the FK
            # KEY SHARE taken by concurrent prepaid ledger inserts.  UPDATE
            # here would invert company->wallet against payment wallet->FK.
            .with_for_update(key_share=True)
        )
        if company is None:
            raise NotFoundError("公司不存在")
        if company.billing_version != 2 or company.billing_unit != BillingUnit.POINT:
            raise ConflictError("企业月结只支持 POINT/v2 企业")
        if session.get(CompanyPointWalletAccount, company_id) is None:
            raise ConflictError("企业 POINT/v2 钱包状态不完整")
        return company

    @staticmethod
    def _locked_billing_account(
        session: Session, company_id: str
    ) -> CompanyBillingAccount:
        account = session.scalar(
            select(CompanyBillingAccount)
            .where(CompanyBillingAccount.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise NotFoundError("企业月结账户不存在")
        return account

    @staticmethod
    def _refresh_billing_hold(
        session: Session,
        *,
        account: CompanyBillingAccount,
        as_of: datetime | None = None,
    ) -> bool:
        # Several test/session configurations intentionally disable autoflush.
        # Persist the locked invoice projection before deriving the account hold.
        session.flush()
        risk_time = _stored_utc(as_of if as_of is not None else utcnow())
        delinquent = session.scalar(
            select(CompanyInvoice.id)
            .where(
                CompanyInvoice.company_id == account.company_id,
                or_(
                    CompanyInvoice.status.in_(
                        {
                            EnterpriseInvoiceStatus.OVERDUE,
                            EnterpriseInvoiceStatus.DISPUTED,
                        }
                    ),
                    and_(
                        CompanyInvoice.status.in_(
                            {
                                EnterpriseInvoiceStatus.ISSUED,
                                EnterpriseInvoiceStatus.PARTIALLY_PAID,
                            }
                        ),
                        CompanyInvoice.due_at <= risk_time,
                    ),
                ),
                CompanyInvoice.paid_cents < CompanyInvoice.total_cents,
            )
            .order_by(CompanyInvoice.due_at, CompanyInvoice.id)
            .limit(1)
        )
        should_hold = delinquent is not None
        if should_hold:
            changed = not account.billing_hold
            if changed:
                account.billing_hold = True
                account.billing_hold_since = risk_time
                account.billing_hold_reason = DUNNING_HOLD_REASON
            account.dunning_level = max(account.dunning_level, DUNNING_STAGE_OVERDUE)
            return changed
        if account.billing_hold_reason == DUNNING_HOLD_REASON:
            account.billing_hold = False
            account.billing_hold_since = None
            account.billing_hold_reason = None
            account.dunning_level = 0
            return True
        if account.billing_hold:
            # Preserve a hold owned by another risk/control workflow while
            # clearing only this service's resolved dunning stage.
            account.dunning_level = 0
            return False
        if not account.billing_hold:
            account.billing_hold_since = None
            account.billing_hold_reason = None
            account.dunning_level = 0
        return False

    @staticmethod
    def _refresh_invoice_collection_status(
        session: Session,
        *,
        invoice: CompanyInvoice,
        cycle: CompanyBillingCycle,
        as_of: datetime,
    ) -> None:
        """Derive present collection risk without replaying the PSP's clock.

        occurred_at remains immutable payment evidence. A late capture, refund
        or dispute win cannot move an unpaid invoice back before its due date,
        or erase a delinquency already established by a completed dunning run.
        """
        previously_overdue = invoice.status == EnterpriseInvoiceStatus.OVERDUE
        session.flush()
        if invoice.paid_cents == invoice.total_cents:
            invoice.status = EnterpriseInvoiceStatus.PAID
            cycle.status = EnterpriseBillingCycleStatus.PAID
            return
        dispute_entries = session.execute(
            select(
                AccountsReceivableLedgerEntry.debit_cents,
                AccountsReceivableLedgerEntry.credit_cents,
            ).where(
                AccountsReceivableLedgerEntry.invoice_id == invoice.id,
                AccountsReceivableLedgerEntry.kind.in_(
                    {
                        "INVOICE_PAYMENT_CHARGEBACK",
                        "INVOICE_PAYMENT_DISPUTE_REVERSAL",
                    }
                ),
            )
        ).all()
        if sum(debit - credit for debit, credit in dispute_entries) > 0:
            invoice.status = EnterpriseInvoiceStatus.DISPUTED
            cycle.status = EnterpriseBillingCycleStatus.DISPUTED
        elif (
            previously_overdue
            or _stored_utc(invoice.due_at) <= as_of
            or session.scalar(
                select(EnterpriseDunningAction.id).where(
                    EnterpriseDunningAction.invoice_id == invoice.id,
                    EnterpriseDunningAction.action == "mark_overdue",
                ).limit(1)
            ) is not None
        ):
            invoice.status = EnterpriseInvoiceStatus.OVERDUE
            cycle.status = EnterpriseBillingCycleStatus.OVERDUE
        else:
            invoice.status = (
                EnterpriseInvoiceStatus.PARTIALLY_PAID
                if invoice.paid_cents
                else EnterpriseInvoiceStatus.ISSUED
            )
            cycle.status = EnterpriseBillingCycleStatus.ISSUED

    @staticmethod
    def _validate_payment_transaction_lineage(
        session: Session,
        *,
        transaction: PaymentTransaction,
        order: PaymentOrder,
    ) -> None:
        if order.points != 0 or order.amount_cents <= 0:
            raise ConflictError("企业发票支付订单不得购买积分")
        if transaction.kind == PaymentTransactionKind.CAPTURE:
            if transaction.refund_id is not None or transaction.dispute_id is not None:
                raise ConflictError("收款交易错误绑定退款或争议")
            return
        if transaction.kind == PaymentTransactionKind.REFUND:
            refund = (
                session.get(PaymentRefund, transaction.refund_id)
                if transaction.refund_id is not None
                else None
            )
            if (
                refund is None
                or transaction.dispute_id is not None
                or refund.order_id != order.id
                or refund.provider != order.provider
                or refund.status != PaymentRefundStatus.SUCCEEDED
                or refund.amount_cents != transaction.amount_cents
                or refund.currency != transaction.currency
                or refund.points != 0
                or refund.completed_at is None
                or _stored_utc(refund.completed_at)
                > _stored_utc(transaction.occurred_at)
            ):
                raise ConflictError("退款交易缺少已成功的原始退款事实")
            return
        if transaction.kind in {
            PaymentTransactionKind.CHARGEBACK,
            PaymentTransactionKind.DISPUTE_REVERSAL,
        }:
            dispute = (
                session.get(PaymentDispute, transaction.dispute_id)
                if transaction.dispute_id is not None
                else None
            )
            if (
                dispute is None
                or transaction.refund_id is not None
                or dispute.order_id != order.id
                or dispute.provider != order.provider
                or dispute.amount_cents != transaction.amount_cents
                or dispute.currency != transaction.currency
                or dispute.points != 0
                or _stored_utc(dispute.opened_at)
                > _stored_utc(transaction.occurred_at)
            ):
                raise ConflictError("拒付交易缺少原始争议事实")
            if transaction.kind == PaymentTransactionKind.DISPUTE_REVERSAL and (
                dispute.status != PaymentDisputeStatus.WON
                or dispute.closed_at is None
                or _stored_utc(dispute.closed_at)
                > _stored_utc(transaction.occurred_at)
            ):
                raise ConflictError("争议胜诉交易缺少已关闭的胜诉事实")
            return
        raise ConflictError("企业发票不接受该支付交易类型")

    @classmethod
    def _validate_invoice_projection(
        cls,
        session: Session,
        *,
        invoice: CompanyInvoice,
        cycle: CompanyBillingCycle,
    ) -> list[CompanyInvoiceLine]:
        """Fail closed unless invoice, cycle, lines and AR form one state."""

        if invoice.company_id != cycle.company_id or invoice.cycle_id != cycle.id:
            raise ConflictError("企业发票账期作用域不一致")
        if invoice.currency != "CNY":
            raise ConflictError("企业发票币种必须为 CNY")
        if (
            invoice.subtotal_cents < 0
            or invoice.credit_cents < 0
            or invoice.tax_cents < 0
            or invoice.total_cents < 0
            or invoice.paid_cents < 0
            or invoice.total_cents
            != invoice.subtotal_cents - invoice.credit_cents + invoice.tax_cents
            or invoice.paid_cents > invoice.total_cents
        ):
            raise ConflictError("企业发票金额投影无效")
        if invoice.status in {
            EnterpriseInvoiceStatus.DRAFT,
            EnterpriseInvoiceStatus.VOID,
        }:
            raise ConflictError("企业发票尚未形成可核销的已开票事实")
        if invoice.issued_at is None or invoice.due_at is None:
            raise ConflictError("企业发票缺少开票或到期时间")
        if _stored_utc(invoice.issued_at) < _stored_utc(cycle.period_end):
            raise ConflictError("企业发票早于账期结束时间")
        if _stored_utc(invoice.due_at) < _stored_utc(invoice.issued_at):
            raise ConflictError("企业发票到期时间早于开票时间")

        lines = list(
            session.scalars(
                select(CompanyInvoiceLine)
                .where(CompanyInvoiceLine.invoice_id == invoice.id)
                .order_by(CompanyInvoiceLine.created_at, CompanyInvoiceLine.id)
            ).all()
        )
        if sum(line.amount_cents for line in lines) != invoice.subtotal_cents:
            raise ConflictError("企业发票与行项目无法对账")

        ar_entries = list(
            session.scalars(
                select(AccountsReceivableLedgerEntry)
                .where(AccountsReceivableLedgerEntry.invoice_id == invoice.id)
                .order_by(
                    AccountsReceivableLedgerEntry.created_at,
                    AccountsReceivableLedgerEntry.id,
                )
            ).all()
        )
        issue_entries = [
            entry for entry in ar_entries if entry.kind == "INVOICE_ISSUED"
        ]
        if invoice.total_cents:
            if (
                len(issue_entries) != 1
                or issue_entries[0].company_id != invoice.company_id
                or issue_entries[0].payment_transaction_id is not None
                or issue_entries[0].debit_cents != invoice.total_cents
                or issue_entries[0].credit_cents != 0
            ):
                raise ConflictError("企业发票与应收借方无法对账")
        elif issue_entries:
            raise ConflictError("零金额企业发票存在应收借方")

        transaction_kinds = {
            "INVOICE_PAYMENT_CAPTURE": PaymentTransactionKind.CAPTURE,
            "INVOICE_PAYMENT_DISPUTE_REVERSAL": PaymentTransactionKind.DISPUTE_REVERSAL,
            "INVOICE_PAYMENT_REFUND": PaymentTransactionKind.REFUND,
            "INVOICE_PAYMENT_CHARGEBACK": PaymentTransactionKind.CHARGEBACK,
        }
        seen_transactions: set[str] = set()
        credits = 0
        reversals = 0
        for entry in ar_entries:
            if entry.kind == "INVOICE_ISSUED":
                continue
            expected_transaction_kind = transaction_kinds.get(entry.kind)
            if expected_transaction_kind is None or entry.payment_transaction_id is None:
                raise ConflictError("企业发票包含未知或无支付事实的应收分录")
            if entry.payment_transaction_id in seen_transactions:
                raise ConflictError("同一支付交易重复作用于企业发票")
            seen_transactions.add(entry.payment_transaction_id)
            transaction = session.get(
                PaymentTransaction,
                entry.payment_transaction_id,
            )
            order = (
                session.get(PaymentOrder, transaction.order_id)
                if transaction is not None
                else None
            )
            if (
                transaction is None
                or order is None
                or transaction.kind != expected_transaction_kind
                or transaction.amount_cents <= 0
                or transaction.amount_cents > order.amount_cents
                or _stored_utc(transaction.occurred_at)
                < _stored_utc(invoice.issued_at)
                or transaction.currency != invoice.currency
                or transaction.provider != order.provider
                or order.purpose != PaymentPurpose.INVOICE_PAYMENT
                or order.purpose_reference_id != invoice.id
                or order.company_id != invoice.company_id
                or order.personal_workspace_id is not None
                or order.currency != invoice.currency
                or order.amount_cents <= 0
                or order.points != 0
                or entry.company_id != invoice.company_id
                or (
                    expected_transaction_kind == PaymentTransactionKind.CAPTURE
                    and transaction.amount_cents != order.amount_cents
                )
            ):
                raise ConflictError("企业发票应收分录与支付事实不一致")
            cls._validate_payment_transaction_lineage(
                session,
                transaction=transaction,
                order=order,
            )
            if expected_transaction_kind in {
                PaymentTransactionKind.CAPTURE,
                PaymentTransactionKind.DISPUTE_REVERSAL,
            }:
                if (
                    entry.debit_cents != 0
                    or entry.credit_cents != transaction.amount_cents
                ):
                    raise ConflictError("企业发票应收贷方与支付事实不一致")
                credits += entry.credit_cents
            else:
                if (
                    entry.credit_cents != 0
                    or entry.debit_cents != transaction.amount_cents
                ):
                    raise ConflictError("企业发票应收借方与支付事实不一致")
                reversals += entry.debit_cents
        if invoice.paid_cents != credits - reversals:
            raise ConflictError("企业发票已付投影与应收账本无法对账")

        expected_cycle_status: EnterpriseBillingCycleStatus
        if invoice.status == EnterpriseInvoiceStatus.PAID:
            if invoice.paid_cents != invoice.total_cents:
                raise ConflictError("企业发票已付状态与金额不一致")
            expected_cycle_status = EnterpriseBillingCycleStatus.PAID
        elif invoice.status == EnterpriseInvoiceStatus.ISSUED:
            if invoice.paid_cents != 0 or invoice.total_cents <= 0:
                raise ConflictError("企业发票已开票状态与金额不一致")
            expected_cycle_status = EnterpriseBillingCycleStatus.ISSUED
        elif invoice.status == EnterpriseInvoiceStatus.PARTIALLY_PAID:
            if not 0 < invoice.paid_cents < invoice.total_cents:
                raise ConflictError("企业发票部分付款状态与金额不一致")
            expected_cycle_status = EnterpriseBillingCycleStatus.ISSUED
        elif invoice.status == EnterpriseInvoiceStatus.OVERDUE:
            if invoice.paid_cents >= invoice.total_cents:
                raise ConflictError("企业发票逾期状态与金额不一致")
            expected_cycle_status = EnterpriseBillingCycleStatus.OVERDUE
        elif invoice.status == EnterpriseInvoiceStatus.DISPUTED:
            if invoice.paid_cents >= invoice.total_cents:
                raise ConflictError("企业发票争议状态与金额不一致")
            expected_cycle_status = EnterpriseBillingCycleStatus.DISPUTED
        else:  # pragma: no cover - enum exhaustiveness.
            raise ConflictError("企业发票状态无效")
        if cycle.status != expected_cycle_status:
            raise ConflictError("企业发票与账期状态不一致")
        return lines

    @classmethod
    def activate_contract(
        cls,
        session: Session,
        *,
        company_id: str,
        contract_reference: str,
        currency: str,
        timezone_name: str,
        cycle_day: int,
        payment_terms_days: int,
        credit_limit_points: int,
        effective_at: datetime,
        created_by_user_id: str,
        expires_at: datetime | None = None,
    ) -> tuple[CompanyBillingContractVersion, CompanyBillingAccount, bool]:
        """Activate one immutable POSTPAID contract version idempotently.

        The active pointer is the mutable projection. Prior versions remain
        immutable and the new version records the exact predecessor edge.
        """

        cls._locked_point_company(session, company_id)
        reference = _required_text(
            contract_reference,
            field_name="contract_reference",
            max_length=160,
        )
        if currency != "CNY":
            raise ConflictError("企业月结合同只支持 CNY")
        normalized_timezone = _required_text(
            timezone_name,
            field_name="timezone_name",
            max_length=80,
        )
        try:
            ZoneInfo(normalized_timezone)
        except ZoneInfoNotFoundError:
            raise ConflictError("timezone_name is invalid") from None
        if isinstance(cycle_day, bool) or not 1 <= cycle_day <= 28:
            raise ConflictError("cycle_day must be between 1 and 28")
        if (
            isinstance(payment_terms_days, bool)
            or not 0 <= payment_terms_days <= 180
        ):
            raise ConflictError("payment_terms_days must be between 0 and 180")
        if (
            isinstance(credit_limit_points, bool)
            or credit_limit_points <= 0
            or credit_limit_points > MAX_POINTS
        ):
            raise ConflictError("credit_limit_points is invalid")
        effective = _utc(effective_at, field_name="effective_at")
        expiry = (
            _utc(expires_at, field_name="expires_at")
            if expires_at is not None
            else None
        )
        if expiry is not None and expiry <= effective:
            raise ConflictError("expires_at must be after effective_at")
        if session.get(User, created_by_user_id) is None:
            raise NotFoundError("合同创建人不存在")

        account = session.scalar(
            select(CompanyBillingAccount)
            .where(CompanyBillingAccount.company_id == company_id)
            .with_for_update()
        )
        current = (
            session.get(
                CompanyBillingContractVersion,
                account.active_contract_version_id,
            )
            if account is not None
            else None
        )
        if current is not None and _contract_matches(
            current,
            contract_reference=reference,
            currency=currency,
            timezone_name=normalized_timezone,
            cycle_day=cycle_day,
            payment_terms_days=payment_terms_days,
            credit_limit_points=credit_limit_points,
            effective_at=effective,
            expires_at=expiry,
        ):
            return current, account, False

        supersedes_id = current.id if current is not None else None
        content_sha256 = _contract_content_sha256(
            company_id=company_id,
            contract_reference=reference,
            currency=currency,
            timezone_name=normalized_timezone,
            cycle_day=cycle_day,
            payment_terms_days=payment_terms_days,
            credit_limit_points=credit_limit_points,
            effective_at=effective,
            expires_at=expiry,
            supersedes_version_id=supersedes_id,
        )
        contract = session.scalar(
            select(CompanyBillingContractVersion).where(
                CompanyBillingContractVersion.content_sha256 == content_sha256
            )
        )
        created = contract is None
        if contract is None:
            contract = CompanyBillingContractVersion(
                id=new_id(),
                company_id=company_id,
                status=EnterpriseContractStatus.ACTIVE,
                contract_reference=reference,
                currency=currency,
                timezone_name=normalized_timezone,
                cycle_day=cycle_day,
                payment_terms_days=payment_terms_days,
                credit_limit_points=credit_limit_points,
                receivable_per_point_cents=POINT_VALUE_CENTS,
                content_sha256=content_sha256,
                supersedes_version_id=supersedes_id,
                effective_at=effective,
                expires_at=expiry,
                created_by_user_id=created_by_user_id,
            )
            session.add(contract)
            session.flush()
        elif (
            contract.company_id != company_id
            or contract.supersedes_version_id != supersedes_id
            or not _contract_matches(
                contract,
                contract_reference=reference,
                currency=currency,
                timezone_name=normalized_timezone,
                cycle_day=cycle_day,
                payment_terms_days=payment_terms_days,
                credit_limit_points=credit_limit_points,
                effective_at=effective,
                expires_at=expiry,
            )
        ):
            raise ConflictError("企业月结合同内容摘要冲突")

        if account is None:
            account = CompanyBillingAccount(
                company_id=company_id,
                active_contract_version_id=contract.id,
                unbilled_receivable_cents=0,
                billing_hold=False,
            )
            session.add(account)
        else:
            account.active_contract_version_id = contract.id
        session.flush()
        return contract, account, created

    @staticmethod
    def _outstanding_receivable_cents(session: Session, company_id: str) -> int:
        debit, credit = session.execute(
            select(
                func.coalesce(func.sum(AccountsReceivableLedgerEntry.debit_cents), 0),
                func.coalesce(func.sum(AccountsReceivableLedgerEntry.credit_cents), 0),
            ).where(AccountsReceivableLedgerEntry.company_id == company_id)
        ).one()
        outstanding = int(debit or 0) - int(credit or 0)
        if outstanding < 0:
            raise ConflictError("企业应收账本出现负余额")
        return outstanding

    @classmethod
    def open_cycle(
        cls,
        session: Session,
        *,
        company_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> tuple[CompanyBillingCycle, CompanyPointLot | None, bool]:
        """Open a POSTPAID period and top receivable credit up to its limit."""

        cls._locked_point_company(session, company_id)
        start = _utc(period_start, field_name="period_start")
        end = _utc(period_end, field_name="period_end")
        if end <= start:
            raise ConflictError("period_end must be after period_start")

        account = cls._locked_billing_account(session, company_id)
        overlaps = list(
            session.scalars(
                select(CompanyBillingCycle)
                .where(
                    CompanyBillingCycle.company_id == company_id,
                    CompanyBillingCycle.period_start < end,
                    CompanyBillingCycle.period_end > start,
                )
                .order_by(CompanyBillingCycle.period_start, CompanyBillingCycle.id)
                .with_for_update()
            ).all()
        )
        exact = [
            cycle
            for cycle in overlaps
            if _stored_utc(cycle.period_start) == start
            and _stored_utc(cycle.period_end) == end
        ]
        if overlaps:
            if len(overlaps) != 1 or len(exact) != 1:
                raise ConflictError("企业月结账期与已有账期重叠")
            existing = exact[0]
            existing_contract = session.get(
                CompanyBillingContractVersion, existing.contract_version_id
            )
            if existing_contract is None or existing_contract.company_id != company_id:
                raise ConflictError("已有企业月结账期合同无效")
            _validate_contract_cycle_period(
                existing_contract,
                period_start=start,
                period_end=end,
            )
            lots = list(
                session.scalars(
                    select(CompanyPointLot)
                    .where(
                        CompanyPointLot.company_id == company_id,
                        CompanyPointLot.billing_cycle_id == existing.id,
                        CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
                    )
                    .order_by(CompanyPointLot.created_at, CompanyPointLot.id)
                ).all()
            )
            if len(lots) > 1 or (
                lots
                and (
                    lots[0].contract_version_id != existing.contract_version_id
                    or lots[0].billing_cycle_id != existing.id
                )
            ):
                raise ConflictError("已有企业月结账期授信批次不一致")
            return existing, lots[0] if lots else None, False

        if account.billing_hold:
            raise ConflictError("企业月结账户处于暂停状态")
        contract = session.scalar(
            select(CompanyBillingContractVersion)
            .where(
                CompanyBillingContractVersion.id
                == account.active_contract_version_id
            )
            .with_for_update()
        )
        if contract is None or contract.status != EnterpriseContractStatus.ACTIVE:
            raise ConflictError("企业没有有效的 POSTPAID 月结合同")
        if _stored_utc(contract.effective_at) > start:
            raise ConflictError("月结合同在账期开始时尚未生效")
        if contract.expires_at is not None and _stored_utc(contract.expires_at) < end:
            raise ConflictError("月结合同不能覆盖完整账期")
        _validate_contract_cycle_period(
            contract,
            period_start=start,
            period_end=end,
        )

        cycle = CompanyBillingCycle(
            id=new_id(),
            company_id=company_id,
            contract_version_id=contract.id,
            period_start=start,
            period_end=end,
            status=EnterpriseBillingCycleStatus.OPEN,
            frozen_at=None,
        )
        session.add(cycle)
        session.flush()

        wallet = session.scalar(
            select(CompanyPointWalletAccount)
            .where(CompanyPointWalletAccount.company_id == company_id)
            .with_for_update()
        )
        if wallet is None:
            raise ConflictError("企业 POINT/v2 钱包状态不完整")
        contract_capacity = int(
            session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            CompanyPointLot.available_points
                            + CompanyPointLot.reserved_points
                            + CompanyPointLot.reversal_reserved_points
                        ),
                        0,
                    )
                ).where(
                    CompanyPointLot.company_id == company_id,
                    CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
                )
            )
            or 0
        )
        receivable_exposure_cents = (
            account.unbilled_receivable_cents
            + cls._outstanding_receivable_cents(session, company_id)
        )
        if receivable_exposure_cents % POINT_VALUE_CENTS:
            raise ConflictError("企业应收余额无法按积分锚点对账")
        exposure_points = contract_capacity + (
            receivable_exposure_cents // POINT_VALUE_CENTS
        )
        if exposure_points > contract.credit_limit_points:
            raise ConflictError("企业月结信用额度已超限")
        top_up_points = contract.credit_limit_points - exposure_points
        if top_up_points == 0:
            return cycle, None, True
        if wallet.available_points > MAX_POINTS - top_up_points:
            raise ConflictError("月结授信后企业积分余额超出系统上限")

        lot_key = f"enterprise-cycle-credit:{cycle.id}"
        lot = CompanyPointLot(
            company_id=company_id,
            source_kind=PointLotSourceKind.CONTRACT,
            original_points=top_up_points,
            available_points=top_up_points,
            reserved_points=0,
            reversal_reserved_points=0,
            settled_points=0,
            reversed_points=0,
            cash_basis_cents=0,
            receivable_basis_cents=top_up_points * POINT_VALUE_CENTS,
            subsidy_cents=0,
            idempotency_key=lot_key,
            expires_at=None,
            payment_order_id=None,
            contract_version_id=contract.id,
            billing_cycle_id=cycle.id,
        )
        session.add(lot)
        wallet.available_points += top_up_points
        session.add(
            CompanyPointLedgerEntry(
                company_id=company_id,
                kind=PointLedgerKind.CREDIT,
                amount_points=top_up_points,
                available_delta_points=top_up_points,
                reserved_delta_points=0,
                reversal_reserved_delta_points=0,
                debt_delta_points=0,
                idempotency_key=lot_key,
                task_id=None,
                payment_order_id=None,
                payment_refund_id=None,
                payment_dispute_id=None,
                note=f"POSTPAID contract credit for billing cycle {cycle.id}",
            )
        )
        session.flush()
        return cycle, lot, True

    @classmethod
    def _existing_invoice(
        cls, session: Session, cycle: CompanyBillingCycle
    ) -> tuple[CompanyInvoice, list[CompanyInvoiceLine]] | None:
        invoice = session.scalar(
            select(CompanyInvoice)
            .where(CompanyInvoice.cycle_id == cycle.id)
            .with_for_update()
        )
        if invoice is None:
            return None
        lines = cls._validate_invoice_projection(
            session,
            invoice=invoice,
            cycle=cycle,
        )
        return invoice, lines

    @classmethod
    def _locked_invoice_context(
        cls,
        session: Session,
        *,
        invoice_id: str,
    ) -> tuple[CompanyBillingAccount, CompanyBillingCycle, CompanyInvoice]:
        """Acquire every enterprise mutation lock in one canonical order."""

        # Probe only immutable routing columns. An ORM entity read before
        # waiting on the company lock can retain a stale paid/overdue state.
        probe = session.execute(
            select(CompanyInvoice.company_id, CompanyInvoice.cycle_id).where(
                CompanyInvoice.id == invoice_id
            )
        ).one_or_none()
        if probe is None:
            raise NotFoundError("企业发票不存在")
        cls._locked_point_company(session, probe.company_id)
        account = cls._locked_billing_account(session, probe.company_id)
        cycle = session.scalar(
            select(CompanyBillingCycle)
            .where(
                CompanyBillingCycle.id == probe.cycle_id,
                CompanyBillingCycle.company_id == probe.company_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cycle is None:
            raise ConflictError("企业发票账期作用域不一致")
        invoice = session.scalar(
            select(CompanyInvoice)
            .where(CompanyInvoice.id == invoice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            invoice is None
            or invoice.company_id != probe.company_id
            or invoice.cycle_id != cycle.id
        ):
            raise ConflictError("企业发票作用域在锁定期间发生变化")
        cls._validate_invoice_projection(session, invoice=invoice, cycle=cycle)
        return account, cycle, invoice

    @staticmethod
    def _order_receivable_totals(
        session: Session,
        *,
        invoice_id: str,
        order_id: str,
    ) -> dict[str, int]:
        totals = {
            "INVOICE_PAYMENT_CAPTURE": 0,
            "INVOICE_PAYMENT_DISPUTE_REVERSAL": 0,
            "INVOICE_PAYMENT_REFUND": 0,
            "INVOICE_PAYMENT_CHARGEBACK": 0,
        }
        rows = session.execute(
            select(
                AccountsReceivableLedgerEntry.kind,
                AccountsReceivableLedgerEntry.debit_cents,
                AccountsReceivableLedgerEntry.credit_cents,
            )
            .join(
                PaymentTransaction,
                PaymentTransaction.id
                == AccountsReceivableLedgerEntry.payment_transaction_id,
            )
            .where(
                AccountsReceivableLedgerEntry.invoice_id == invoice_id,
                PaymentTransaction.order_id == order_id,
                AccountsReceivableLedgerEntry.kind.in_(set(totals)),
            )
        ).all()
        for kind, debit_cents, credit_cents in rows:
            if kind in {
                "INVOICE_PAYMENT_CAPTURE",
                "INVOICE_PAYMENT_DISPUTE_REVERSAL",
            }:
                totals[kind] += int(credit_cents)
            else:
                totals[kind] += int(debit_cents)
        return totals

    @classmethod
    def close_and_issue_cycle(
        cls,
        session: Session,
        *,
        company_id: str,
        cycle_id: str,
        issued_at: datetime | None = None,
    ) -> tuple[CompanyBillingCycle, CompanyInvoice, list[CompanyInvoiceLine], bool]:
        """Freeze a half-open period and invoice immutable receivable allocations."""

        cls._locked_point_company(session, company_id)
        account = cls._locked_billing_account(session, company_id)
        cycle = session.scalar(
            select(CompanyBillingCycle)
            .where(
                CompanyBillingCycle.id == cycle_id,
                CompanyBillingCycle.company_id == company_id,
            )
            .with_for_update()
        )
        if cycle is None:
            raise NotFoundError("企业月结账期不存在")
        existing = cls._existing_invoice(session, cycle)
        if existing is not None:
            invoice, lines = existing
            return cycle, invoice, lines, False
        if cycle.status not in {
            EnterpriseBillingCycleStatus.OPEN,
            EnterpriseBillingCycleStatus.FROZEN,
        }:
            raise ConflictError("企业月结账期当前状态不能开票")

        contract = session.get(
            CompanyBillingContractVersion, cycle.contract_version_id
        )
        if contract is None or contract.company_id != company_id:
            raise ConflictError("企业月结账期合同不存在或作用域不一致")
        issue_time = _utc(
            issued_at if issued_at is not None else utcnow(),
            field_name="issued_at",
        )
        period_start = _stored_utc(cycle.period_start)
        period_end = _stored_utc(cycle.period_end)
        _validate_contract_cycle_period(
            contract,
            period_start=period_start,
            period_end=period_end,
        )
        if issue_time < period_end:
            raise ConflictError("企业月结账期结束前禁止开票")
        if cycle.frozen_at is not None and _stored_utc(cycle.frozen_at) > issue_time:
            raise ConflictError("企业月结账期冻结时间晚于开票时间")

        settle_entries = list(
            session.scalars(
                select(CompanyPointLedgerEntry)
                .where(
                    CompanyPointLedgerEntry.company_id == company_id,
                    CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE,
                    CompanyPointLedgerEntry.created_at >= period_start,
                    CompanyPointLedgerEntry.created_at < period_end,
                )
                .order_by(
                    CompanyPointLedgerEntry.created_at,
                    CompanyPointLedgerEntry.id,
                )
                .with_for_update()
            ).all()
        )
        settle_ids = [entry.id for entry in settle_entries]
        values = (
            list(
                session.scalars(
                    select(PointLotSettlementValueAllocation)
                    .where(
                        PointLotSettlementValueAllocation.company_id == company_id,
                        PointLotSettlementValueAllocation.company_settle_ledger_id.in_(
                            settle_ids
                        ),
                    )
                    .order_by(
                        PointLotSettlementValueAllocation.created_at,
                        PointLotSettlementValueAllocation.id,
                    )
                ).all()
            )
            if settle_ids
            else []
        )
        values_by_settle: dict[str, list[PointLotSettlementValueAllocation]] = {}
        for value in values:
            assert value.company_settle_ledger_id is not None
            values_by_settle.setdefault(value.company_settle_ledger_id, []).append(value)

        invoice_id = new_id()
        invoice_number = f"INV-{period_end:%Y%m%d}-{cycle.id}"
        lines: list[CompanyInvoiceLine] = []
        subtotal = 0
        for settle in settle_entries:
            if settle.task_id is None:
                raise ConflictError("企业积分结算分录缺少任务")
            task = session.get(GenerationTask, settle.task_id)
            if (
                task is None
                or task.company_id != company_id
                or task.personal_workspace_id is not None
                or task.billing_unit != BillingUnit.POINT
                or task.billing_version != 2
                or task.status != TaskStatus.SUCCEEDED
                or task.actual_cost_points != settle.amount_points
            ):
                raise ConflictError("企业积分结算分录与成功任务不一致")
            task_values = values_by_settle.get(settle.id, [])
            if (
                not task_values
                or sum(value.settled_points for value in task_values)
                != settle.amount_points
            ):
                raise ConflictError("企业积分结算缺少完整价值分摊")
            for value in task_values:
                if value.task_id != task.id or value.company_task_allocation_id is None:
                    raise ConflictError("企业积分价值分摊任务作用域不一致")
                allocation = session.get(
                    TaskPointLotAllocation, value.company_task_allocation_id
                )
                if (
                    allocation is None
                    or allocation.company_id != company_id
                    or allocation.task_id != task.id
                    or allocation.settled_points != value.settled_points
                ):
                    raise ConflictError("企业积分价值分摊与批次分配不一致")
                lot = session.get(CompanyPointLot, allocation.lot_id)
                if lot is None or lot.company_id != company_id:
                    raise ConflictError("企业积分价值分摊批次作用域不一致")
                if value.receivable_basis_cents == 0:
                    continue
                if (
                    lot.source_kind != PointLotSourceKind.CONTRACT
                    or lot.contract_version_id is None
                    or value.cash_basis_cents != 0
                    or value.subsidy_cents != 0
                ):
                    raise ConflictError("只有合同应收积分可进入企业月结发票")
                line_contract = session.get(
                    CompanyBillingContractVersion, lot.contract_version_id
                )
                if line_contract is None or line_contract.company_id != company_id:
                    raise ConflictError("企业月结行项目合同版本无效")
                expected_amount = (
                    value.settled_points
                    * line_contract.receivable_per_point_cents
                )
                if value.receivable_basis_cents != expected_amount:
                    raise ConflictError("企业月结应收金额与合同积分锚点不一致")
                already_invoiced = session.scalar(
                    select(CompanyInvoiceLine.id).where(
                        CompanyInvoiceLine.value_allocation_id == value.id
                    )
                )
                if already_invoiced is not None:
                    raise ConflictError("企业积分价值分摊已经进入另一张发票")
                line = CompanyInvoiceLine(
                    id=new_id(),
                    invoice_id=invoice_id,
                    task_id=task.id,
                    value_allocation_id=value.id,
                    contract_version_id=line_contract.id,
                    points=value.settled_points,
                    amount_cents=value.receivable_basis_cents,
                )
                lines.append(line)
                subtotal += line.amount_cents

        if account.unbilled_receivable_cents < subtotal:
            raise ConflictError("企业未开票应收投影小于本期发票金额")
        account.unbilled_receivable_cents -= subtotal
        invoice_status = (
            EnterpriseInvoiceStatus.ISSUED
            if subtotal > 0
            else EnterpriseInvoiceStatus.PAID
        )
        invoice = CompanyInvoice(
            id=invoice_id,
            company_id=company_id,
            cycle_id=cycle.id,
            invoice_number=invoice_number,
            status=invoice_status,
            currency=contract.currency,
            subtotal_cents=subtotal,
            credit_cents=0,
            tax_cents=0,
            total_cents=subtotal,
            paid_cents=0,
            issued_at=issue_time,
            due_at=issue_time + timedelta(days=contract.payment_terms_days),
        )
        session.add(invoice)
        cycle.frozen_at = cycle.frozen_at or issue_time
        cycle.status = (
            EnterpriseBillingCycleStatus.ISSUED
            if subtotal > 0
            else EnterpriseBillingCycleStatus.PAID
        )
        # These immutable models intentionally use explicit foreign-key ids
        # rather than ORM relationships. Establish each parent before its
        # children so PostgreSQL cannot legally reorder the inserts.
        session.flush()
        session.add_all(lines)
        session.flush()
        if subtotal > 0:
            session.add(
                AccountsReceivableLedgerEntry(
                    company_id=company_id,
                    invoice_id=invoice.id,
                    payment_transaction_id=None,
                    kind="INVOICE_ISSUED",
                    debit_cents=subtotal,
                    credit_cents=0,
                    idempotency_key=f"enterprise-invoice:{invoice.id}:debit",
                    note=f"Issued from billing cycle {cycle.id}",
                )
            )
        session.flush()
        reconciled_lines = cls._validate_invoice_projection(
            session,
            invoice=invoice,
            cycle=cycle,
        )
        return cycle, invoice, reconciled_lines, True

    @classmethod
    def apply_payment_transaction(
        cls,
        session: Session,
        *,
        invoice_id: str,
        payment_transaction_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[CompanyInvoice, AccountsReceivableLedgerEntry, bool]:
        """Apply a capture or won-dispute credit to accounts receivable only."""

        account, cycle, invoice = cls._locked_invoice_context(
            session,
            invoice_id=invoice_id,
        )
        transaction = session.get(PaymentTransaction, payment_transaction_id)
        if transaction is None:
            raise NotFoundError("支付交易不存在")
        order = session.get(PaymentOrder, transaction.order_id)
        if order is None:
            raise ConflictError("支付交易缺少支付订单")
        if (
            transaction.kind
            not in {
                PaymentTransactionKind.CAPTURE,
                PaymentTransactionKind.DISPUTE_REVERSAL,
            }
            or order.purpose != PaymentPurpose.INVOICE_PAYMENT
            or order.purpose_reference_id != invoice.id
            or order.company_id != invoice.company_id
            or order.personal_workspace_id is not None
            or transaction.order_id != order.id
            or transaction.provider != order.provider
            or transaction.amount_cents <= 0
            or transaction.amount_cents > order.amount_cents
            or transaction.amount_cents > order.captured_amount_cents
            or _stored_utc(transaction.occurred_at)
            < _stored_utc(invoice.issued_at)
            or (
                transaction.kind == PaymentTransactionKind.CAPTURE
                and (
                    transaction.amount_cents != order.amount_cents
                    or transaction.amount_cents != order.captured_amount_cents
                )
            )
        ):
            raise ConflictError("支付交易不是当前发票的应收收款交易")
        if transaction.currency != invoice.currency or order.currency != invoice.currency:
            raise ConflictError("支付交易币种与企业发票不一致")
        cls._validate_payment_transaction_lineage(
            session,
            transaction=transaction,
            order=order,
        )

        entry_kind = (
            "INVOICE_PAYMENT_CAPTURE"
            if transaction.kind == PaymentTransactionKind.CAPTURE
            else "INVOICE_PAYMENT_DISPUTE_REVERSAL"
        )
        key = idempotency_key or f"enterprise-invoice-payment:{transaction.id}"
        key = _required_text(
            key,
            field_name="idempotency_key",
            max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
        )
        existing_by_transaction = session.scalar(
            select(AccountsReceivableLedgerEntry).where(
                AccountsReceivableLedgerEntry.payment_transaction_id
                == transaction.id
            )
        )
        if existing_by_transaction is not None:
            if (
                existing_by_transaction.invoice_id != invoice.id
                or existing_by_transaction.kind != entry_kind
                or existing_by_transaction.credit_cents
                != transaction.amount_cents
                or existing_by_transaction.debit_cents != 0
                or existing_by_transaction.idempotency_key != key
            ):
                raise ConflictError("支付交易已经核销到另一笔应收")
            return invoice, existing_by_transaction, False
        existing_by_key = session.scalar(
            select(AccountsReceivableLedgerEntry).where(
                AccountsReceivableLedgerEntry.idempotency_key == key
            )
        )
        if existing_by_key is not None:
            raise ConflictError("应收核销幂等键已被另一笔操作使用")
        order_totals = cls._order_receivable_totals(
            session,
            invoice_id=invoice.id,
            order_id=order.id,
        )
        if transaction.kind == PaymentTransactionKind.CAPTURE:
            if order_totals["INVOICE_PAYMENT_CAPTURE"]:
                raise ConflictError("企业发票支付订单已经核销过收款")
        else:
            unrestored_chargeback = (
                order_totals["INVOICE_PAYMENT_CHARGEBACK"]
                - order_totals["INVOICE_PAYMENT_DISPUTE_REVERSAL"]
            )
            if transaction.amount_cents > unrestored_chargeback:
                raise ConflictError("争议胜诉金额没有对应的应收拒付借方")
        if invoice.status not in {
            EnterpriseInvoiceStatus.ISSUED,
            EnterpriseInvoiceStatus.PARTIALLY_PAID,
            EnterpriseInvoiceStatus.OVERDUE,
            EnterpriseInvoiceStatus.DISPUTED,
        }:
            raise ConflictError("企业发票当前状态不能核销收款")
        outstanding = invoice.total_cents - invoice.paid_cents
        if transaction.amount_cents > outstanding:
            raise ConflictError("支付交易金额超过企业发票未付金额")

        entry = AccountsReceivableLedgerEntry(
            company_id=invoice.company_id,
            invoice_id=invoice.id,
            payment_transaction_id=transaction.id,
            kind=entry_kind,
            debit_cents=0,
            credit_cents=transaction.amount_cents,
            idempotency_key=key,
            note=(
                f"Captured payment order {order.id}"
                if transaction.kind == PaymentTransactionKind.CAPTURE
                else f"Won dispute restored payment order {order.id}"
            ),
        )
        session.add(entry)
        invoice.paid_cents += transaction.amount_cents
        risk_time = _stored_utc(utcnow())
        cls._refresh_invoice_collection_status(
            session, invoice=invoice, cycle=cycle, as_of=risk_time
        )
        cls._refresh_billing_hold(
            session,
            account=account,
            as_of=risk_time,
        )
        session.flush()
        cls._validate_invoice_projection(session, invoice=invoice, cycle=cycle)
        return invoice, entry, True

    @classmethod
    def apply_payment_reversal(
        cls,
        session: Session,
        *,
        invoice_id: str,
        payment_transaction_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[CompanyInvoice, AccountsReceivableLedgerEntry, bool]:
        """Reopen receivable after a verified refund or chargeback."""

        account, cycle, invoice = cls._locked_invoice_context(
            session,
            invoice_id=invoice_id,
        )
        transaction = session.get(PaymentTransaction, payment_transaction_id)
        if transaction is None:
            raise NotFoundError("支付反向交易不存在")
        order = session.get(PaymentOrder, transaction.order_id)
        if (
            order is None
            or transaction.kind
            not in {
                PaymentTransactionKind.REFUND,
                PaymentTransactionKind.CHARGEBACK,
            }
            or order.purpose != PaymentPurpose.INVOICE_PAYMENT
            or order.purpose_reference_id != invoice.id
            or order.company_id != invoice.company_id
            or order.personal_workspace_id is not None
            or transaction.provider != order.provider
            or transaction.currency != invoice.currency
            or order.currency != invoice.currency
            or transaction.amount_cents <= 0
            or transaction.amount_cents > order.amount_cents
            or transaction.amount_cents > order.captured_amount_cents
            or _stored_utc(transaction.occurred_at)
            < _stored_utc(invoice.issued_at)
        ):
            raise ConflictError("支付反向交易不属于当前企业发票")
        cls._validate_payment_transaction_lineage(
            session,
            transaction=transaction,
            order=order,
        )
        entry_kind = (
            "INVOICE_PAYMENT_REFUND"
            if transaction.kind == PaymentTransactionKind.REFUND
            else "INVOICE_PAYMENT_CHARGEBACK"
        )
        key = _required_text(
            idempotency_key or f"enterprise-invoice-reversal:{transaction.id}",
            field_name="idempotency_key",
            max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
        )
        existing = session.scalar(
            select(AccountsReceivableLedgerEntry).where(
                AccountsReceivableLedgerEntry.payment_transaction_id == transaction.id
            )
        )
        if existing is not None:
            if (
                existing.invoice_id != invoice.id
                or existing.kind != entry_kind
                or existing.debit_cents != transaction.amount_cents
                or existing.credit_cents != 0
                or existing.idempotency_key != key
            ):
                raise ConflictError("支付反向交易已经作用于另一笔应收")
            return invoice, existing, False
        if session.scalar(
            select(AccountsReceivableLedgerEntry.id).where(
                AccountsReceivableLedgerEntry.idempotency_key == key
            )
        ) is not None:
            raise ConflictError("应收反向分录幂等键已被另一笔操作使用")
        if (
            transaction.kind == PaymentTransactionKind.REFUND
            and transaction.amount_cents > order.refunded_amount_cents
        ) or (
            transaction.kind == PaymentTransactionKind.CHARGEBACK
            and transaction.amount_cents > order.disputed_amount_cents
        ):
            raise ConflictError("支付反向交易与支付订单累计投影不一致")
        order_totals = cls._order_receivable_totals(
            session,
            invoice_id=invoice.id,
            order_id=order.id,
        )
        order_net_applied = (
            order_totals["INVOICE_PAYMENT_CAPTURE"]
            + order_totals["INVOICE_PAYMENT_DISPUTE_REVERSAL"]
            - order_totals["INVOICE_PAYMENT_REFUND"]
            - order_totals["INVOICE_PAYMENT_CHARGEBACK"]
        )
        if transaction.amount_cents > order_net_applied:
            raise ConflictError("支付反向交易没有对应的同订单应收收款")
        if transaction.amount_cents > invoice.paid_cents:
            raise ConflictError("支付反向金额超过企业发票已付金额")

        entry = AccountsReceivableLedgerEntry(
            company_id=invoice.company_id,
            invoice_id=invoice.id,
            payment_transaction_id=transaction.id,
            kind=entry_kind,
            debit_cents=transaction.amount_cents,
            credit_cents=0,
            idempotency_key=key,
            note=f"Verified {transaction.kind.value} for payment order {order.id}",
        )
        session.add(entry)
        invoice.paid_cents -= transaction.amount_cents
        risk_time = _stored_utc(utcnow())
        cls._refresh_invoice_collection_status(
            session, invoice=invoice, cycle=cycle, as_of=risk_time
        )
        cls._refresh_billing_hold(
            session,
            account=account,
            as_of=risk_time,
        )
        session.flush()
        cls._validate_invoice_projection(session, invoice=invoice, cycle=cycle)
        return invoice, entry, True

    @classmethod
    def run_dunning(
        cls,
        session: Session,
        *,
        company_id: str,
        as_of: datetime,
        idempotency_key: str,
    ) -> tuple[EnterpriseDunningRun, list[EnterpriseDunningAction], bool]:
        """Project due invoices to overdue exactly once for one intent.

        The company/account lock is the serialization boundary shared with
        cycle, invoice and payment mutations. The durable run is the replay
        receipt; immutable actions explain each state transition.
        """

        effective_at = _utc(as_of, field_name="as_of")
        key = _required_text(
            idempotency_key,
            field_name="idempotency_key",
            max_length=DUNNING_IDEMPOTENCY_KEY_LENGTH,
        )
        if len(key) < 8:
            raise ConflictError("idempotency_key is invalid")
        intent_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "enterprise_dunning",
                    "company_id": company_id,
                    "as_of": effective_at.isoformat(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        cls._locked_point_company(session, company_id)
        account = cls._locked_billing_account(session, company_id)

        def replay_run(
            claimed: EnterpriseDunningRun,
        ) -> tuple[EnterpriseDunningRun, list[EnterpriseDunningAction], bool]:
            if (
                claimed.intent_sha256 != intent_sha256
                or claimed.company_id != company_id
                or _stored_utc(claimed.as_of) != effective_at
            ):
                raise ConflictError("催收运行幂等键已绑定另一意图")
            if claimed.status != EnterpriseDunningRunStatus.COMPLETED:
                raise ConflictError("同一催收运行尚未成功完成")
            claimed_actions = list(
                session.scalars(
                    select(EnterpriseDunningAction)
                    .where(EnterpriseDunningAction.run_id == claimed.id)
                    .order_by(
                        EnterpriseDunningAction.occurred_at,
                        EnterpriseDunningAction.id,
                    )
                ).all()
            )
            return claimed, claimed_actions, False

        existing = session.scalar(
            select(EnterpriseDunningRun)
            .where(EnterpriseDunningRun.idempotency_key == key)
            .with_for_update()
        )
        if existing is not None:
            return replay_run(existing)

        started_at = utcnow()
        run = EnterpriseDunningRun(
            id=new_id(),
            idempotency_key=key,
            intent_sha256=intent_sha256,
            company_id=company_id,
            as_of=effective_at,
            status=EnterpriseDunningRunStatus.RUNNING,
            scanned_count=0,
            overdue_count=0,
            hold_count=0,
            started_at=started_at,
            completed_at=None,
        )
        try:
            # The key is globally unique. The savepoint converts a concurrent
            # cross-company claim into a typed intent conflict without
            # poisoning the caller's surrounding transaction.
            with session.begin_nested():
                session.add(run)
                session.flush()
        except IntegrityError:
            concurrent = session.scalar(
                select(EnterpriseDunningRun)
                .where(EnterpriseDunningRun.idempotency_key == key)
                .with_for_update()
            )
            if concurrent is None:
                raise ConflictError("催收运行幂等键声明失败") from None
            return replay_run(concurrent)

        cycles = list(
            session.scalars(
                select(CompanyBillingCycle)
                .where(CompanyBillingCycle.company_id == company_id)
                .order_by(CompanyBillingCycle.period_start, CompanyBillingCycle.id)
                .with_for_update()
            ).all()
        )
        cycles_by_id = {cycle.id: cycle for cycle in cycles}
        invoices = list(
            session.scalars(
                select(CompanyInvoice)
                .where(CompanyInvoice.company_id == company_id)
                .order_by(CompanyInvoice.due_at, CompanyInvoice.id)
                .with_for_update()
            ).all()
        )
        invoices_by_cycle = {invoice.cycle_id: invoice for invoice in invoices}
        if len(invoices_by_cycle) != len(invoices):
            raise ConflictError("企业月结账期存在重复发票")
        for cycle in cycles:
            if cycle.status in {
                EnterpriseBillingCycleStatus.ISSUED,
                EnterpriseBillingCycleStatus.PAID,
                EnterpriseBillingCycleStatus.OVERDUE,
                EnterpriseBillingCycleStatus.DISPUTED,
            } and cycle.id not in invoices_by_cycle:
                raise ConflictError("已开票企业账期缺少发票投影")

        actions: list[EnterpriseDunningAction] = []

        def append_action(invoice: CompanyInvoice, action: str) -> None:
            prior = session.scalar(
                select(EnterpriseDunningAction).where(
                    EnterpriseDunningAction.invoice_id == invoice.id,
                    EnterpriseDunningAction.action == action,
                    EnterpriseDunningAction.stage == DUNNING_STAGE_OVERDUE,
                )
            )
            if prior is not None:
                if prior.company_id != company_id:
                    raise ConflictError("企业催收动作作用域不一致")
                raise ConflictError("企业催收状态已回退到完成动作之前")
            item = EnterpriseDunningAction(
                id=new_id(),
                run_id=run.id,
                invoice_id=invoice.id,
                company_id=company_id,
                action=action,
                stage=DUNNING_STAGE_OVERDUE,
                occurred_at=effective_at,
            )
            session.add(item)
            actions.append(item)

        delinquent: list[CompanyInvoice] = []
        for invoice in invoices:
            cycle = cycles_by_id.get(invoice.cycle_id)
            if cycle is None:
                raise ConflictError("企业发票账期不存在")
            cls._validate_invoice_projection(
                session,
                invoice=invoice,
                cycle=cycle,
            )
            outstanding = invoice.total_cents - invoice.paid_cents
            if (
                outstanding > 0
                and invoice.status
                in {
                    EnterpriseInvoiceStatus.ISSUED,
                    EnterpriseInvoiceStatus.PARTIALLY_PAID,
                }
                and _stored_utc(invoice.due_at) <= effective_at
            ):
                invoice.status = EnterpriseInvoiceStatus.OVERDUE
                cycle.status = EnterpriseBillingCycleStatus.OVERDUE
                append_action(invoice, "mark_overdue")
            if (
                outstanding > 0
                and invoice.status
                in {
                    EnterpriseInvoiceStatus.OVERDUE,
                    EnterpriseInvoiceStatus.DISPUTED,
                }
            ):
                delinquent.append(invoice)

        held_before = account.billing_hold
        hold_changed = cls._refresh_billing_hold(
            session,
            account=account,
            as_of=effective_at,
        )
        if hold_changed and account.billing_hold and delinquent:
            append_action(delinquent[0], "apply_hold")
        elif hold_changed and held_before and not account.billing_hold:
            if not invoices:
                raise ConflictError("企业催收暂停缺少可审计的发票来源")
            append_action(invoices[-1], "clear_hold")

        session.flush()
        for invoice in invoices:
            cls._validate_invoice_projection(
                session,
                invoice=invoice,
                cycle=cycles_by_id[invoice.cycle_id],
            )
        run.scanned_count = len(invoices)
        run.overdue_count = len(delinquent)
        run.hold_count = 1 if account.billing_hold else 0
        run.status = EnterpriseDunningRunStatus.COMPLETED
        run.completed_at = utcnow()
        session.flush()
        actions.sort(key=lambda action: (_stored_utc(action.occurred_at), action.id))
        return run, actions, True

    @classmethod
    def run_once(
        cls,
        session: Session,
        *,
        as_of: datetime | None = None,
    ) -> dict:
        """Close one due month, run daily dunning, and open its successor.

        Only explicitly activated accounts are candidates. One company is
        processed per call; idle-company savepoints are rolled back so their
        locks cannot starve later companies or concurrent worker instances.
        There is no external I/O and no implicit contract activation here.
        """

        now = _utc(as_of if as_of is not None else utcnow(), field_name="as_of")
        company_ids = list(
            session.scalars(
                select(CompanyBillingAccount.company_id).order_by(
                    CompanyBillingAccount.company_id
                )
            )
        )
        for company_id in company_ids:
            with session.begin_nested() as claim:
                company = session.scalar(
                    select(Company)
                    .where(Company.id == company_id)
                    .with_for_update(key_share=True, skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if company is None:
                    claim.rollback()
                    continue
                cls._locked_point_company(session, company_id)
                account = cls._locked_billing_account(session, company_id)
                contract = session.get(
                    CompanyBillingContractVersion, account.active_contract_version_id
                )
                if contract is None or contract.company_id != company_id:
                    raise ConflictError("企业月结账户的合同投影不完整")
                try:
                    zone = ZoneInfo(contract.timezone_name)
                except ZoneInfoNotFoundError:
                    raise ConflictError("企业月结合同账期时区无效") from None
                local_day = now.astimezone(zone).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                daily_as_of = local_day.astimezone(timezone.utc)
                daily_key = f"enterprise-daily:{company_id}:{local_day.date().isoformat()}"
                daily_run = session.scalar(
                    select(EnterpriseDunningRun).where(
                        EnterpriseDunningRun.idempotency_key == daily_key
                    )
                )
                if daily_run is not None and (
                    daily_run.company_id != company_id
                    or _stored_utc(daily_run.as_of) != daily_as_of
                    or daily_run.status != EnterpriseDunningRunStatus.COMPLETED
                ):
                    raise ConflictError("企业每日催收运行与固定日期意图不一致")
                due_cycle = session.scalar(
                    select(CompanyBillingCycle)
                    .where(
                        CompanyBillingCycle.company_id == company_id,
                        CompanyBillingCycle.status.in_(
                            {
                                EnterpriseBillingCycleStatus.OPEN,
                                EnterpriseBillingCycleStatus.FROZEN,
                            }
                        ),
                        CompanyBillingCycle.period_end <= now,
                    )
                    .order_by(CompanyBillingCycle.period_start, CompanyBillingCycle.id)
                    .limit(1)
                )
                latest_cycle = session.scalar(
                    select(CompanyBillingCycle)
                    .where(CompanyBillingCycle.company_id == company_id)
                    .order_by(CompanyBillingCycle.period_end.desc(), CompanyBillingCycle.id)
                    .limit(1)
                )
                next_period = (
                    _scheduled_next_period(contract, latest_cycle=latest_cycle, now=now)
                    if not account.billing_hold
                    else None
                )
                if due_cycle is None and daily_run is not None and next_period is None:
                    claim.rollback()
                    continue

                closed_ids = []
                if due_cycle is not None:
                    cycle, _, _, issued = cls.close_and_issue_cycle(
                        session,
                        company_id=company_id,
                        cycle_id=due_cycle.id,
                        issued_at=now,
                    )
                    if issued:
                        closed_ids.append(cycle.id)
                daily_created = False
                if daily_run is None:
                    daily_run, _, daily_created = cls.run_dunning(
                        session,
                        company_id=company_id,
                        as_of=daily_as_of,
                        idempotency_key=daily_key,
                    )
                opened_id = None
                # Dunning may have just put the account on hold. Never extend
                # new credit using a pre-dunning snapshot of billing_hold.
                if next_period is not None and not account.billing_hold:
                    cycle, _, created = cls.open_cycle(
                        session,
                        company_id=company_id,
                        period_start=next_period[0],
                        period_end=next_period[1],
                    )
                    if created:
                        opened_id = cycle.id
                if not (closed_ids or daily_created or opened_id):
                    claim.rollback()
                    continue
                session.flush()
                return {
                    "processed": True,
                    "company_id": company_id,
                    "closed_cycle_ids": closed_ids,
                    "opened_cycle_id": opened_id,
                    "dunning_run_id": daily_run.id,
                }
        return {
            "processed": False,
            "company_id": None,
            "closed_cycle_ids": [],
            "opened_cycle_id": None,
            "dunning_run_id": None,
        }
