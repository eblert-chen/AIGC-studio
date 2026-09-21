from __future__ import annotations

import enum
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .finance_snapshot import begin_finance_snapshot

from ..models import (
    AccountsReceivableLedgerEntry,
    BillingUnit,
    ChannelCostEntry,
    ChannelCostSource,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    CompanyInvoice,
    FinanceReconciliationException,
    FinanceReconciliationRun,
    FinanceReconciliationRunSource,
    FinanceReconciliationResolution,
    FinanceReconciliationSnapshot,
    GenerationTask,
    LedgerKind,
    PaymentOrder,
    PaymentOrderStatus,
    PaymentPurpose,
    PaymentDispute,
    PaymentDisputeDebtRecoveryAllocation,
    PaymentDisputeDebtRecoveryReversal,
    PaymentDisputeStatus,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentSettlementBatch,
    PaymentSettlementEntry,
    PaymentSettlementSourceKind,
    PaymentTransaction,
    PaymentTransactionKind,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalTaskPointLotAllocation,
    PersonalWalletAccount,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    PointLotSourceKind,
    ProviderCostStatementBatch,
    ProviderCostStatementLine,
    ReconciliationDimensionStatus,
    ReconciliationRunStatus,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    utcnow,
)
from .errors import ConflictError
from .enterprise_billing import (
    EnterpriseBillingService,
    _contract_content_sha256,
    _validate_contract_cycle_period,
)
from .payment_settlements import validate_archived_statement


RECONCILIATION_SCHEMA_VERSION = 1
RECONCILIATION_DIMENSIONS = ("cash", "points", "tasks", "provider_cost")
_CAPTURED_ORDER_STATUSES = {
    PaymentOrderStatus.PAID,
    PaymentOrderStatus.PARTIALLY_REFUNDED,
    PaymentOrderStatus.REFUNDED,
    PaymentOrderStatus.DISPUTED,
}


@dataclass(frozen=True, slots=True)
class FinancialReconciliationResult:
    run: FinanceReconciliationRun
    snapshots: tuple[FinanceReconciliationSnapshot, ...]
    exceptions: tuple[FinanceReconciliationException, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class _ExceptionDraft:
    dimension: str
    code: str
    status: ReconciliationDimensionStatus
    severity: str
    entity_type: str
    entity_id: str | None
    expected_amount: int | None
    actual_amount: int | None
    details: dict[str, Any]

    def evidence_payload(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "code": self.code,
            "severity": self.severity,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "expected_amount": self.expected_amount,
            "actual_amount": self.actual_amount,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class _DimensionDraft:
    dimension: str
    status: ReconciliationDimensionStatus
    totals: dict[str, Any]
    exceptions: tuple[_ExceptionDraft, ...]

    def evidence_payload(self) -> dict[str, Any]:
        return {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "dimension": self.dimension,
            "status": self.status.value,
            "totals": self.totals,
        }


def _canonical_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ConflictError(f"对账证据包含不能规范化的类型：{type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used by all evidence hashes."""

    try:
        return json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConflictError("对账证据不能规范化为 JSON") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _payment_lines_sha256(entries: Sequence[PaymentSettlementEntry]) -> str:
    return canonical_sha256(
        [
            {
                "provider_line_id": entry.provider_line_id,
                "provider_transaction_id": entry.provider_transaction_id,
                "related_provider_reference": entry.related_provider_reference,
                "line_type": entry.line_type,
                "gross_amount_cents": entry.gross_amount_cents,
                "fee_amount_cents": entry.fee_amount_cents,
                "net_amount_cents": entry.net_amount_cents,
                "currency": entry.currency,
                "occurred_at": _database_utc(entry.occurred_at),
            }
            for entry in sorted(entries, key=lambda item: item.provider_line_id)
        ]
    )


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ConflictError(f"{field} 必须是时间")
    if value.tzinfo is None:
        raise ConflictError(f"{field} 必须包含时区")
    return value.astimezone(timezone.utc)


def _database_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; persisted reconciliation times are UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _status_for_exceptions(
    exceptions: Sequence[_ExceptionDraft],
    *,
    source_unavailable: bool = False,
) -> ReconciliationDimensionStatus:
    if source_unavailable:
        return ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
    priorities = (
        ReconciliationDimensionStatus.DUPLICATE,
        ReconciliationDimensionStatus.MISMATCH,
        ReconciliationDimensionStatus.MISSING,
        ReconciliationDimensionStatus.UNATTRIBUTED,
        ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
        ReconciliationDimensionStatus.PENDING,
    )
    statuses = {item.status for item in exceptions}
    for status in priorities:
        if status in statuses:
            return status
    return ReconciliationDimensionStatus.MATCHED


def _exception_sort_key(item: _ExceptionDraft) -> tuple[Any, ...]:
    return (
        item.dimension,
        item.code,
        item.entity_type,
        item.entity_id or "",
        item.expected_amount if item.expected_amount is not None else -1,
        item.actual_amount if item.actual_amount is not None else -1,
        canonical_json(item.details),
    )


def _terminal_run_status(
    *,
    exception_count: int,
    dimension_statuses: Sequence[ReconciliationDimensionStatus],
) -> ReconciliationRunStatus:
    if exception_count == 0 and all(
        status
        in {
            ReconciliationDimensionStatus.MATCHED,
            ReconciliationDimensionStatus.NOT_APPLICABLE,
        }
        for status in dimension_statuses
    ):
        return ReconciliationRunStatus.BALANCED
    return ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS


class FinancialReconciliationService:
    """Persist a fail-closed, independently hashable four-dimension snapshot.

    The service reads commercial facts only.  It never repairs a wallet, task,
    payment, settlement line, or provider-cost row.  Every discrepancy becomes
    an immutable exception tied to the immutable snapshot that observed it.
    """

    @staticmethod
    def _source_ids(values: Sequence[str] | None, *, field: str) -> tuple[str, ...]:
        normalized = tuple(str(value or "").strip() for value in (values or ()))
        if any(not value or len(value) > 36 for value in normalized):
            raise ConflictError(f"{field} 包含无效批次编号")
        if len(set(normalized)) != len(normalized):
            raise ConflictError(f"{field} 包含重复批次编号")
        return tuple(sorted(normalized))

    @classmethod
    def _payment_batches(
        cls,
        session: Session,
        *,
        batch_ids: Sequence[str] | None,
        available: bool,
        start: datetime,
        end: datetime,
        provider: str | None,
        merchant_account: str | None,
    ) -> tuple[PaymentSettlementBatch, ...]:
        ids = cls._source_ids(batch_ids, field="payment_settlement_batch_ids")
        if not available:
            if ids:
                raise ConflictError("支付结算来源不可用时不能绑定结算批次")
            return ()
        if not ids:
            raise ConflictError("支付结算来源可用时必须绑定 PSP 与银行批次")
        batches = tuple(
            session.scalars(
                select(PaymentSettlementBatch)
                .where(PaymentSettlementBatch.id.in_(ids))
                .order_by(PaymentSettlementBatch.id)
            ).all()
        )
        if len(batches) != len(ids):
            raise ConflictError("支付结算批次不存在或不完整")
        for batch in batches:
            if (
                _database_utc(batch.period_start) != start
                or _database_utc(batch.period_end) != end
                or (provider is not None and batch.provider != provider)
                or (
                    merchant_account is not None
                    and batch.merchant_account != merchant_account
                )
            ):
                raise ConflictError("支付结算批次与渠道、商户或对账期间不一致")
        kinds_by_account: dict[
            tuple[str, str], set[PaymentSettlementSourceKind]
        ] = defaultdict(set)
        for batch in batches:
            kinds_by_account[(batch.provider, batch.merchant_account)].add(
                batch.source_kind
            )
        if any(
            kinds
            != {
                PaymentSettlementSourceKind.PSP_STATEMENT,
                PaymentSettlementSourceKind.BANK_STATEMENT,
            }
            for kinds in kinds_by_account.values()
        ):
            raise ConflictError("每个支付渠道商户必须同时绑定 PSP 结算单和银行流水")

        coverage = select(PaymentSettlementBatch.id).where(
            PaymentSettlementBatch.period_start == start,
            PaymentSettlementBatch.period_end == end,
        )
        if provider is not None:
            coverage = coverage.where(PaymentSettlementBatch.provider == provider)
        if merchant_account is not None:
            coverage = coverage.where(
                PaymentSettlementBatch.merchant_account == merchant_account
            )
        all_matching_ids = set(session.scalars(coverage).all())
        if all_matching_ids != set(ids):
            raise ConflictError("支付结算批次未覆盖该渠道、商户与期间的全部已导入文件")
        return batches

    @classmethod
    def _provider_cost_batches(
        cls,
        session: Session,
        *,
        batch_ids: Sequence[str] | None,
        available: bool,
        start: datetime,
        end: datetime,
    ) -> tuple[ProviderCostStatementBatch, ...]:
        ids = cls._source_ids(batch_ids, field="provider_cost_batch_ids")
        if not available:
            if ids:
                raise ConflictError("供应商成本来源不可用时不能绑定账单批次")
            return ()
        if not ids:
            raise ConflictError("供应商成本来源可用时必须绑定账单批次")
        batches = tuple(
            session.scalars(
                select(ProviderCostStatementBatch)
                .where(ProviderCostStatementBatch.id.in_(ids))
                .order_by(ProviderCostStatementBatch.id)
            ).all()
        )
        if len(batches) != len(ids):
            raise ConflictError("供应商成本账单批次不存在或不完整")
        if any(
            _database_utc(batch.period_start) != start
            or _database_utc(batch.period_end) != end
            for batch in batches
        ):
            raise ConflictError("供应商成本账单批次与对账期间不一致")
        all_matching_ids = set(
            session.scalars(
                select(ProviderCostStatementBatch.id).where(
                    ProviderCostStatementBatch.period_start == start,
                    ProviderCostStatementBatch.period_end == end,
                )
            ).all()
        )
        if all_matching_ids != set(ids):
            raise ConflictError("供应商成本批次未覆盖该期间的全部已导入账单")
        return batches

    @classmethod
    def run(
        cls,
        session: Session,
        *,
        run_kind: str,
        period_start: datetime,
        period_end: datetime,
        idempotency_key: str,
        provider: str | None = None,
        merchant_account: str | None = None,
        provider_statement_available: bool = True,
        payment_settlement_batch_ids: Sequence[str] | None = None,
        provider_cost_statement_available: bool = True,
        provider_cost_batch_ids: Sequence[str] | None = None,
        source_watermarks: Mapping[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> FinancialReconciliationResult:
        start = _utc(period_start, field="period_start")
        end = _utc(period_end, field="period_end")
        if end <= start:
            raise ConflictError("对账结束时间必须晚于开始时间")
        normalized_kind = str(run_kind or "").strip()
        if not normalized_kind or len(normalized_kind) > 24:
            raise ConflictError("对账类型无效")
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key or len(normalized_key) > 160:
            raise ConflictError("对账幂等键无效")
        normalized_provider = str(provider).strip() if provider is not None else None
        normalized_merchant = (
            str(merchant_account).strip() if merchant_account is not None else None
        )
        if normalized_provider == "" or normalized_merchant == "":
            raise ConflictError("支付渠道或商户号不能为空")
        if normalized_merchant is not None and normalized_provider is None:
            raise ConflictError("指定商户号时必须同时指定支付渠道")
        if not isinstance(provider_statement_available, bool):
            raise ConflictError("支付渠道结算单可用状态无效")
        if not isinstance(provider_cost_statement_available, bool):
            raise ConflictError("供应商成本账单可用状态无效")

        begin_finance_snapshot(session)

        payment_batches = cls._payment_batches(
            session,
            batch_ids=payment_settlement_batch_ids,
            available=provider_statement_available,
            start=start,
            end=end,
            provider=normalized_provider,
            merchant_account=normalized_merchant,
        )
        provider_cost_batches = cls._provider_cost_batches(
            session,
            batch_ids=provider_cost_batch_ids,
            available=provider_cost_statement_available,
            start=start,
            end=end,
        )

        try:
            caller_watermarks = dict(source_watermarks or {})
        except (TypeError, ValueError) as exc:
            raise ConflictError("source_watermarks 必须是对象") from exc
        for reserved_key in (
            "schema_version",
            "intent_sha256",
            "provider_statement",
            "provider_cost_statement",
            "payment_settlement_batches",
            "provider_cost_batches",
        ):
            if reserved_key in caller_watermarks:
                raise ConflictError(f"source_watermarks 不能覆盖保留字段 {reserved_key}")
        normalized_watermarks = _canonical_value(caller_watermarks)
        if not isinstance(normalized_watermarks, dict):  # pragma: no cover - defensive
            raise ConflictError("source_watermarks 必须是对象")
        intent = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "run_kind": normalized_kind,
            "period_start": start,
            "period_end": end,
            "provider": normalized_provider,
            "merchant_account": normalized_merchant,
            "provider_statement_available": provider_statement_available,
            "payment_settlement_batch_ids": [batch.id for batch in payment_batches],
            "provider_cost_statement_available": provider_cost_statement_available,
            "provider_cost_batch_ids": [batch.id for batch in provider_cost_batches],
            "source_watermarks": normalized_watermarks,
        }
        intent_sha256 = canonical_sha256(intent)
        persisted_watermarks = {
            **normalized_watermarks,
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "intent_sha256": intent_sha256,
            "provider_statement": {
                "status": "available" if provider_statement_available else "unavailable"
            },
            "provider_cost_statement": {
                "status": (
                    "available" if provider_cost_statement_available else "unavailable"
                )
            },
            "payment_settlement_batches": [
                {
                    "id": batch.id,
                    "source_kind": batch.source_kind.value,
                    "document_sha256": batch.source_document_sha256,
                    "lines_sha256": batch.lines_sha256,
                    "verification_method": batch.verification_method,
                    "verified_at": _database_utc(batch.verified_at).isoformat(),
                    "source_authenticity": "unverified",
                }
                for batch in payment_batches
            ],
            "provider_cost_batches": [
                {
                    "id": batch.id,
                    "supplier": batch.supplier,
                    "supplier_account": batch.supplier_account,
                    "document_sha256": batch.source_document_sha256,
                    "lines_sha256": batch.lines_sha256,
                    "verification_method": batch.verification_method,
                    "verified_at": _database_utc(batch.verified_at).isoformat(),
                    "source_authenticity": "unverified",
                }
                for batch in provider_cost_batches
            ],
        }

        existing = session.scalar(
            select(FinanceReconciliationRun)
            .where(FinanceReconciliationRun.idempotency_key == normalized_key)
            .with_for_update()
        )
        if existing is not None:
            return cls._replay(
                session,
                existing=existing,
                intent_sha256=intent_sha256,
                run_kind=normalized_kind,
                period_start=start,
                period_end=end,
                provider=normalized_provider,
                merchant_account=normalized_merchant,
            )

        current = _utc(started_at or utcnow(), field="started_at")
        run = FinanceReconciliationRun(
            run_kind=normalized_kind,
            period_start=start,
            period_end=end,
            provider=normalized_provider,
            merchant_account=normalized_merchant,
            status=ReconciliationRunStatus.RUNNING,
            source_watermarks=persisted_watermarks,
            control_totals={},
            snapshot_sha256=None,
            idempotency_key=normalized_key,
            started_at=current,
            completed_at=None,
        )
        try:
            with session.begin_nested():
                session.add(run)
                session.flush()
        except IntegrityError as exc:
            if session.get_bind().dialect.name == "postgresql":
                sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
                constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                if sqlstate != "23505" or constraint != "uq_finance_reconciliation_key":
                    raise
            existing = session.scalar(
                select(FinanceReconciliationRun)
                .where(FinanceReconciliationRun.idempotency_key == normalized_key)
                .with_for_update()
            )
            if existing is None:
                # A concurrent winner can own the unique key while remaining
                # invisible to this REPEATABLE READ snapshot. Never call this
                # a successful replay or continue using mixed-age evidence;
                # a fresh transaction with the same key can read the winner.
                if session.get_bind().dialect.name == "postgresql":
                    raise ConflictError(
                        "对账幂等键已被并发事务占用，请使用原幂等键重试"
                    ) from None
                raise
            return cls._replay(
                session,
                existing=existing,
                intent_sha256=intent_sha256,
                run_kind=normalized_kind,
                period_start=start,
                period_end=end,
                provider=normalized_provider,
                merchant_account=normalized_merchant,
            )

        session.add_all(
            [
                FinanceReconciliationRunSource(
                    run_id=run.id,
                    source_kind="payment_settlement",
                    payment_settlement_batch_id=batch.id,
                    provider_cost_batch_id=None,
                    document_sha256=batch.source_document_sha256,
                )
                for batch in payment_batches
            ]
            + [
                FinanceReconciliationRunSource(
                    run_id=run.id,
                    source_kind="provider_cost",
                    payment_settlement_batch_id=None,
                    provider_cost_batch_id=batch.id,
                    document_sha256=batch.source_document_sha256,
                )
                for batch in provider_cost_batches
            ]
        )
        session.flush()

        dimensions = (
            cls._cash_dimension(
                session,
                start=start,
                end=end,
                provider=normalized_provider,
                merchant_account=normalized_merchant,
                provider_statement_available=provider_statement_available,
                settlement_batches=payment_batches,
            ),
            cls._points_dimension(session),
            cls._tasks_dimension(session, start=start, end=end),
            cls._provider_cost_dimension(
                session,
                start=start,
                end=end,
                statement_available=provider_cost_statement_available,
                statement_batches=provider_cost_batches,
            ),
        )
        if tuple(item.dimension for item in dimensions) != RECONCILIATION_DIMENSIONS:
            raise AssertionError("financial reconciliation dimension order drifted")

        snapshots: list[FinanceReconciliationSnapshot] = []
        exception_models: list[FinanceReconciliationException] = []
        all_exception_payloads: list[dict[str, Any]] = []
        dimension_payloads: list[dict[str, Any]] = []
        control_dimensions: dict[str, Any] = {}
        for dimension in dimensions:
            ordered_exceptions = sorted(dimension.exceptions, key=_exception_sort_key)
            evidence_payload = {
                **dimension.evidence_payload(),
                "exceptions": [
                    item.evidence_payload() for item in ordered_exceptions
                ],
            }
            evidence_sha256 = canonical_sha256(evidence_payload)
            snapshot = FinanceReconciliationSnapshot(
                run_id=run.id,
                dimension=dimension.dimension,
                status=dimension.status,
                totals=dimension.totals,
                evidence_sha256=evidence_sha256,
            )
            session.add(snapshot)
            snapshots.append(snapshot)
            dimension_payloads.append(
                {**evidence_payload, "evidence_sha256": evidence_sha256}
            )
            control_dimensions[dimension.dimension] = {
                "status": dimension.status.value,
                "exception_count": len(ordered_exceptions),
                "totals": dimension.totals,
            }
            for item in ordered_exceptions:
                exception_payload = item.evidence_payload()
                exception_sha256 = canonical_sha256(exception_payload)
                exception_models.append(
                    FinanceReconciliationException(
                        run_id=run.id,
                        dimension=item.dimension,
                        code=item.code,
                        severity=item.severity,
                        entity_type=item.entity_type,
                        entity_id=item.entity_id,
                        expected_amount=item.expected_amount,
                        actual_amount=item.actual_amount,
                        evidence_sha256=exception_sha256,
                        details=item.details,
                    )
                )
                all_exception_payloads.append(
                    {**exception_payload, "evidence_sha256": exception_sha256}
                )

        session.add_all(exception_models)
        total_exception_count = len(exception_models)
        control_totals = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "dimensions": control_dimensions,
            "total_exception_count": total_exception_count,
        }
        final_evidence = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "intent_sha256": intent_sha256,
            "source_watermarks": persisted_watermarks,
            "dimensions": dimension_payloads,
            "exceptions": all_exception_payloads,
            "control_totals": control_totals,
        }
        run.control_totals = control_totals
        run.snapshot_sha256 = canonical_sha256(final_evidence)
        run.status = _terminal_run_status(
            exception_count=total_exception_count,
            dimension_statuses=[item.status for item in dimensions],
        )
        run.completed_at = current
        session.flush()
        return FinancialReconciliationResult(
            run=run,
            snapshots=tuple(snapshots),
            exceptions=tuple(exception_models),
            created=True,
        )

    @classmethod
    def record_resolution(
        cls,
        session: Session,
        *,
        exception_id: str,
        action: str,
        note: str,
        evidence_sha256: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> tuple[FinanceReconciliationResolution, bool]:
        """Append an audited disposition without rewriting historical evidence."""

        allowed_actions = {
            "acknowledged",
            "source_corrected",
            "ledger_corrected",
            "accepted_adjustment",
            "escalated",
        }
        normalized_action = str(action or "").strip()
        normalized_note = str(note or "").strip()
        normalized_key = str(idempotency_key or "").strip()
        if normalized_action not in allowed_actions:
            raise ConflictError("对账异常处置动作无效")
        if not normalized_note or len(normalized_note) > 240:
            raise ConflictError("对账异常处置说明无效")
        if not normalized_key or len(normalized_key) > 120:
            raise ConflictError("对账异常处置幂等键无效")
        if (
            not isinstance(evidence_sha256, str)
            or len(evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in evidence_sha256)
        ):
            raise ConflictError("对账异常处置证据摘要无效")
        exception = session.get(FinanceReconciliationException, exception_id)
        if exception is None:
            raise ConflictError("对账异常不存在")
        if session.get(User, actor_user_id) is None:
            raise ConflictError("对账异常处置人不存在")
        existing = session.scalar(
            select(FinanceReconciliationResolution).where(
                FinanceReconciliationResolution.exception_id == exception_id,
                FinanceReconciliationResolution.idempotency_key == normalized_key,
            )
        )
        if existing is not None:
            if (
                existing.action != normalized_action
                or existing.note != normalized_note
                or existing.evidence_sha256 != evidence_sha256
                or existing.actor_user_id != actor_user_id
            ):
                raise ConflictError("对账异常处置幂等键对应了不同事实")
            return existing, False
        resolution = FinanceReconciliationResolution(
            exception_id=exception_id,
            action=normalized_action,
            note=normalized_note,
            evidence_sha256=evidence_sha256,
            actor_user_id=actor_user_id,
            idempotency_key=normalized_key,
        )
        session.add(resolution)
        session.flush()
        return resolution, True

    @staticmethod
    def _replay(
        session: Session,
        *,
        existing: FinanceReconciliationRun,
        intent_sha256: str,
        run_kind: str,
        period_start: datetime,
        period_end: datetime,
        provider: str | None,
        merchant_account: str | None,
    ) -> FinancialReconciliationResult:
        stored_intent = (existing.source_watermarks or {}).get("intent_sha256")
        same_intent = (
            stored_intent == intent_sha256
            and existing.run_kind == run_kind
            and _database_utc(existing.period_start) == period_start
            and _database_utc(existing.period_end) == period_end
            and existing.provider == provider
            and existing.merchant_account == merchant_account
        )
        if not same_intent:
            raise ConflictError("对账幂等键已被另一笔不同意图的运行使用")
        snapshots = tuple(
            session.scalars(
                select(FinanceReconciliationSnapshot)
                .where(FinanceReconciliationSnapshot.run_id == existing.id)
            ).all()
        )
        exceptions = tuple(
            session.scalars(
                select(FinanceReconciliationException)
                .where(FinanceReconciliationException.run_id == existing.id)
                .order_by(
                    FinanceReconciliationException.dimension,
                    FinanceReconciliationException.code,
                    FinanceReconciliationException.entity_type,
                    FinanceReconciliationException.entity_id,
                    FinanceReconciliationException.id,
                )
            ).all()
        )
        if (
            existing.status == ReconciliationRunStatus.RUNNING
            or existing.snapshot_sha256 is None
            or existing.completed_at is None
            or len(snapshots) != len(RECONCILIATION_DIMENSIONS)
        ):
            raise ConflictError("同一对账运行尚未形成完整不可变快照")

        snapshot_by_dimension = {item.dimension: item for item in snapshots}
        if set(snapshot_by_dimension) != set(RECONCILIATION_DIMENSIONS):
            raise ConflictError("对账快照维度不完整或重复")

        watermarks = existing.source_watermarks
        if not isinstance(watermarks, dict):
            raise ConflictError("对账源水位快照已损坏")
        payment_watermarks = watermarks.get("payment_settlement_batches")
        cost_watermarks = watermarks.get("provider_cost_batches")
        if not isinstance(payment_watermarks, list) or not isinstance(
            cost_watermarks, list
        ):
            raise ConflictError("对账批次水位快照已损坏")
        try:
            expected_payment_sources = {
                (str(item["id"]), str(item["document_sha256"]))
                for item in payment_watermarks
            }
            expected_cost_sources = {
                (str(item["id"]), str(item["document_sha256"]))
                for item in cost_watermarks
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError("对账批次水位快照已损坏") from exc
        if len(expected_payment_sources) != len(payment_watermarks) or len(
            expected_cost_sources
        ) != len(cost_watermarks):
            raise ConflictError("对账批次水位快照含重复项")

        sources = tuple(
            session.scalars(
                select(FinanceReconciliationRunSource).where(
                    FinanceReconciliationRunSource.run_id == existing.id
                )
            ).all()
        )
        actual_payment_sources: set[tuple[str, str]] = set()
        actual_cost_sources: set[tuple[str, str]] = set()
        for source in sources:
            if source.source_kind == "payment_settlement":
                if (
                    source.payment_settlement_batch_id is None
                    or source.provider_cost_batch_id is not None
                ):
                    raise ConflictError("支付结算批次绑定已损坏")
                batch = session.get(
                    PaymentSettlementBatch, source.payment_settlement_batch_id
                )
                if (
                    batch is None
                    or batch.source_document_sha256 != source.document_sha256
                ):
                    raise ConflictError("支付结算批次绑定与源文件不一致")
                validate_archived_statement(batch)
                watermark = next((item for item in payment_watermarks if item["id"] == batch.id), {})
                if (
                    watermark.get("lines_sha256") != batch.lines_sha256
                    or watermark.get("verification_method") != batch.verification_method
                    or watermark.get("verified_at") != _database_utc(batch.verified_at).isoformat()
                    or watermark.get("source_authenticity") != "unverified"
                ):
                    raise ConflictError("支付结算批次来源证据与快照不一致")
                actual_payment_sources.add((batch.id, source.document_sha256))
            elif source.source_kind == "provider_cost":
                if (
                    source.provider_cost_batch_id is None
                    or source.payment_settlement_batch_id is not None
                ):
                    raise ConflictError("供应商成本批次绑定已损坏")
                batch = session.get(
                    ProviderCostStatementBatch, source.provider_cost_batch_id
                )
                if (
                    batch is None
                    or batch.source_document_sha256 != source.document_sha256
                ):
                    raise ConflictError("供应商成本批次绑定与源文件不一致")
                validate_archived_statement(batch)
                watermark = next((item for item in cost_watermarks if item["id"] == batch.id), {})
                if (
                    watermark.get("lines_sha256") != batch.lines_sha256
                    or watermark.get("verification_method") != batch.verification_method
                    or watermark.get("verified_at") != _database_utc(batch.verified_at).isoformat()
                    or watermark.get("source_authenticity") != "unverified"
                ):
                    raise ConflictError("供应商成本批次来源证据与快照不一致")
                actual_cost_sources.add((batch.id, source.document_sha256))
            else:
                raise ConflictError("对账运行绑定了未知来源")
        if (
            actual_payment_sources != expected_payment_sources
            or actual_cost_sources != expected_cost_sources
            or len(sources)
            != len(expected_payment_sources) + len(expected_cost_sources)
        ):
            raise ConflictError("对账运行的外部源批次绑定不完整")

        exceptions_by_dimension: dict[str, list[_ExceptionDraft]] = defaultdict(list)
        for item in exceptions:
            if item.dimension not in snapshot_by_dimension:
                raise ConflictError("对账异常指向了未知维度")
            draft = _ExceptionDraft(
                dimension=item.dimension,
                code=item.code,
                status=ReconciliationDimensionStatus.MISMATCH,
                severity=item.severity,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                expected_amount=item.expected_amount,
                actual_amount=item.actual_amount,
                details=item.details,
            )
            if canonical_sha256(draft.evidence_payload()) != item.evidence_sha256:
                raise ConflictError("对账异常证据摘要校验失败")
            exceptions_by_dimension[item.dimension].append(draft)

        dimension_payloads: list[dict[str, Any]] = []
        all_exception_payloads: list[dict[str, Any]] = []
        control_dimensions: dict[str, Any] = {}
        for dimension in RECONCILIATION_DIMENSIONS:
            snapshot = snapshot_by_dimension[dimension]
            ordered = sorted(
                exceptions_by_dimension.get(dimension, ()), key=_exception_sort_key
            )
            exception_payloads = [item.evidence_payload() for item in ordered]
            evidence_payload = {
                "schema_version": RECONCILIATION_SCHEMA_VERSION,
                "dimension": snapshot.dimension,
                "status": snapshot.status.value,
                "totals": snapshot.totals,
                "exceptions": exception_payloads,
            }
            evidence_sha256 = canonical_sha256(evidence_payload)
            if evidence_sha256 != snapshot.evidence_sha256:
                raise ConflictError("对账维度快照证据摘要校验失败")
            dimension_payloads.append(
                {**evidence_payload, "evidence_sha256": evidence_sha256}
            )
            control_dimensions[dimension] = {
                "status": snapshot.status.value,
                "exception_count": len(ordered),
                "totals": snapshot.totals,
            }
            for payload in exception_payloads:
                all_exception_payloads.append(
                    {**payload, "evidence_sha256": canonical_sha256(payload)}
                )

        control_totals = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "dimensions": control_dimensions,
            "total_exception_count": len(exceptions),
        }
        if canonical_json(existing.control_totals) != canonical_json(control_totals):
            raise ConflictError("对账运行控制总额与不可变快照不一致")
        expected_snapshot_sha256 = canonical_sha256(
            {
                "schema_version": RECONCILIATION_SCHEMA_VERSION,
                "intent_sha256": intent_sha256,
                "source_watermarks": watermarks,
                "dimensions": dimension_payloads,
                "exceptions": all_exception_payloads,
                "control_totals": control_totals,
            }
        )
        if existing.snapshot_sha256 != expected_snapshot_sha256:
            raise ConflictError("对账运行总快照证据摘要校验失败")
        expected_status = _terminal_run_status(
            exception_count=len(exceptions),
            dimension_statuses=[
                snapshot_by_dimension[dimension].status
                for dimension in RECONCILIATION_DIMENSIONS
            ],
        )
        if existing.status != expected_status:
            raise ConflictError("对账运行结论与不可变快照不一致")
        return FinancialReconciliationResult(
            run=existing,
            snapshots=tuple(
                snapshot_by_dimension[dimension]
                for dimension in RECONCILIATION_DIMENSIONS
            ),
            exceptions=exceptions,
            created=False,
        )

    @staticmethod
    def _validate_invoice_document(session: Session, invoice: CompanyInvoice) -> None:
        """Read-only proof of invoice -> period/contract -> task value -> AR."""

        cycle = session.get(CompanyBillingCycle, invoice.cycle_id)
        if cycle is None or cycle.company_id != invoice.company_id:
            raise ConflictError("企业发票缺少同作用域账期")
        contract = session.get(CompanyBillingContractVersion, cycle.contract_version_id)

        def validate_contract(version: CompanyBillingContractVersion | None) -> None:
            if (
                version is None
                or version.company_id != invoice.company_id
                or version.currency != invoice.currency
                or version.receivable_per_point_cents != 10
            ):
                raise ConflictError("企业发票合同版本或积分价值无效")
            expected_hash = _contract_content_sha256(
                company_id=version.company_id,
                contract_reference=version.contract_reference,
                currency=version.currency,
                timezone_name=version.timezone_name,
                cycle_day=version.cycle_day,
                payment_terms_days=version.payment_terms_days,
                credit_limit_points=version.credit_limit_points,
                effective_at=_database_utc(version.effective_at),
                expires_at=_database_utc(version.expires_at) if version.expires_at else None,
                supersedes_version_id=version.supersedes_version_id,
            )
            if expected_hash != version.content_sha256:
                raise ConflictError("企业发票合同内容摘要不一致")

        validate_contract(contract)
        assert contract is not None
        cycle_start, cycle_end = _database_utc(cycle.period_start), _database_utc(cycle.period_end)
        if (
            cycle_end <= cycle_start
            or cycle_start < _database_utc(contract.effective_at)
            or (contract.expires_at is not None and cycle_end > _database_utc(contract.expires_at))
        ):
            raise ConflictError("企业发票账期超出合同有效期间")
        _validate_contract_cycle_period(contract, period_start=cycle_start, period_end=cycle_end)
        lines = EnterpriseBillingService._validate_invoice_projection(
            session, invoice=invoice, cycle=cycle
        )
        expected_values = set(
            session.scalars(
                select(PointLotSettlementValueAllocation.id)
                .join(
                    CompanyPointLedgerEntry,
                    CompanyPointLedgerEntry.id
                    == PointLotSettlementValueAllocation.company_settle_ledger_id,
                )
                .where(
                    CompanyPointLedgerEntry.company_id == invoice.company_id,
                    CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE,
                    CompanyPointLedgerEntry.created_at >= cycle_start,
                    CompanyPointLedgerEntry.created_at < cycle_end,
                    PointLotSettlementValueAllocation.receivable_basis_cents > 0,
                )
            ).all()
        )
        if {line.value_allocation_id for line in lines} != expected_values:
            raise ConflictError("企业发票未完整覆盖账期应收价值分摊")
        for line in lines:
            value = session.get(PointLotSettlementValueAllocation, line.value_allocation_id)
            allocation = (
                session.get(TaskPointLotAllocation, value.company_task_allocation_id)
                if value is not None and value.company_task_allocation_id is not None else None
            )
            lot = session.get(CompanyPointLot, allocation.lot_id) if allocation is not None else None
            settle = (
                session.get(CompanyPointLedgerEntry, value.company_settle_ledger_id)
                if value is not None and value.company_settle_ledger_id is not None else None
            )
            task = session.get(GenerationTask, line.task_id)
            line_contract = session.get(CompanyBillingContractVersion, line.contract_version_id)
            validate_contract(line_contract)
            assert line_contract is not None
            if (
                value is None or allocation is None or lot is None or settle is None or task is None
                or value.company_id != invoice.company_id or value.personal_workspace_id is not None
                or value.task_id != line.task_id or value.settled_points != line.points
                or value.receivable_basis_cents != line.amount_cents
                or value.cash_basis_cents != 0 or value.subsidy_cents != 0
                or allocation.company_id != invoice.company_id or allocation.task_id != line.task_id
                or allocation.settled_points != value.settled_points
                or lot.company_id != invoice.company_id or lot.source_kind != PointLotSourceKind.CONTRACT
                or lot.contract_version_id != line.contract_version_id
                or settle.company_id != invoice.company_id or settle.task_id != line.task_id
                or settle.kind != PointLedgerKind.SETTLE
                or not cycle_start <= _database_utc(settle.created_at) < cycle_end
                or task.company_id != invoice.company_id or task.personal_workspace_id is not None
                or task.status != TaskStatus.SUCCEEDED or task.billing_unit != BillingUnit.POINT
                or task.billing_version != 2 or task.actual_cost_points != settle.amount_points
                or line.amount_cents != line.points * line_contract.receivable_per_point_cents
            ):
                raise ConflictError("企业发票行与任务、合同积分批次或应收价值不一致")

    @classmethod
    def _cash_dimension(
        cls,
        session: Session,
        *,
        start: datetime,
        end: datetime,
        provider: str | None,
        merchant_account: str | None,
        provider_statement_available: bool,
        settlement_batches: Sequence[PaymentSettlementBatch],
    ) -> _DimensionDraft:
        exceptions: list[_ExceptionDraft] = []

        def issue(
            code: str,
            status: ReconciliationDimensionStatus,
            entity_type: str,
            entity_id: str | None,
            *,
            expected: int | None = None,
            actual: int | None = None,
            severity: str = "critical",
            **details: Any,
        ) -> None:
            exceptions.append(
                _ExceptionDraft(
                    dimension="cash",
                    code=code,
                    status=status,
                    severity=severity,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    expected_amount=expected,
                    actual_amount=actual,
                    details=details,
                )
            )

        order_statement = select(PaymentOrder).where(
            PaymentOrder.purpose.in_(
                (PaymentPurpose.POINT_PURCHASE, PaymentPurpose.INVOICE_PAYMENT)
            ),
            or_(
                and_(
                    PaymentOrder.created_at >= start,
                    PaymentOrder.created_at < end,
                ),
                and_(
                    PaymentOrder.captured_at.is_not(None),
                    PaymentOrder.captured_at >= start,
                    PaymentOrder.captured_at < end,
                ),
            ),
        )
        if provider is not None:
            order_statement = order_statement.where(PaymentOrder.provider == provider)
        if merchant_account is not None:
            order_statement = order_statement.where(
                PaymentOrder.merchant_account == merchant_account
            )
        orders = list(
            session.scalars(order_statement.order_by(PaymentOrder.created_at, PaymentOrder.id)).all()
        )
        captured_orders = []
        for order in orders:
            captured_indicator = (
                order.captured_amount_cents > 0
                or order.status in _CAPTURED_ORDER_STATUSES
            )
            captured_in_period = (
                order.captured_at is not None
                and start <= _database_utc(order.captured_at) < end
            )
            if captured_in_period or (captured_indicator and order.captured_at is None):
                captured_orders.append(order)
        order_ids = [order.id for order in captured_orders]

        captures_by_order: dict[str, list[PaymentTransaction]] = defaultdict(list)
        if order_ids:
            for transaction in session.scalars(
                select(PaymentTransaction)
                .where(
                    PaymentTransaction.order_id.in_(order_ids),
                    PaymentTransaction.kind == PaymentTransactionKind.CAPTURE,
                )
                .order_by(PaymentTransaction.occurred_at, PaymentTransaction.id)
            ).all():
                captures_by_order[transaction.order_id].append(transaction)

        company_lots_by_order: dict[str, list[CompanyPointLot]] = defaultdict(list)
        personal_lots_by_order: dict[str, list[PersonalPointLot]] = defaultdict(list)
        company_credits_by_order: dict[str, list[CompanyPointLedgerEntry]] = defaultdict(list)
        personal_credits_by_order: dict[str, list[PersonalLedgerEntry]] = defaultdict(list)
        company_debt_recoveries_by_order: dict[
            str, list[CompanyPointLedgerEntry]
        ] = defaultdict(list)
        personal_debt_recoveries_by_order: dict[
            str, list[PersonalLedgerEntry]
        ] = defaultdict(list)
        if order_ids:
            for lot in session.scalars(
                select(CompanyPointLot).where(CompanyPointLot.payment_order_id.in_(order_ids))
            ).all():
                assert lot.payment_order_id is not None
                company_lots_by_order[lot.payment_order_id].append(lot)
            for lot in session.scalars(
                select(PersonalPointLot).where(PersonalPointLot.payment_order_id.in_(order_ids))
            ).all():
                assert lot.payment_order_id is not None
                personal_lots_by_order[lot.payment_order_id].append(lot)
            for entry in session.scalars(
                select(CompanyPointLedgerEntry).where(
                    CompanyPointLedgerEntry.payment_order_id.in_(order_ids),
                    CompanyPointLedgerEntry.kind == PointLedgerKind.CREDIT,
                )
            ).all():
                assert entry.payment_order_id is not None
                company_credits_by_order[entry.payment_order_id].append(entry)
            for entry in session.scalars(
                select(PersonalLedgerEntry).where(
                    PersonalLedgerEntry.payment_order_id.in_(order_ids),
                    PersonalLedgerEntry.kind == LedgerKind.RECHARGE,
                )
            ).all():
                assert entry.payment_order_id is not None
                personal_credits_by_order[entry.payment_order_id].append(entry)
            for entry in session.scalars(
                select(CompanyPointLedgerEntry).where(
                    CompanyPointLedgerEntry.payment_order_id.in_(order_ids),
                    CompanyPointLedgerEntry.kind == PointLedgerKind.DEBT_RECOVERY,
                )
            ).all():
                assert entry.payment_order_id is not None
                company_debt_recoveries_by_order[entry.payment_order_id].append(entry)
            for entry in session.scalars(
                select(PersonalLedgerEntry).where(
                    PersonalLedgerEntry.payment_order_id.in_(order_ids),
                    PersonalLedgerEntry.kind == LedgerKind.DEBT_RECOVERY,
                )
            ).all():
                assert entry.payment_order_id is not None
                personal_debt_recoveries_by_order[entry.payment_order_id].append(entry)

        debt_allocations_by_capture: dict[
            str, list[PaymentDisputeDebtRecoveryAllocation]
        ] = defaultdict(list)
        capture_transaction_ids = [
            transaction.id
            for transactions in captures_by_order.values()
            for transaction in transactions
        ]
        if capture_transaction_ids:
            for allocation in session.scalars(
                select(PaymentDisputeDebtRecoveryAllocation).where(
                    PaymentDisputeDebtRecoveryAllocation.recovery_payment_transaction_id.in_(
                        capture_transaction_ids
                    )
                )
            ).all():
                debt_allocations_by_capture[
                    allocation.recovery_payment_transaction_id
                ].append(allocation)

        adjustment_kinds = (
            PaymentTransactionKind.REFUND,
            PaymentTransactionKind.CHARGEBACK,
            PaymentTransactionKind.DISPUTE_REVERSAL,
        )
        adjustment_statement = (
            select(PaymentTransaction, PaymentOrder)
            .join(PaymentOrder, PaymentOrder.id == PaymentTransaction.order_id)
            .where(
                PaymentTransaction.kind.in_(adjustment_kinds),
                PaymentTransaction.occurred_at >= start,
                PaymentTransaction.occurred_at < end,
            )
        )
        if provider is not None:
            adjustment_statement = adjustment_statement.where(
                PaymentOrder.provider == provider
            )
        if merchant_account is not None:
            adjustment_statement = adjustment_statement.where(
                PaymentOrder.merchant_account == merchant_account
            )
        adjustment_rows = list(
            session.execute(
                adjustment_statement.order_by(
                    PaymentTransaction.occurred_at, PaymentTransaction.id
                )
            ).all()
        )
        adjustment_transactions = [row[0] for row in adjustment_rows]
        adjustment_orders = {row[1].id: row[1] for row in adjustment_rows}

        operational_statement = (
            select(PaymentTransaction, PaymentOrder)
            .join(PaymentOrder, PaymentOrder.id == PaymentTransaction.order_id)
            .where(
                PaymentTransaction.kind.in_(
                    (PaymentTransactionKind.FEE, PaymentTransactionKind.PAYOUT)
                ),
                PaymentTransaction.occurred_at >= start,
                PaymentTransaction.occurred_at < end,
            )
        )
        if provider is not None:
            operational_statement = operational_statement.where(
                PaymentOrder.provider == provider
            )
        if merchant_account is not None:
            operational_statement = operational_statement.where(
                PaymentOrder.merchant_account == merchant_account
            )
        operational_rows = list(session.execute(operational_statement).all())
        operational_transactions = [row[0] for row in operational_rows]
        operational_orders = {row[1].id: row[1] for row in operational_rows}
        fee_transactions_by_order: dict[str, list[PaymentTransaction]] = defaultdict(list)
        for transaction in operational_transactions:
            if transaction.kind == PaymentTransactionKind.FEE:
                fee_transactions_by_order[transaction.order_id].append(transaction)

        invoice_orders: dict[str, PaymentOrder] = {
            order.id: order
            for order in captured_orders
            if order.purpose == PaymentPurpose.INVOICE_PAYMENT
        }
        invoice_orders.update(
            {
                order_id: order
                for order_id, order in adjustment_orders.items()
                if order.purpose == PaymentPurpose.INVOICE_PAYMENT
            }
        )
        invoices_by_id = {
            invoice.id: invoice
            for invoice in session.scalars(
                select(CompanyInvoice).order_by(CompanyInvoice.id)
            ).all()
        }

        invoice_transactions: list[PaymentTransaction] = [
            transaction
            for order_id, transactions in captures_by_order.items()
            if order_id in invoice_orders
            for transaction in transactions
        ]
        invoice_transactions.extend(
            transaction
            for transaction in adjustment_transactions
            if adjustment_orders[transaction.order_id].purpose
            == PaymentPurpose.INVOICE_PAYMENT
        )
        invoice_transaction_ids = [item.id for item in invoice_transactions]
        ar_by_transaction: dict[str, list[AccountsReceivableLedgerEntry]] = (
            defaultdict(list)
        )
        if invoice_transaction_ids:
            for entry in session.scalars(
                select(AccountsReceivableLedgerEntry).where(
                    AccountsReceivableLedgerEntry.payment_transaction_id.in_(
                        invoice_transaction_ids
                    )
                )
            ).all():
                assert entry.payment_transaction_id is not None
                ar_by_transaction[entry.payment_transaction_id].append(entry)
        ar_by_invoice: dict[str, list[AccountsReceivableLedgerEntry]] = defaultdict(list)
        all_ar_entries = list(session.scalars(select(AccountsReceivableLedgerEntry)).all())
        for entry in all_ar_entries:
            if entry.invoice_id not in invoices_by_id:
                issue(
                    "ACCOUNTS_RECEIVABLE_ENTRY_ORPHANED",
                    ReconciliationDimensionStatus.UNATTRIBUTED,
                    "accounts_receivable_ledger_entry",
                    entry.id,
                    actual=entry.debit_cents - entry.credit_cents,
                    invoice_id=entry.invoice_id,
                    payment_transaction_id=entry.payment_transaction_id,
                )
                continue
            ar_by_invoice[entry.invoice_id].append(entry)
            if entry.kind != "INVOICE_ISSUED" and entry.payment_transaction_id is None:
                issue(
                    "ACCOUNTS_RECEIVABLE_PAYMENT_FACT_MISSING",
                    ReconciliationDimensionStatus.MISSING,
                    "accounts_receivable_ledger_entry",
                    entry.id,
                    actual=entry.debit_cents - entry.credit_cents,
                    invoice_id=entry.invoice_id,
                    entry_kind=entry.kind,
                )
        # Rebuild the transaction index from the complete invoice/AR population,
        # not only transactions that happened in this run period.
        ar_by_transaction.clear()
        for entries in ar_by_invoice.values():
            for entry in entries:
                if entry.payment_transaction_id is not None:
                    ar_by_transaction[entry.payment_transaction_id].append(entry)

        settlement_lines: list[PaymentSettlementEntry] = []
        settlement_by_transaction: dict[
            tuple[str, str, str, str], list[PaymentSettlementEntry]
        ] = defaultdict(list)
        if provider_statement_available:
            batch_ids = [batch.id for batch in settlement_batches]
            statement = select(PaymentSettlementEntry).where(
                PaymentSettlementEntry.batch_id.in_(batch_ids)
            )
            settlement_lines = list(
                session.scalars(
                    statement.order_by(
                        PaymentSettlementEntry.occurred_at,
                        PaymentSettlementEntry.id,
                    )
                ).all()
            )
            lines_by_batch: dict[str, list[PaymentSettlementEntry]] = defaultdict(list)
            for line in settlement_lines:
                lines_by_batch[line.batch_id].append(line)
                if line.provider_transaction_id:
                    settlement_by_transaction[
                        (
                            line.provider,
                            line.merchant_account,
                            line.provider_transaction_id,
                            line.line_type,
                        )
                    ].append(line)
            for batch in settlement_batches:
                try:
                    validate_archived_statement(batch)
                except ConflictError as exc:
                    issue(
                        "PAYMENT_SETTLEMENT_SOURCE_DOCUMENT_INVALID",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_settlement_batch",
                        batch.id,
                        reason=str(exc),
                    )
                # An uploaded digest proves retained-byte integrity, not who
                # issued the statement. No signature/API verifier is connected
                # yet, so metadata labels cannot turn this into trusted proof.
                issue(
                    "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
                    ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
                    "payment_settlement_batch",
                    batch.id,
                    severity="warning",
                    verification_method=batch.verification_method,
                    source_kind=batch.source_kind.value,
                )
                rows = lines_by_batch.get(batch.id, [])
                actual_lines_sha256 = _payment_lines_sha256(rows)
                control_matches = (
                    len(rows) == batch.line_count
                    and sum(row.gross_amount_cents for row in rows)
                    == batch.gross_total_cents
                    and sum(row.fee_amount_cents for row in rows)
                    == batch.fee_total_cents
                    and sum(row.net_amount_cents for row in rows)
                    == batch.net_total_cents
                    and actual_lines_sha256 == batch.lines_sha256
                )
                row_scope_matches = all(
                    row.provider == batch.provider
                    and row.merchant_account == batch.merchant_account
                    and row.source_document_sha256
                    == batch.source_document_sha256
                    and start <= _database_utc(row.occurred_at) < end
                    for row in rows
                )
                if not control_matches or not row_scope_matches:
                    issue(
                        "PAYMENT_SETTLEMENT_BATCH_CONTROL_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_settlement_batch",
                        batch.id,
                        expected=batch.line_count,
                        actual=len(rows),
                        expected_lines_sha256=batch.lines_sha256,
                        actual_lines_sha256=actual_lines_sha256,
                        expected_gross_total_cents=batch.gross_total_cents,
                        actual_gross_total_cents=sum(
                            row.gross_amount_cents for row in rows
                        ),
                        expected_fee_total_cents=batch.fee_total_cents,
                        actual_fee_total_cents=sum(
                            row.fee_amount_cents for row in rows
                        ),
                        expected_net_total_cents=batch.net_total_cents,
                        actual_net_total_cents=sum(
                            row.net_amount_cents for row in rows
                        ),
                    )
        else:
            issue(
                "PAYMENT_SETTLEMENT_SOURCE_UNAVAILABLE",
                ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
                "payment_settlement_source",
                provider,
                severity="warning",
                provider=provider,
                merchant_account=merchant_account,
            )

        statement_transaction_keys: set[tuple[str, str, str, str]] = set()
        resolved_invoice_by_order: dict[str, CompanyInvoice | None] = {}

        def invoice_for_order(order: PaymentOrder) -> CompanyInvoice | None:
            if order.id in resolved_invoice_by_order:
                return resolved_invoice_by_order[order.id]
            invoice = (
                invoices_by_id.get(order.purpose_reference_id)
                if order.purpose_reference_id is not None
                else None
            )
            resolved_invoice_by_order[order.id] = invoice
            if invoice is None:
                issue(
                    "INVOICE_PAYMENT_REFERENCE_MISSING",
                    ReconciliationDimensionStatus.MISSING,
                    "payment_order",
                    order.id,
                    expected=1,
                    actual=0,
                    purpose_reference_id=order.purpose_reference_id,
                )
                return None
            if (
                order.company_id is None
                or invoice.company_id != order.company_id
                or invoice.currency != order.currency
            ):
                issue(
                    "INVOICE_PAYMENT_SCOPE_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_order",
                    order.id,
                    invoice_id=invoice.id,
                    expected_company_id=order.company_id,
                    actual_company_id=invoice.company_id,
                    expected_currency=order.currency,
                    actual_currency=invoice.currency,
                )
            return invoice

        def check_invoice_ar_entry(
            *,
            order: PaymentOrder,
            transaction: PaymentTransaction,
            expected_direction: str,
        ) -> None:
            invoice = invoice_for_order(order)
            rows = ar_by_transaction.get(transaction.id, [])
            if len(rows) != 1:
                issue(
                    "INVOICE_AR_ENTRY_MISSING"
                    if not rows
                    else "INVOICE_AR_ENTRY_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not rows
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_transaction",
                    transaction.id,
                    expected=1,
                    actual=len(rows),
                    transaction_kind=transaction.kind.value,
                    expected_direction=expected_direction,
                )
                return
            entry = rows[0]
            expected_invoice_id = invoice.id if invoice is not None else None
            if (
                invoice is None
                or entry.invoice_id != expected_invoice_id
                or entry.company_id != order.company_id
            ):
                issue(
                    "INVOICE_AR_ENTRY_SCOPE_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "accounts_receivable_ledger_entry",
                    entry.id,
                    transaction_kind=transaction.kind.value,
                    expected_invoice_id=expected_invoice_id,
                    actual_invoice_id=entry.invoice_id,
                    expected_company_id=order.company_id,
                    actual_company_id=entry.company_id,
                )
            actual_amount = (
                entry.credit_cents
                if expected_direction == "credit"
                else entry.debit_cents
            )
            opposite_amount = (
                entry.debit_cents
                if expected_direction == "credit"
                else entry.credit_cents
            )
            if actual_amount != transaction.amount_cents or opposite_amount != 0:
                issue(
                    "INVOICE_AR_ENTRY_AMOUNT_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "accounts_receivable_ledger_entry",
                    entry.id,
                    expected=transaction.amount_cents,
                    actual=actual_amount,
                    transaction_kind=transaction.kind.value,
                    expected_direction=expected_direction,
                    opposite_direction_amount=opposite_amount,
                )

        def check_point_adjustment(
            *, order: PaymentOrder, transaction: PaymentTransaction
        ) -> None:
            if order.purpose != PaymentPurpose.POINT_PURCHASE:
                return
            expected_kind: PointLedgerKind | LedgerKind
            expected_points: int
            reference_field: Any
            reference_id: str | None
            if transaction.kind == PaymentTransactionKind.REFUND:
                refund = (
                    session.get(PaymentRefund, transaction.refund_id)
                    if transaction.refund_id
                    else None
                )
                if (
                    refund is None
                    or refund.order_id != order.id
                    or refund.provider != transaction.provider
                    or refund.status != PaymentRefundStatus.SUCCEEDED
                    or refund.amount_cents != transaction.amount_cents
                    or refund.currency != transaction.currency
                ):
                    issue(
                        "POINT_REFUND_FACT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_transaction",
                        transaction.id,
                        refund_id=transaction.refund_id,
                    )
                    return
                expected_points = refund.points
                reference_field = (
                    CompanyPointLedgerEntry.payment_refund_id
                    if order.company_id is not None
                    else PersonalLedgerEntry.payment_refund_id
                )
                reference_id = refund.id
                expected_kind = (
                    PointLedgerKind.REFUND_SETTLE
                    if order.company_id is not None
                    else LedgerKind.REFUND_SETTLE
                )
            else:
                dispute = (
                    session.get(PaymentDispute, transaction.dispute_id)
                    if transaction.dispute_id
                    else None
                )
                expected_statuses = (
                    {PaymentDisputeStatus.OPEN, PaymentDisputeStatus.LOST, PaymentDisputeStatus.WON}
                    if transaction.kind == PaymentTransactionKind.CHARGEBACK
                    else {PaymentDisputeStatus.WON}
                )
                if (
                    dispute is None
                    or dispute.order_id != order.id
                    or dispute.provider != transaction.provider
                    or dispute.status not in expected_statuses
                    or dispute.amount_cents != transaction.amount_cents
                    or dispute.currency != transaction.currency
                    or dispute.points
                    != dispute.recovered_available_points + dispute.debt_points
                ):
                    issue(
                        "POINT_DISPUTE_FACT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_transaction",
                        transaction.id,
                        transaction_kind=transaction.kind.value,
                        dispute_id=transaction.dispute_id,
                    )
                    return
                expected_points = dispute.points
                reference_field = (
                    CompanyPointLedgerEntry.payment_dispute_id
                    if order.company_id is not None
                    else PersonalLedgerEntry.payment_dispute_id
                )
                reference_id = dispute.id
                expected_kind = (
                    PointLedgerKind.CHARGEBACK
                    if order.company_id is not None
                    and transaction.kind == PaymentTransactionKind.CHARGEBACK
                    else PointLedgerKind.DISPUTE_REVERSAL
                    if order.company_id is not None
                    else LedgerKind.CHARGEBACK
                    if transaction.kind == PaymentTransactionKind.CHARGEBACK
                    else LedgerKind.DISPUTE_REVERSAL
                )
                if transaction.kind == PaymentTransactionKind.DISPUTE_REVERSAL:
                    allocations = list(
                        session.scalars(
                            select(PaymentDisputeDebtRecoveryAllocation).where(
                                PaymentDisputeDebtRecoveryAllocation.dispute_id
                                == dispute.id
                            )
                        ).all()
                    )
                    reversals = list(
                        session.scalars(
                            select(PaymentDisputeDebtRecoveryReversal).where(
                                PaymentDisputeDebtRecoveryReversal.dispute_id
                                == dispute.id
                            )
                        ).all()
                    )
                    reversals_by_allocation: dict[
                        str, list[PaymentDisputeDebtRecoveryReversal]
                    ] = defaultdict(list)
                    for reversal in reversals:
                        reversals_by_allocation[reversal.allocation_id].append(reversal)
                    reversal_shape_valid = len(reversals) == len(allocations)
                    linked_ledgers: set[str] = set()
                    linked_lots: set[str] = set()
                    for allocation in allocations:
                        matching_reversals = reversals_by_allocation.get(
                            allocation.id, []
                        )
                        if len(matching_reversals) != 1:
                            reversal_shape_valid = False
                            continue
                        reversal = matching_reversals[0]
                        if (
                            reversal.dispute_id != dispute.id
                            or reversal.restored_points
                            != allocation.recovered_points
                        ):
                            reversal_shape_valid = False
                        if order.company_id is not None:
                            if (
                                reversal.company_ledger_entry_id is None
                                or reversal.company_point_lot_id is None
                                or reversal.personal_ledger_entry_id is not None
                                or reversal.personal_point_lot_id is not None
                            ):
                                reversal_shape_valid = False
                            else:
                                linked_ledgers.add(reversal.company_ledger_entry_id)
                                linked_lots.add(reversal.company_point_lot_id)
                        else:
                            if (
                                reversal.personal_ledger_entry_id is None
                                or reversal.personal_point_lot_id is None
                                or reversal.company_ledger_entry_id is not None
                                or reversal.company_point_lot_id is not None
                            ):
                                reversal_shape_valid = False
                            else:
                                linked_ledgers.add(reversal.personal_ledger_entry_id)
                                linked_lots.add(reversal.personal_point_lot_id)
                    if allocations and (
                        len(linked_ledgers) != 1 or len(linked_lots) != 1
                    ):
                        reversal_shape_valid = False
                    recovered_after_dispute = sum(
                        item.recovered_points for item in allocations
                    )
                    if sum(
                        item.restored_points for item in reversals
                    ) != recovered_after_dispute:
                        reversal_shape_valid = False
                    if linked_ledgers and linked_lots:
                        ledger_id = next(iter(linked_ledgers))
                        lot_id = next(iter(linked_lots))
                        if order.company_id is not None:
                            reversal_ledger = session.get(
                                CompanyPointLedgerEntry, ledger_id
                            )
                            reversal_lot = session.get(CompanyPointLot, lot_id)
                            linked_scope_valid = (
                                reversal_ledger is not None
                                and reversal_ledger.company_id == order.company_id
                                and reversal_ledger.kind
                                == PointLedgerKind.DISPUTE_REVERSAL
                                and reversal_ledger.payment_dispute_id == dispute.id
                                and reversal_lot is not None
                                and reversal_lot.company_id == order.company_id
                            )
                        else:
                            reversal_ledger = session.get(PersonalLedgerEntry, ledger_id)
                            reversal_lot = session.get(PersonalPointLot, lot_id)
                            linked_scope_valid = (
                                reversal_ledger is not None
                                and reversal_ledger.workspace_id
                                == order.personal_workspace_id
                                and reversal_ledger.kind == LedgerKind.DISPUTE_REVERSAL
                                and reversal_ledger.payment_dispute_id == dispute.id
                                and reversal_lot is not None
                                and reversal_lot.workspace_id
                                == order.personal_workspace_id
                            )
                        if (
                            not linked_scope_valid
                            or reversal_lot.source_kind
                            != PointLotSourceKind.COMPENSATION
                            or reversal_lot.original_points
                            != dispute.recovered_available_points
                            + recovered_after_dispute
                        ):
                            reversal_shape_valid = False
                    if not reversal_shape_valid:
                        issue(
                            "POINT_DISPUTE_DEBT_REVERSAL_MISMATCH",
                            ReconciliationDimensionStatus.MISMATCH,
                            "payment_dispute",
                            dispute.id,
                            expected=recovered_after_dispute,
                            actual=sum(item.restored_points for item in reversals),
                        )

            ledger_model: Any = (
                CompanyPointLedgerEntry
                if order.company_id is not None
                else PersonalLedgerEntry
            )
            rows = list(
                session.scalars(
                    select(ledger_model).where(
                        reference_field == reference_id,
                        ledger_model.kind == expected_kind,
                    )
                ).all()
            )
            if len(rows) != 1:
                issue(
                    "POINT_ADJUSTMENT_LEDGER_MISSING"
                    if not rows
                    else "POINT_ADJUSTMENT_LEDGER_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not rows
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_transaction",
                    transaction.id,
                    expected=1,
                    actual=len(rows),
                    transaction_kind=transaction.kind.value,
                )
                return
            ledger = rows[0]
            scope_matches = (
                order.company_id is not None
                and ledger.company_id == order.company_id
                or order.personal_workspace_id is not None
                and ledger.workspace_id == order.personal_workspace_id
            )
            if (
                not scope_matches
                or ledger.payment_order_id != order.id
                or ledger.kind != expected_kind
                or ledger.amount_points != expected_points
            ):
                issue(
                    "POINT_ADJUSTMENT_LEDGER_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_ledger_entry",
                    ledger.id,
                    expected=expected_points,
                    actual=ledger.amount_points,
                    expected_kind=expected_kind.value,
                    actual_kind=ledger.kind.value,
                )

        capture_amount_cents = 0
        purchased_lot_count = 0
        credit_ledger_count = 0
        debt_recovery_allocation_count = 0
        debt_recovered_points_total = 0
        granted_points_total = 0
        for order in captured_orders:
            captures = captures_by_order.get(order.id, [])
            capture_amount_cents += sum(item.amount_cents for item in captures)
            if order.captured_at is None:
                issue(
                    "PAYMENT_CAPTURE_TIMESTAMP_MISSING",
                    ReconciliationDimensionStatus.MISSING,
                    "payment_order",
                    order.id,
                    expected=1,
                    actual=0,
                )
            if len(captures) != 1:
                issue(
                    "PAYMENT_CAPTURE_MISSING" if not captures else "PAYMENT_CAPTURE_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not captures
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_order",
                    order.id,
                    expected=1,
                    actual=len(captures),
                )
                capture = None
            else:
                capture = captures[0]
                capture_key = (
                    capture.provider,
                    order.merchant_account,
                    capture.provider_transaction_id,
                    "capture",
                )
                statement_transaction_keys.add(capture_key)
                if capture.provider != order.provider or capture.currency != order.currency:
                    issue(
                        "PAYMENT_CAPTURE_SCOPE_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_transaction",
                        capture.id,
                        provider=order.provider,
                        actual_provider=capture.provider,
                        currency=order.currency,
                        actual_currency=capture.currency,
                    )
                if capture.amount_cents != order.captured_amount_cents:
                    issue(
                        "PAYMENT_CAPTURE_TOTAL_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_order",
                        order.id,
                        expected=order.captured_amount_cents,
                        actual=capture.amount_cents,
                    )
                if capture.amount_cents != order.amount_cents:
                    issue(
                        "PAYMENT_ORDER_AMOUNT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_order",
                        order.id,
                        expected=order.amount_cents,
                        actual=capture.amount_cents,
                    )
                if provider_statement_available:
                    lines = settlement_by_transaction.get(capture_key, [])
                    separate_fee_transactions = fee_transactions_by_order.get(
                        order.id, []
                    )
                    expected_inline_fee = (
                        0 if separate_fee_transactions else order.fee_amount_cents
                    )
                    if len(lines) != 1:
                        issue(
                            "PAYMENT_SETTLEMENT_LINE_MISSING"
                            if not lines
                            else "PAYMENT_SETTLEMENT_LINE_DUPLICATE",
                            ReconciliationDimensionStatus.MISSING
                            if not lines
                            else ReconciliationDimensionStatus.DUPLICATE,
                            "payment_transaction",
                            capture.id,
                            expected=1,
                            actual=len(lines),
                            provider_transaction_id=capture.provider_transaction_id,
                        )
                    elif (
                        lines[0].gross_amount_cents != capture.amount_cents
                        or lines[0].fee_amount_cents != expected_inline_fee
                        or lines[0].net_amount_cents
                        != capture.amount_cents - expected_inline_fee
                        or lines[0].currency != capture.currency
                    ):
                        issue(
                            "PAYMENT_SETTLEMENT_AMOUNT_MISMATCH",
                            ReconciliationDimensionStatus.MISMATCH,
                            "payment_settlement_entry",
                            lines[0].id,
                            expected=capture.amount_cents,
                            actual=lines[0].gross_amount_cents,
                            expected_currency=capture.currency,
                            actual_currency=lines[0].currency,
                            expected_fee_amount_cents=expected_inline_fee,
                            actual_fee_amount_cents=lines[0].fee_amount_cents,
                            expected_net_amount_cents=(
                                capture.amount_cents - expected_inline_fee
                            ),
                            actual_net_amount_cents=lines[0].net_amount_cents,
                        )

                separate_fee_total = sum(
                    item.amount_cents
                    for item in fee_transactions_by_order.get(order.id, ())
                )
                if (
                    fee_transactions_by_order.get(order.id)
                    and separate_fee_total != order.fee_amount_cents
                ):
                    issue(
                        "PAYMENT_ORDER_FEE_PROJECTION_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_order",
                        order.id,
                        expected=order.fee_amount_cents,
                        actual=separate_fee_total,
                    )

            if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
                invoice_for_order(order)
                # Invoice payments settle accounts receivable.  They must never
                # mint a PURCHASED point lot or a point credit ledger entry.
                continue

            if order.amount_cents != order.points * 10:
                issue(
                    "PAYMENT_POINT_ANCHOR_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_order",
                    order.id,
                    expected=order.points * 10,
                    actual=order.amount_cents,
                )

            allocations = (
                debt_allocations_by_capture.get(capture.id, [])
                if capture is not None
                else []
            )
            debt_recovered_points = sum(
                allocation.recovered_points for allocation in allocations
            )
            debt_recovery_allocation_count += len(allocations)
            debt_recovered_points_total += debt_recovered_points
            for allocation in allocations:
                dispute = session.get(PaymentDispute, allocation.dispute_id)
                disputed_order = (
                    session.get(PaymentOrder, dispute.order_id)
                    if dispute is not None
                    else None
                )
                allocation_scope_matches = (
                    allocation.company_id == order.company_id
                    and allocation.personal_workspace_id
                    == order.personal_workspace_id
                )
                dispute_scope_matches = (
                    disputed_order is not None
                    and disputed_order.company_id == order.company_id
                    and disputed_order.personal_workspace_id
                    == order.personal_workspace_id
                )
                if (
                    not allocation_scope_matches
                    or dispute is None
                    or not dispute_scope_matches
                    or allocation.recovered_points > dispute.debt_points
                ):
                    issue(
                        "PAYMENT_DEBT_RECOVERY_ALLOCATION_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_dispute_debt_recovery_allocation",
                        allocation.id,
                        recovery_order_id=order.id,
                        dispute_id=allocation.dispute_id,
                        recovered_points=allocation.recovered_points,
                    )
            debt_ledgers = [
                *company_debt_recoveries_by_order.get(order.id, []),
                *personal_debt_recoveries_by_order.get(order.id, []),
            ]
            expected_debt_ledger_count = 1 if debt_recovered_points else 0
            if len(debt_ledgers) != expected_debt_ledger_count:
                issue(
                    "PAYMENT_DEBT_RECOVERY_LEDGER_MISSING"
                    if not debt_ledgers and expected_debt_ledger_count
                    else "PAYMENT_DEBT_RECOVERY_LEDGER_UNEXPECTED"
                    if debt_ledgers and not expected_debt_ledger_count
                    else "PAYMENT_DEBT_RECOVERY_LEDGER_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not debt_ledgers and expected_debt_ledger_count
                    else ReconciliationDimensionStatus.MISMATCH
                    if debt_ledgers and not expected_debt_ledger_count
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_order",
                    order.id,
                    expected=expected_debt_ledger_count,
                    actual=len(debt_ledgers),
                    allocated_debt_recovery_points=debt_recovered_points,
                )
            elif debt_ledgers:
                debt_ledger = debt_ledgers[0]
                debt_scope_matches = (
                    isinstance(debt_ledger, CompanyPointLedgerEntry)
                    and order.company_id is not None
                    and debt_ledger.company_id == order.company_id
                    or isinstance(debt_ledger, PersonalLedgerEntry)
                    and order.personal_workspace_id is not None
                    and debt_ledger.workspace_id == order.personal_workspace_id
                )
                if (
                    not debt_scope_matches
                    or debt_ledger.amount_points != debt_recovered_points
                    or debt_ledger.available_delta_points != 0
                    or debt_ledger.reserved_delta_points != 0
                    or debt_ledger.reversal_reserved_delta_points != 0
                    or debt_ledger.debt_delta_points != -debt_recovered_points
                ):
                    issue(
                        "PAYMENT_DEBT_RECOVERY_LEDGER_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_ledger_entry",
                        debt_ledger.id,
                        expected=debt_recovered_points,
                        actual=debt_ledger.amount_points,
                        debt_delta_points=debt_ledger.debt_delta_points,
                    )

            granted_points = order.points - debt_recovered_points
            if granted_points < 0:
                issue(
                    "PAYMENT_POINT_GRANT_CONSERVATION_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_order",
                    order.id,
                    expected=order.points,
                    actual=debt_recovered_points,
                )
                granted_points = 0
            granted_points_total += granted_points
            lots = [
                *company_lots_by_order.get(order.id, []),
                *personal_lots_by_order.get(order.id, []),
            ]
            purchased_lot_count += len(lots)
            expected_lot_count = 1 if granted_points else 0
            lot = None
            if len(lots) != expected_lot_count:
                issue(
                    "PURCHASED_POINT_LOT_MISSING"
                    if not lots and expected_lot_count
                    else "PURCHASED_POINT_LOT_UNEXPECTED"
                    if lots and not expected_lot_count
                    else "PURCHASED_POINT_LOT_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not lots and expected_lot_count
                    else ReconciliationDimensionStatus.MISMATCH
                    if lots and not expected_lot_count
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_order",
                    order.id,
                    expected=expected_lot_count,
                    actual=len(lots),
                    granted_points=granted_points,
                    debt_recovered_points=debt_recovered_points,
                )
            else:
                lot = lots[0] if lots else None
            if lot is not None:
                scope_matches = (
                    isinstance(lot, CompanyPointLot)
                    and order.company_id is not None
                    and lot.company_id == order.company_id
                    or isinstance(lot, PersonalPointLot)
                    and order.personal_workspace_id is not None
                    and lot.workspace_id == order.personal_workspace_id
                )
                if not scope_matches or lot.source_kind != PointLotSourceKind.PURCHASED:
                    issue(
                        "PURCHASED_POINT_LOT_SCOPE_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "point_lot",
                        lot.id,
                        order_id=order.id,
                    )
                if (
                    lot.original_points != granted_points
                    or lot.cash_basis_cents != granted_points * 10
                    or lot.receivable_basis_cents != 0
                    or lot.subsidy_cents != 0
                ):
                    issue(
                        "PURCHASED_POINT_LOT_VALUE_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "point_lot",
                        lot.id,
                        expected=granted_points,
                        actual=lot.original_points,
                        expected_cash_basis_cents=granted_points * 10,
                        actual_cash_basis_cents=lot.cash_basis_cents,
                        receivable_basis_cents=lot.receivable_basis_cents,
                        subsidy_cents=lot.subsidy_cents,
                    )

            credits = [
                *company_credits_by_order.get(order.id, []),
                *personal_credits_by_order.get(order.id, []),
            ]
            credit_ledger_count += len(credits)
            expected_credit_count = 1 if granted_points else 0
            credit = None
            if len(credits) != expected_credit_count:
                issue(
                    "PURCHASE_CREDIT_LEDGER_MISSING"
                    if not credits and expected_credit_count
                    else "PURCHASE_CREDIT_LEDGER_UNEXPECTED"
                    if credits and not expected_credit_count
                    else "PURCHASE_CREDIT_LEDGER_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not credits and expected_credit_count
                    else ReconciliationDimensionStatus.MISMATCH
                    if credits and not expected_credit_count
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_order",
                    order.id,
                    expected=expected_credit_count,
                    actual=len(credits),
                    granted_points=granted_points,
                    debt_recovered_points=debt_recovered_points,
                )
            else:
                credit = credits[0] if credits else None
            if credit is not None:
                credit_scope_matches = (
                    isinstance(credit, CompanyPointLedgerEntry)
                    and order.company_id is not None
                    and credit.company_id == order.company_id
                    or isinstance(credit, PersonalLedgerEntry)
                    and order.personal_workspace_id is not None
                    and credit.workspace_id == order.personal_workspace_id
                )
                if not credit_scope_matches:
                    issue(
                        "PURCHASE_CREDIT_LEDGER_SCOPE_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_ledger_entry",
                        credit.id,
                        order_id=order.id,
                    )
                if credit.amount_points != granted_points:
                    issue(
                        "PURCHASE_CREDIT_LEDGER_AMOUNT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_ledger_entry",
                        credit.id,
                        expected=granted_points,
                        actual=credit.amount_points,
                    )
            if granted_points + debt_recovered_points != order.points:
                issue(
                    "PAYMENT_POINT_GRANT_CONSERVATION_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_order",
                    order.id,
                    expected=order.points,
                    actual=granted_points + debt_recovered_points,
                )

        for transaction in adjustment_transactions:
            order = adjustment_orders[transaction.order_id]
            expected_line_type = transaction.kind.value
            statement_key = (
                transaction.provider,
                order.merchant_account,
                transaction.provider_transaction_id,
                expected_line_type,
            )
            statement_transaction_keys.add(statement_key)
            if transaction.provider != order.provider or transaction.currency != order.currency:
                issue(
                    "PAYMENT_ADJUSTMENT_SCOPE_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_transaction",
                    transaction.id,
                    transaction_kind=transaction.kind.value,
                    expected_provider=order.provider,
                    actual_provider=transaction.provider,
                    expected_currency=order.currency,
                    actual_currency=transaction.currency,
                )
            if provider_statement_available:
                lines = settlement_by_transaction.get(statement_key, [])
                if len(lines) != 1:
                    issue(
                        "PAYMENT_ADJUSTMENT_SETTLEMENT_LINE_MISSING"
                        if not lines
                        else "PAYMENT_ADJUSTMENT_SETTLEMENT_LINE_DUPLICATE",
                        ReconciliationDimensionStatus.MISSING
                        if not lines
                        else ReconciliationDimensionStatus.DUPLICATE,
                        "payment_transaction",
                        transaction.id,
                        expected=1,
                        actual=len(lines),
                        transaction_kind=transaction.kind.value,
                        provider_transaction_id=transaction.provider_transaction_id,
                    )
                else:
                    expected_gross = (
                        transaction.amount_cents
                        if transaction.kind
                        == PaymentTransactionKind.DISPUTE_REVERSAL
                        else -transaction.amount_cents
                    )
                    if (
                        lines[0].gross_amount_cents != expected_gross
                        or lines[0].currency != transaction.currency
                    ):
                        issue(
                            "PAYMENT_ADJUSTMENT_SETTLEMENT_AMOUNT_MISMATCH",
                            ReconciliationDimensionStatus.MISMATCH,
                            "payment_settlement_entry",
                            lines[0].id,
                            expected=expected_gross,
                            actual=lines[0].gross_amount_cents,
                            transaction_kind=transaction.kind.value,
                            expected_currency=transaction.currency,
                            actual_currency=lines[0].currency,
                        )
            if order.purpose == PaymentPurpose.INVOICE_PAYMENT:
                invoice_for_order(order)
            else:
                check_point_adjustment(order=order, transaction=transaction)

        for transaction in operational_transactions:
            order = operational_orders[transaction.order_id]
            line_type = transaction.kind.value
            statement_key = (
                transaction.provider,
                order.merchant_account,
                transaction.provider_transaction_id,
                line_type,
            )
            statement_transaction_keys.add(statement_key)
            lines = settlement_by_transaction.get(statement_key, [])
            if len(lines) != 1:
                issue(
                    "PAYMENT_OPERATIONAL_SETTLEMENT_LINE_MISSING"
                    if not lines
                    else "PAYMENT_OPERATIONAL_SETTLEMENT_LINE_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not lines
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_transaction",
                    transaction.id,
                    expected=1,
                    actual=len(lines),
                    transaction_kind=line_type,
                )
                continue
            line = lines[0]
            expected_gross = (
                0 if transaction.kind == PaymentTransactionKind.FEE else transaction.amount_cents
            )
            expected_fee = (
                transaction.amount_cents
                if transaction.kind == PaymentTransactionKind.FEE
                else 0
            )
            expected_net = expected_gross - expected_fee
            if (
                line.gross_amount_cents != expected_gross
                or line.fee_amount_cents != expected_fee
                or line.net_amount_cents != expected_net
                or line.currency != transaction.currency
                or transaction.provider != order.provider
            ):
                issue(
                    "PAYMENT_OPERATIONAL_SETTLEMENT_AMOUNT_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_settlement_entry",
                    line.id,
                    expected=expected_net,
                    actual=line.net_amount_cents,
                    transaction_kind=line_type,
                    expected_gross_amount_cents=expected_gross,
                    actual_gross_amount_cents=line.gross_amount_cents,
                    expected_fee_amount_cents=expected_fee,
                    actual_fee_amount_cents=line.fee_amount_cents,
                )

        transactional_statement_lines = [
            line
            for line in settlement_lines
            if line.line_type
            in {"capture", "refund", "chargeback", "dispute_reversal"}
        ]
        fee_statement_lines = [
            line for line in settlement_lines if line.line_type == "fee"
        ]
        payout_statement_lines = [
            line for line in settlement_lines if line.line_type == "payout"
        ]
        bank_deposit_lines = [
            line for line in settlement_lines if line.line_type == "bank_deposit"
        ]
        expected_payout_cents = sum(
            line.net_amount_cents
            for line in (*transactional_statement_lines, *fee_statement_lines)
        )
        payout_cents = sum(line.net_amount_cents for line in payout_statement_lines)
        bank_deposit_cents = sum(line.net_amount_cents for line in bank_deposit_lines)
        account_controls: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"expected_payout": 0, "payout": 0, "bank": 0}
        )
        for line in (*transactional_statement_lines, *fee_statement_lines):
            account_controls[(line.provider, line.merchant_account)][
                "expected_payout"
            ] += line.net_amount_cents
        for line in payout_statement_lines:
            account_controls[(line.provider, line.merchant_account)][
                "payout"
            ] += line.net_amount_cents
        for line in bank_deposit_lines:
            account_controls[(line.provider, line.merchant_account)][
                "bank"
            ] += line.net_amount_cents
        for (account_provider, account_merchant), control in account_controls.items():
            if control["expected_payout"] != control["payout"]:
                issue(
                    "PAYMENT_PAYOUT_CONTROL_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_settlement_batch",
                    None,
                    expected=control["expected_payout"],
                    actual=control["payout"],
                    provider=account_provider,
                    merchant_account=account_merchant,
                )
            if control["payout"] != control["bank"]:
                issue(
                    "PAYMENT_BANK_DEPOSIT_CONTROL_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_settlement_batch",
                    None,
                    expected=control["payout"],
                    actual=control["bank"],
                    provider=account_provider,
                    merchant_account=account_merchant,
                )
        payouts_by_reference: dict[
            tuple[str, str, str], list[PaymentSettlementEntry]
        ] = defaultdict(list)
        deposits_by_payout: dict[
            tuple[str, str, str], list[PaymentSettlementEntry]
        ] = defaultdict(list)
        for line in payout_statement_lines:
            assert line.provider_transaction_id is not None
            payouts_by_reference[
                (line.provider, line.merchant_account, line.provider_transaction_id)
            ].append(line)
        for line in bank_deposit_lines:
            deposits_by_payout[
                (line.provider, line.merchant_account, line.related_provider_reference or "")
            ].append(line)
        for payout_key, payouts in payouts_by_reference.items():
            deposits = deposits_by_payout.get(payout_key, [])
            if len(payouts) != 1 or len(deposits) != 1:
                issue(
                    "PAYMENT_PAYOUT_BANK_DEPOSIT_MISSING"
                    if not deposits
                    else "PAYMENT_PAYOUT_BANK_DEPOSIT_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not deposits
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_settlement_entry",
                    payouts[0].id,
                    expected=1,
                    actual=len(deposits),
                    payout_reference=payout_key[2],
                    payout_line_count=len(payouts),
                )
        for line in bank_deposit_lines:
            related = payouts_by_reference.get(
                (line.provider, line.merchant_account, line.related_provider_reference or ""),
                [],
            )
            if len(related) != 1:
                issue(
                    "PAYMENT_BANK_DEPOSIT_PAYOUT_MISSING"
                    if not related
                    else "PAYMENT_BANK_DEPOSIT_PAYOUT_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not related
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "payment_settlement_entry",
                    line.id,
                    expected=1,
                    actual=len(related),
                    related_provider_reference=line.related_provider_reference,
                )
            elif related[0].net_amount_cents != line.net_amount_cents:
                issue(
                    "PAYMENT_BANK_DEPOSIT_PAYOUT_AMOUNT_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "payment_settlement_entry",
                    line.id,
                    expected=related[0].net_amount_cents,
                    actual=line.net_amount_cents,
                    payout_entry_id=related[0].id,
                )

        projected_orders = {
            order.id: order
            for order in (*captured_orders, *adjustment_orders.values())
        }
        if projected_orders:
            projections: dict[str, dict[PaymentTransactionKind, int]] = defaultdict(
                lambda: defaultdict(int)
            )
            for transaction in session.scalars(
                select(PaymentTransaction).where(
                    PaymentTransaction.order_id.in_(projected_orders),
                    PaymentTransaction.kind.in_(adjustment_kinds),
                )
            ).all():
                projections[transaction.order_id][transaction.kind] += (
                    transaction.amount_cents
                )
            for order_id, order in projected_orders.items():
                refund_total = projections[order_id][PaymentTransactionKind.REFUND]
                disputed_total = (
                    projections[order_id][PaymentTransactionKind.CHARGEBACK]
                    - projections[order_id][PaymentTransactionKind.DISPUTE_REVERSAL]
                )
                if order.refunded_amount_cents != refund_total:
                    issue(
                        "PAYMENT_ORDER_REFUND_PROJECTION_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_order",
                        order.id,
                        expected=refund_total,
                        actual=order.refunded_amount_cents,
                    )
                if order.disputed_amount_cents != disputed_total:
                    issue(
                        "PAYMENT_ORDER_DISPUTE_PROJECTION_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "payment_order",
                        order.id,
                        expected=disputed_total,
                        actual=order.disputed_amount_cents,
                    )

        # An accepted task can settle after the final contract period, or
        # arrive after its period was invoiced. Preserve its receivable and
        # surface a durable exception instead of silently abandoning it or
        # rewriting an immutable invoice. This is global AR, not PSP-filtered.
        unbilled_orphan_values = []
        for company_id in session.scalars(select(
            PointLotSettlementValueAllocation.company_id
        ).where(
            PointLotSettlementValueAllocation.company_id.is_not(None),
            PointLotSettlementValueAllocation.receivable_basis_cents > 0,
        ).distinct()):
            unbilled_orphan_values.extend(EnterpriseBillingService.unbilled_receivable_exceptions(
                session, company_id=company_id, as_of=end
            ))
        for item in unbilled_orphan_values:
            issue(
                "INVOICE_LATE_RECEIVABLE_REQUIRES_ACTION",
                ReconciliationDimensionStatus.MISSING,
                "point_lot_settlement_value_allocation",
                item["value_allocation_id"],
                expected=item["receivable_cents"], actual=0,
                **item,
            )

        invoice_projected_paid_cents = 0
        invoice_payment_ar_entries: list[AccountsReceivableLedgerEntry] = []
        for invoice_id in sorted(invoices_by_id):
            invoice = invoices_by_id[invoice_id]
            try:
                cls._validate_invoice_document(session, invoice)
            except ConflictError as exc:
                issue(
                    "INVOICE_DOCUMENT_CHAIN_INVALID",
                    ReconciliationDimensionStatus.MISMATCH,
                    "company_invoice",
                    invoice.id,
                    reason=str(exc),
                )
            invoice_entries = ar_by_invoice.get(invoice.id, [])
            issuance_entries = [
                entry for entry in invoice_entries if entry.kind == "INVOICE_ISSUED"
            ]
            expected_issuance_count = 1 if invoice.total_cents > 0 else 0
            if len(issuance_entries) != expected_issuance_count:
                issue(
                    "INVOICE_AR_ISSUANCE_MISSING"
                    if not issuance_entries and expected_issuance_count
                    else "INVOICE_AR_ISSUANCE_DUPLICATE",
                    ReconciliationDimensionStatus.MISSING
                    if not issuance_entries and expected_issuance_count
                    else ReconciliationDimensionStatus.DUPLICATE,
                    "company_invoice",
                    invoice.id,
                    expected=expected_issuance_count,
                    actual=len(issuance_entries),
                )
            elif issuance_entries and (
                issuance_entries[0].company_id != invoice.company_id
                or issuance_entries[0].debit_cents != invoice.total_cents
                or issuance_entries[0].credit_cents != 0
                or issuance_entries[0].payment_transaction_id is not None
            ):
                issue(
                    "INVOICE_AR_ISSUANCE_AMOUNT_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "accounts_receivable_ledger_entry",
                    issuance_entries[0].id,
                    expected=invoice.total_cents,
                    actual=issuance_entries[0].debit_cents,
                )
            payment_entries = [
                entry
                for entry in invoice_entries
                if entry.payment_transaction_id is not None
            ]
            for entry in payment_entries:
                transaction = session.get(
                    PaymentTransaction, entry.payment_transaction_id
                )
                order = (
                    session.get(PaymentOrder, transaction.order_id)
                    if transaction is not None
                    else None
                )
                if (
                    transaction is None
                    or order is None
                    or order.purpose != PaymentPurpose.INVOICE_PAYMENT
                    or order.purpose_reference_id != invoice.id
                    or order.company_id != invoice.company_id
                    or transaction.kind
                    not in {
                        PaymentTransactionKind.CAPTURE,
                        PaymentTransactionKind.REFUND,
                        PaymentTransactionKind.CHARGEBACK,
                        PaymentTransactionKind.DISPUTE_REVERSAL,
                    }
                ):
                    issue(
                        "INVOICE_AR_PAYMENT_LINKAGE_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "accounts_receivable_ledger_entry",
                        entry.id,
                        payment_transaction_id=entry.payment_transaction_id,
                    )
                    continue
                check_invoice_ar_entry(
                    order=order,
                    transaction=transaction,
                    expected_direction=(
                        "credit"
                        if transaction.kind
                        in {
                            PaymentTransactionKind.CAPTURE,
                            PaymentTransactionKind.DISPUTE_REVERSAL,
                        }
                        else "debit"
                    ),
                )
            invoice_payment_ar_entries.extend(payment_entries)
            projected_paid_cents = sum(
                entry.credit_cents - entry.debit_cents for entry in payment_entries
            )
            invoice_projected_paid_cents += projected_paid_cents
            if invoice.paid_cents != projected_paid_cents:
                issue(
                    "INVOICE_PAID_PROJECTION_MISMATCH",
                    ReconciliationDimensionStatus.MISMATCH,
                    "company_invoice",
                    invoice.id,
                    expected=projected_paid_cents,
                    actual=invoice.paid_cents,
                )
            if invoice.paid_cents < 0 or invoice.paid_cents > invoice.total_cents:
                issue(
                    "INVOICE_PAID_OUT_OF_RANGE",
                    ReconciliationDimensionStatus.MISMATCH,
                    "company_invoice",
                    invoice.id,
                    expected=invoice.total_cents,
                    actual=invoice.paid_cents,
                )

        if provider_statement_available:
            for line in settlement_lines:
                if line.line_type in {"payout", "bank_deposit"}:
                    continue
                if not line.provider_transaction_id:
                    issue(
                        "PAYMENT_SETTLEMENT_LINE_UNATTRIBUTED",
                        ReconciliationDimensionStatus.UNATTRIBUTED,
                        "payment_settlement_entry",
                        line.id,
                        actual=line.gross_amount_cents,
                        provider_line_id=line.provider_line_id,
                    )
                    continue
                key = (
                    line.provider,
                    line.merchant_account,
                    line.provider_transaction_id,
                    line.line_type,
                )
                if key not in statement_transaction_keys:
                    issue(
                        "PAYMENT_SETTLEMENT_LINE_UNATTRIBUTED",
                        ReconciliationDimensionStatus.UNATTRIBUTED,
                        "payment_settlement_entry",
                        line.id,
                        actual=line.gross_amount_cents,
                        provider_transaction_id=line.provider_transaction_id,
                    )

        totals = {
            "source_status": (
                "available" if provider_statement_available else "unavailable"
            ),
            "order_count": len(orders),
            "captured_order_count": len(captured_orders),
            "point_purchase_order_count": sum(
                1
                for order in captured_orders
                if order.purpose == PaymentPurpose.POINT_PURCHASE
            ),
            "invoice_payment_order_count": sum(
                1
                for order in captured_orders
                if order.purpose == PaymentPurpose.INVOICE_PAYMENT
            ),
            "capture_transaction_count": sum(len(value) for value in captures_by_order.values()),
            "capture_amount_cents": capture_amount_cents,
            "invoice_count": len(invoices_by_id),
            "unbilled_receivable_requires_action_count": len(unbilled_orphan_values),
            "unbilled_receivable_requires_action_cents": sum(
                item["receivable_cents"] for item in unbilled_orphan_values
            ),
            "invoice_paid_cents": sum(
                invoice.paid_cents for invoice in invoices_by_id.values()
            ),
            "invoice_projected_paid_cents": invoice_projected_paid_cents,
            "invoice_ar_credit_cents": sum(
                entry.credit_cents for entry in invoice_payment_ar_entries
            ),
            "invoice_ar_debit_cents": sum(
                entry.debit_cents for entry in invoice_payment_ar_entries
            ),
            "adjustment_transaction_count": len(adjustment_transactions),
            "adjustment_amount_cents": sum(
                item.amount_cents for item in adjustment_transactions
            ),
            "operational_transaction_count": len(operational_transactions),
            "settlement_batch_count": len(settlement_batches),
            "settlement_line_count": len(settlement_lines),
            "settlement_gross_amount_cents": sum(
                line.gross_amount_cents for line in settlement_lines
            ),
            "settlement_fee_amount_cents": sum(
                line.fee_amount_cents for line in settlement_lines
            ),
            "settlement_net_amount_cents": sum(
                line.net_amount_cents for line in settlement_lines
            ),
            "expected_payout_cents": expected_payout_cents,
            "payout_cents": payout_cents,
            "bank_deposit_cents": bank_deposit_cents,
            "purchased_lot_count": purchased_lot_count,
            "credit_ledger_count": credit_ledger_count,
            "debt_recovery_allocation_count": debt_recovery_allocation_count,
            "debt_recovered_points": debt_recovered_points_total,
            "granted_points": granted_points_total,
        }
        return _DimensionDraft(
            dimension="cash",
            status=_status_for_exceptions(
                exceptions, source_unavailable=not provider_statement_available
            ),
            totals=totals,
            exceptions=tuple(exceptions),
        )

    @classmethod
    def _points_dimension(cls, session: Session) -> _DimensionDraft:
        exceptions: list[_ExceptionDraft] = []

        def issue(
            code: str,
            status: ReconciliationDimensionStatus,
            entity_type: str,
            entity_id: str | None,
            *,
            expected: int | None = None,
            actual: int | None = None,
            **details: Any,
        ) -> None:
            exceptions.append(
                _ExceptionDraft(
                    dimension="points",
                    code=code,
                    status=status,
                    severity="critical",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    expected_amount=expected,
                    actual_amount=actual,
                    details=details,
                )
            )

        company_wallets = {
            wallet.company_id: wallet
            for wallet in session.scalars(select(CompanyPointWalletAccount)).all()
        }
        personal_wallets = {
            wallet.workspace_id: wallet
            for wallet in session.scalars(select(PersonalWalletAccount)).all()
        }
        company_lots: dict[str, list[CompanyPointLot]] = defaultdict(list)
        for lot in session.scalars(select(CompanyPointLot)).all():
            company_lots[lot.company_id].append(lot)
        personal_lots: dict[str, list[PersonalPointLot]] = defaultdict(list)
        for lot in session.scalars(select(PersonalPointLot)).all():
            personal_lots[lot.workspace_id].append(lot)
        company_ledgers: dict[str, list[CompanyPointLedgerEntry]] = defaultdict(list)
        for entry in session.scalars(select(CompanyPointLedgerEntry)).all():
            company_ledgers[entry.company_id].append(entry)
        personal_ledgers: dict[str, list[PersonalLedgerEntry]] = defaultdict(list)
        for entry in session.scalars(select(PersonalLedgerEntry)).all():
            personal_ledgers[entry.workspace_id].append(entry)

        def check_scope(
            *,
            scope_type: str,
            scope_id: str,
            wallet: Any | None,
            lots: Sequence[Any],
            ledgers: Sequence[Any],
        ) -> None:
            if wallet is None:
                issue(
                    "POINT_WALLET_MISSING",
                    ReconciliationDimensionStatus.MISSING,
                    scope_type,
                    scope_id,
                    expected=1,
                    actual=0,
                )
                return
            lot_available = sum(item.available_points for item in lots)
            lot_reserved = sum(item.reserved_points for item in lots)
            lot_reversal = sum(item.reversal_reserved_points for item in lots)
            ledger_available = sum(item.available_delta_points for item in ledgers)
            ledger_reserved = sum(item.reserved_delta_points for item in ledgers)
            ledger_reversal = sum(item.reversal_reserved_delta_points for item in ledgers)
            ledger_debt = sum(item.debt_delta_points for item in ledgers)
            comparisons = (
                ("POINT_WALLET_AVAILABLE_LOT_MISMATCH", wallet.available_points, lot_available),
                ("POINT_WALLET_RESERVED_LOT_MISMATCH", wallet.reserved_points, lot_reserved),
                (
                    "POINT_WALLET_REVERSAL_LOT_MISMATCH",
                    wallet.reversal_reserved_points,
                    lot_reversal,
                ),
                (
                    "POINT_WALLET_AVAILABLE_LEDGER_MISMATCH",
                    wallet.available_points,
                    ledger_available,
                ),
                (
                    "POINT_WALLET_RESERVED_LEDGER_MISMATCH",
                    wallet.reserved_points,
                    ledger_reserved,
                ),
                (
                    "POINT_WALLET_REVERSAL_LEDGER_MISMATCH",
                    wallet.reversal_reserved_points,
                    ledger_reversal,
                ),
                ("POINT_WALLET_DEBT_LEDGER_MISMATCH", wallet.debt_points, ledger_debt),
            )
            for code, expected, actual in comparisons:
                if expected != actual:
                    issue(
                        code,
                        ReconciliationDimensionStatus.MISMATCH,
                        scope_type,
                        scope_id,
                        expected=expected,
                        actual=actual,
                    )
            for lot in lots:
                components = (
                    lot.available_points
                    + lot.reserved_points
                    + lot.reversal_reserved_points
                    + lot.settled_points
                    + lot.reversed_points
                )
                if components != lot.original_points:
                    issue(
                        "POINT_LOT_CONSERVATION_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "point_lot",
                        lot.id,
                        expected=lot.original_points,
                        actual=components,
                    )
                value_basis = (
                    lot.cash_basis_cents
                    + lot.receivable_basis_cents
                    + lot.subsidy_cents
                )
                if value_basis != lot.original_points * 10:
                    issue(
                        "POINT_LOT_VALUE_BASIS_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "point_lot",
                        lot.id,
                        expected=lot.original_points * 10,
                        actual=value_basis,
                    )
                if (
                    lot.source_kind == PointLotSourceKind.PURCHASED
                    and lot.payment_order_id is None
                ):
                    issue(
                        "PURCHASED_POINT_LOT_PAYMENT_MISSING",
                        ReconciliationDimensionStatus.MISSING,
                        "point_lot",
                        lot.id,
                        expected=1,
                        actual=0,
                    )

        company_scope_ids = sorted(
            set(company_wallets) | set(company_lots) | set(company_ledgers)
        )
        personal_scope_ids = sorted(
            set(personal_wallets) | set(personal_lots) | set(personal_ledgers)
        )
        for company_id in company_scope_ids:
            check_scope(
                scope_type="company",
                scope_id=company_id,
                wallet=company_wallets.get(company_id),
                lots=company_lots.get(company_id, []),
                ledgers=company_ledgers.get(company_id, []),
            )
        for workspace_id in personal_scope_ids:
            check_scope(
                scope_type="personal_workspace",
                scope_id=workspace_id,
                wallet=personal_wallets.get(workspace_id),
                lots=personal_lots.get(workspace_id, []),
                ledgers=personal_ledgers.get(workspace_id, []),
            )

        all_lots: list[Any] = [
            *(item for items in company_lots.values() for item in items),
            *(item for items in personal_lots.values() for item in items),
        ]
        all_wallets: list[Any] = [*company_wallets.values(), *personal_wallets.values()]
        totals = {
            "company_scope_count": len(company_scope_ids),
            "personal_scope_count": len(personal_scope_ids),
            "wallet_count": len(all_wallets),
            "lot_count": len(all_lots),
            "ledger_entry_count": sum(len(items) for items in company_ledgers.values())
            + sum(len(items) for items in personal_ledgers.values()),
            "wallet_available_points": sum(item.available_points for item in all_wallets),
            "wallet_reserved_points": sum(item.reserved_points for item in all_wallets),
            "wallet_reversal_reserved_points": sum(
                item.reversal_reserved_points for item in all_wallets
            ),
            "wallet_debt_points": sum(item.debt_points for item in all_wallets),
            "lot_original_points": sum(item.original_points for item in all_lots),
            "lot_available_points": sum(item.available_points for item in all_lots),
            "lot_reserved_points": sum(item.reserved_points for item in all_lots),
            "lot_reversal_reserved_points": sum(
                item.reversal_reserved_points for item in all_lots
            ),
            "lot_settled_points": sum(item.settled_points for item in all_lots),
            "lot_reversed_points": sum(item.reversed_points for item in all_lots),
        }
        return _DimensionDraft(
            dimension="points",
            status=_status_for_exceptions(exceptions),
            totals=totals,
            exceptions=tuple(exceptions),
        )

    @classmethod
    def _tasks_dimension(
        cls, session: Session, *, start: datetime, end: datetime
    ) -> _DimensionDraft:
        exceptions: list[_ExceptionDraft] = []

        def issue(
            code: str,
            status: ReconciliationDimensionStatus,
            task_id: str,
            *,
            expected: int | None = None,
            actual: int | None = None,
            **details: Any,
        ) -> None:
            exceptions.append(
                _ExceptionDraft(
                    dimension="tasks",
                    code=code,
                    status=status,
                    severity="critical",
                    entity_type="generation_task",
                    entity_id=task_id,
                    expected_amount=expected,
                    actual_amount=actual,
                    details=details,
                )
            )

        tasks = list(
            session.scalars(
                select(GenerationTask)
                .where(
                    GenerationTask.billing_unit == BillingUnit.POINT,
                    GenerationTask.billing_version == 2,
                    GenerationTask.updated_at >= start,
                    GenerationTask.updated_at < end,
                )
                .order_by(GenerationTask.updated_at, GenerationTask.id)
            ).all()
        )
        task_ids = [task.id for task in tasks]
        company_settles: dict[str, list[CompanyPointLedgerEntry]] = defaultdict(list)
        personal_settles: dict[str, list[PersonalLedgerEntry]] = defaultdict(list)
        company_allocations: dict[str, list[TaskPointLotAllocation]] = defaultdict(list)
        personal_allocations: dict[str, list[PersonalTaskPointLotAllocation]] = (
            defaultdict(list)
        )
        value_allocations: dict[str, list[PointLotSettlementValueAllocation]] = (
            defaultdict(list)
        )
        if task_ids:
            for entry in session.scalars(
                select(CompanyPointLedgerEntry).where(
                    CompanyPointLedgerEntry.task_id.in_(task_ids),
                    CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE,
                )
            ).all():
                assert entry.task_id is not None
                company_settles[entry.task_id].append(entry)
            for entry in session.scalars(
                select(PersonalLedgerEntry).where(
                    PersonalLedgerEntry.task_id.in_(task_ids),
                    PersonalLedgerEntry.kind == LedgerKind.SETTLE,
                )
            ).all():
                assert entry.task_id is not None
                personal_settles[entry.task_id].append(entry)
            for allocation in session.scalars(
                select(TaskPointLotAllocation).where(
                    TaskPointLotAllocation.task_id.in_(task_ids)
                )
            ).all():
                company_allocations[allocation.task_id].append(allocation)
            for allocation in session.scalars(
                select(PersonalTaskPointLotAllocation).where(
                    PersonalTaskPointLotAllocation.task_id.in_(task_ids)
                )
            ).all():
                personal_allocations[allocation.task_id].append(allocation)
            for allocation in session.scalars(
                select(PointLotSettlementValueAllocation).where(
                    PointLotSettlementValueAllocation.task_id.in_(task_ids)
                )
            ).all():
                value_allocations[allocation.task_id].append(allocation)

        succeeded = failed = cancelled = active = 0
        settled_points = cash_basis = receivable_basis = subsidy = 0
        for task in tasks:
            company_scope = task.company_id is not None
            settles: list[Any] = (
                company_settles.get(task.id, [])
                if company_scope
                else personal_settles.get(task.id, [])
            )
            allocations: list[Any] = (
                company_allocations.get(task.id, [])
                if company_scope
                else personal_allocations.get(task.id, [])
            )
            values = value_allocations.get(task.id, [])
            if task.status == TaskStatus.SUCCEEDED:
                succeeded += 1
                if len(settles) != 1:
                    issue(
                        "TASK_SETTLEMENT_MISSING"
                        if not settles
                        else "TASK_SETTLEMENT_DUPLICATE",
                        ReconciliationDimensionStatus.MISSING
                        if not settles
                        else ReconciliationDimensionStatus.DUPLICATE,
                        task.id,
                        expected=1,
                        actual=len(settles),
                    )
                settle = settles[0] if len(settles) == 1 else None
                actual_points = task.actual_cost_points
                if actual_points is None:
                    issue(
                        "TASK_ACTUAL_POINTS_MISSING",
                        ReconciliationDimensionStatus.MISSING,
                        task.id,
                        expected=task.quote_points,
                        actual=None,
                    )
                    actual_points = 0
                if task.reserved_points != 0:
                    issue(
                        "TASK_TERMINAL_RESERVATION_NOT_ZERO",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=0,
                        actual=task.reserved_points,
                    )
                if settle is not None and settle.amount_points != actual_points:
                    issue(
                        "TASK_SETTLEMENT_AMOUNT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=actual_points,
                        actual=settle.amount_points,
                        ledger_entry_id=settle.id,
                    )
                allocated_settled = sum(item.settled_points for item in allocations)
                allocated_reserved = sum(item.reserved_points for item in allocations)
                if allocated_settled != actual_points:
                    issue(
                        "TASK_LOT_SETTLEMENT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=actual_points,
                        actual=allocated_settled,
                    )
                if allocated_reserved != 0:
                    issue(
                        "TASK_LOT_RESERVATION_NOT_ZERO",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=0,
                        actual=allocated_reserved,
                    )
                value_points = sum(item.settled_points for item in values)
                if value_points != actual_points:
                    issue(
                        "TASK_VALUE_ALLOCATION_MISSING"
                        if value_points == 0
                        else "TASK_VALUE_ALLOCATION_MISMATCH",
                        ReconciliationDimensionStatus.MISSING
                        if value_points == 0
                        else ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=actual_points,
                        actual=value_points,
                    )
                value_by_allocation: dict[str, list[PointLotSettlementValueAllocation]] = (
                    defaultdict(list)
                )
                for value in values:
                    allocation_id = (
                        value.company_task_allocation_id
                        if company_scope
                        else value.personal_task_allocation_id
                    )
                    if allocation_id:
                        value_by_allocation[allocation_id].append(value)
                    expected_ledger_id = (
                        value.company_settle_ledger_id
                        if company_scope
                        else value.personal_settle_ledger_id
                    )
                    if settle is not None and expected_ledger_id != settle.id:
                        issue(
                            "TASK_VALUE_LEDGER_MISMATCH",
                            ReconciliationDimensionStatus.MISMATCH,
                            task.id,
                            value_allocation_id=value.id,
                            expected_ledger_id=settle.id,
                            actual_ledger_id=expected_ledger_id,
                        )
                for allocation in allocations:
                    if allocation.settled_points <= 0:
                        continue
                    rows = value_by_allocation.get(allocation.id, [])
                    if len(rows) != 1:
                        issue(
                            "TASK_VALUE_ALLOCATION_MISSING"
                            if not rows
                            else "TASK_VALUE_ALLOCATION_DUPLICATE",
                            ReconciliationDimensionStatus.MISSING
                            if not rows
                            else ReconciliationDimensionStatus.DUPLICATE,
                            task.id,
                            expected=1,
                            actual=len(rows),
                            task_allocation_id=allocation.id,
                        )
                    elif rows[0].settled_points != allocation.settled_points:
                        issue(
                            "TASK_VALUE_ALLOCATION_AMOUNT_MISMATCH",
                            ReconciliationDimensionStatus.MISMATCH,
                            task.id,
                            expected=allocation.settled_points,
                            actual=rows[0].settled_points,
                            task_allocation_id=allocation.id,
                        )
                settled_points += actual_points
                cash_basis += sum(item.cash_basis_cents for item in values)
                receivable_basis += sum(item.receivable_basis_cents for item in values)
                subsidy += sum(item.subsidy_cents for item in values)
            elif task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                if task.status == TaskStatus.FAILED:
                    failed += 1
                else:
                    cancelled += 1
                if task.reserved_points != 0:
                    issue(
                        "TASK_TERMINAL_RESERVATION_NOT_ZERO",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=0,
                        actual=task.reserved_points,
                    )
                if task.actual_cost_points not in {None, 0}:
                    issue(
                        "TASK_FAILED_CHARGED",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=0,
                        actual=task.actual_cost_points,
                    )
                if settles:
                    issue(
                        "TASK_FAILED_SETTLEMENT_PRESENT",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=0,
                        actual=len(settles),
                    )
                if values:
                    issue(
                        "TASK_FAILED_VALUE_ALLOCATION_PRESENT",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=0,
                        actual=sum(item.settled_points for item in values),
                    )
            else:
                active += 1
                allocation_reserved = sum(item.reserved_points for item in allocations)
                if allocation_reserved != task.reserved_points:
                    issue(
                        "TASK_ACTIVE_RESERVATION_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        task.id,
                        expected=task.reserved_points,
                        actual=allocation_reserved,
                    )

        totals = {
            "task_count": len(tasks),
            "succeeded_task_count": succeeded,
            "failed_task_count": failed,
            "cancelled_task_count": cancelled,
            "active_task_count": active,
            "settled_points": settled_points,
            "cash_basis_cents": cash_basis,
            "receivable_basis_cents": receivable_basis,
            "subsidy_cents": subsidy,
        }
        return _DimensionDraft(
            dimension="tasks",
            status=_status_for_exceptions(exceptions),
            totals=totals,
            exceptions=tuple(exceptions),
        )

    @classmethod
    def _provider_cost_dimension(
        cls,
        session: Session,
        *,
        start: datetime,
        end: datetime,
        statement_available: bool,
        statement_batches: Sequence[ProviderCostStatementBatch],
    ) -> _DimensionDraft:
        exceptions: list[_ExceptionDraft] = []

        def issue(
            code: str,
            status: ReconciliationDimensionStatus,
            entity_type: str,
            entity_id: str | None,
            *,
            expected: int | None = None,
            actual: int | None = None,
            severity: str = "critical",
            **details: Any,
        ) -> None:
            exceptions.append(
                _ExceptionDraft(
                    dimension="provider_cost",
                    code=code,
                    status=status,
                    severity=severity,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    expected_amount=expected,
                    actual_amount=actual,
                    details=details,
                )
            )

        tasks = list(
            session.scalars(
                select(GenerationTask).where(
                    GenerationTask.status == TaskStatus.SUCCEEDED,
                    GenerationTask.updated_at >= start,
                    GenerationTask.updated_at < end,
                )
            ).all()
        )
        task_by_id = {task.id: task for task in tasks}
        all_costs_by_task: dict[str, list[ChannelCostEntry]] = defaultdict(list)
        if task_by_id:
            for entry in session.scalars(
                select(ChannelCostEntry).where(
                    ChannelCostEntry.task_id.in_(task_by_id)
                )
            ).all():
                assert entry.task_id is not None
                all_costs_by_task[entry.task_id].append(entry)
        period_costs = list(
            session.scalars(
                select(ChannelCostEntry)
                .where(
                    ChannelCostEntry.occurred_at >= start,
                    ChannelCostEntry.occurred_at < end,
                )
                .order_by(ChannelCostEntry.occurred_at, ChannelCostEntry.id)
            ).all()
        )

        statement_lines: list[ProviderCostStatementLine] = []
        if statement_available:
            batch_ids = [batch.id for batch in statement_batches]
            statement_lines = list(
                session.scalars(
                    select(ProviderCostStatementLine)
                    .where(ProviderCostStatementLine.batch_id.in_(batch_ids))
                    .order_by(
                        ProviderCostStatementLine.supplier,
                        ProviderCostStatementLine.supplier_account,
                        ProviderCostStatementLine.provider_line_id,
                    )
                ).all()
            )
            lines_by_batch: dict[str, list[ProviderCostStatementLine]] = defaultdict(
                list
            )
            for line in statement_lines:
                lines_by_batch[line.batch_id].append(line)
            for batch in statement_batches:
                try:
                    validate_archived_statement(batch)
                except ConflictError as exc:
                    issue(
                        "PROVIDER_COST_SOURCE_DOCUMENT_INVALID",
                        ReconciliationDimensionStatus.MISMATCH,
                        "provider_cost_statement_batch",
                        batch.id,
                        reason=str(exc),
                    )
                issue(
                    "PROVIDER_COST_SOURCE_AUTHENTICITY_UNVERIFIED",
                    ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
                    "provider_cost_statement_batch",
                    batch.id,
                    severity="warning",
                    verification_method=batch.verification_method,
                )
                rows = lines_by_batch.get(batch.id, [])
                payload = [
                    {
                        "provider_line_id": line.provider_line_id,
                        "provider_job_reference": line.provider_job_reference,
                        "channel_key": line.channel_key,
                        "task_id": line.task_id,
                        "amount_cents": line.amount_cents,
                        "currency": line.currency,
                        "occurred_at": _database_utc(line.occurred_at),
                    }
                    for line in sorted(rows, key=lambda item: item.provider_line_id)
                ]
                actual_lines_sha256 = canonical_sha256(payload)
                if (
                    len(rows) != batch.line_count
                    or sum(line.amount_cents for line in rows)
                    != batch.total_cost_cents
                    or actual_lines_sha256 != batch.lines_sha256
                    or any(
                        line.supplier != batch.supplier
                        or line.supplier_account != batch.supplier_account
                        or not start <= _database_utc(line.occurred_at) < end
                        for line in rows
                    )
                ):
                    issue(
                        "PROVIDER_COST_BATCH_CONTROL_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "provider_cost_statement_batch",
                        batch.id,
                        expected=batch.total_cost_cents,
                        actual=sum(line.amount_cents for line in rows),
                        expected_line_count=batch.line_count,
                        actual_line_count=len(rows),
                        expected_lines_sha256=batch.lines_sha256,
                        actual_lines_sha256=actual_lines_sha256,
                    )
        else:
            issue(
                "PROVIDER_COST_STATEMENT_SOURCE_UNAVAILABLE",
                ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
                "provider_cost_statement_source",
                None,
            )

        statement_by_task: dict[str, list[ProviderCostStatementLine]] = defaultdict(list)
        statement_by_reference: dict[str, list[ProviderCostStatementLine]] = defaultdict(
            list
        )
        for line in statement_lines:
            if line.task_id is not None:
                statement_by_task[line.task_id].append(line)
            statement_by_reference[line.provider_job_reference].append(line)
        for reference, rows in statement_by_reference.items():
            if len(rows) > 1:
                issue(
                    "PROVIDER_COST_STATEMENT_LOGICAL_DUPLICATE",
                    ReconciliationDimensionStatus.DUPLICATE,
                    "provider_job_reference",
                    reference,
                    expected=1,
                    actual=len(rows),
                    statement_line_ids=[row.id for row in rows],
                )

        matched_cost_ids: set[str] = set()
        matched_line_ids: set[str] = set()
        for task in tasks:
            all_entries = all_costs_by_task.get(task.id, [])
            entries = [
                entry
                for entry in all_entries
                if start <= _database_utc(entry.occurred_at) < end
            ]
            outside_entries = [entry for entry in all_entries if entry not in entries]
            if not entries and outside_entries:
                issue(
                    "TASK_PROVIDER_COST_CROSS_PERIOD",
                    ReconciliationDimensionStatus.MISMATCH,
                    "generation_task",
                    task.id,
                    expected=1,
                    actual=len(outside_entries),
                    outside_cost_entry_ids=[entry.id for entry in outside_entries],
                )
            if not entries:
                issue(
                    "TASK_PROVIDER_COST_MISSING",
                    ReconciliationDimensionStatus.MISSING,
                    "generation_task",
                    task.id,
                    expected=1,
                    actual=0,
                )
                continue
            if len(entries) > 1:
                issue(
                    "TASK_PROVIDER_COST_DUPLICATE",
                    ReconciliationDimensionStatus.DUPLICATE,
                    "generation_task",
                    task.id,
                    expected=1,
                    actual=len(entries),
                    cost_entry_ids=[entry.id for entry in entries],
                )
            for entry in entries:
                scope_matches = (
                    entry.company_id == task.company_id
                    and entry.personal_workspace_id == task.personal_workspace_id
                )
                relay_matches = (
                    task.relay_job_id is not None
                    and entry.relay_job_id == task.relay_job_id
                )
                trusted_internal_fact = (
                    entry.source == ChannelCostSource.RELAY
                    and entry.relay_event_id is not None
                    and entry.relay_event_timestamp is not None
                    and entry.relay_payload_sha256 is not None
                )
                if not scope_matches or not relay_matches or not trusted_internal_fact:
                    issue(
                        "TASK_PROVIDER_COST_SCOPE_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "channel_cost_entry",
                        entry.id,
                        task_id=task.id,
                        expected_company_id=task.company_id,
                        actual_company_id=entry.company_id,
                        expected_personal_workspace_id=task.personal_workspace_id,
                        actual_personal_workspace_id=entry.personal_workspace_id,
                        expected_relay_job_id=task.relay_job_id,
                        actual_relay_job_id=entry.relay_job_id,
                        source=entry.source.value,
                        relay_event_id=entry.relay_event_id,
                    )
                candidates = [
                    line
                    for line in statement_by_task.get(task.id, [])
                    if line.provider_job_reference
                    in {
                        entry.relay_job_id,
                        entry.external_reference,
                        entry.evidence_reference,
                    }
                    and (line.channel_key is None or line.channel_key == entry.channel_key)
                ]
                if not candidates:
                    candidates = [
                        line
                        for reference in {
                            entry.relay_job_id,
                            entry.external_reference,
                            entry.evidence_reference,
                        }
                        if reference
                        for line in statement_by_reference.get(reference, [])
                        if line.task_id in {None, task.id}
                        and (
                            line.channel_key is None
                            or line.channel_key == entry.channel_key
                        )
                    ]
                unique_candidates = {line.id: line for line in candidates}
                candidates = list(unique_candidates.values())
                if len(candidates) != 1:
                    issue(
                        "PROVIDER_COST_STATEMENT_LINE_MISSING"
                        if not candidates
                        else "PROVIDER_COST_STATEMENT_LINE_DUPLICATE",
                        ReconciliationDimensionStatus.MISSING
                        if not candidates
                        else ReconciliationDimensionStatus.DUPLICATE,
                        "channel_cost_entry",
                        entry.id,
                        expected=1,
                        actual=len(candidates),
                        task_id=task.id,
                    )
                    continue
                line = candidates[0]
                matched_cost_ids.add(entry.id)
                matched_line_ids.add(line.id)
                if entry.amount_cents != line.amount_cents:
                    issue(
                        "PROVIDER_COST_STATEMENT_AMOUNT_MISMATCH",
                        ReconciliationDimensionStatus.MISMATCH,
                        "channel_cost_entry",
                        entry.id,
                        expected=line.amount_cents,
                        actual=entry.amount_cents,
                        statement_line_id=line.id,
                    )
        for entry in period_costs:
            if entry.task_id is None:
                issue(
                    "PROVIDER_COST_UNATTRIBUTED",
                    ReconciliationDimensionStatus.UNATTRIBUTED,
                    "channel_cost_entry",
                    entry.id,
                    actual=entry.amount_cents,
                    external_reference=entry.external_reference,
                )
            elif entry.task_id not in task_by_id:
                issue(
                    "PROVIDER_COST_TASK_CROSS_PERIOD",
                    ReconciliationDimensionStatus.MISMATCH,
                    "channel_cost_entry",
                    entry.id,
                    actual=entry.amount_cents,
                    task_id=entry.task_id,
                )
            elif entry.id not in matched_cost_ids and statement_available:
                issue(
                    "PROVIDER_COST_INTERNAL_FACT_UNMATCHED",
                    ReconciliationDimensionStatus.UNATTRIBUTED,
                    "channel_cost_entry",
                    entry.id,
                    actual=entry.amount_cents,
                )
        for line in statement_lines:
            if line.id not in matched_line_ids:
                issue(
                    "PROVIDER_COST_STATEMENT_LINE_UNMATCHED",
                    ReconciliationDimensionStatus.UNATTRIBUTED,
                    "provider_cost_statement_line",
                    line.id,
                    actual=line.amount_cents,
                    provider_job_reference=line.provider_job_reference,
                )

        totals = {
            "source_status": "available" if statement_available else "unavailable",
            "statement_batch_count": len(statement_batches),
            "statement_line_count": len(statement_lines),
            "statement_cost_cents": sum(line.amount_cents for line in statement_lines),
            "succeeded_task_count": len(tasks),
            "costed_succeeded_task_count": sum(
                1
                for task in tasks
                if any(
                    start <= _database_utc(entry.occurred_at) < end
                    for entry in all_costs_by_task.get(task.id, [])
                )
            ),
            "period_cost_entry_count": len(period_costs),
            "provider_cost_cents": sum(item.amount_cents for item in period_costs),
            "unattributed_cost_entry_count": sum(
                1 for item in period_costs if item.task_id is None
            ),
            "matched_cost_entry_count": len(matched_cost_ids),
            "matched_statement_line_count": len(matched_line_ids),
        }
        return _DimensionDraft(
            dimension="provider_cost",
            status=_status_for_exceptions(
                exceptions, source_unavailable=not statement_available
            ),
            totals=totals,
            exceptions=tuple(exceptions),
        )
