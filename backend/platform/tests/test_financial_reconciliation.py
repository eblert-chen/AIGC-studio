from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from platform_api.models import (
    AccountsReceivableLedgerEntry,
    BillingUnit,
    ChannelCostEntry,
    ChannelCostSource,
    ChannelType,
    Company,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyInvoiceLine,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    FinanceReconciliationException,
    FinanceReconciliationRun,
    FinanceReconciliationRunSource,
    FinanceReconciliationSnapshot,
    GenerationTask,
    EnterpriseInvoiceStatus,
    LedgerKind,
    ModelDefinition,
    PaymentOrder,
    PaymentOrderStatus,
    PaymentPurpose,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentDispute,
    PaymentDisputeDebtRecoveryAllocation,
    PaymentDisputeDebtRecoveryReversal,
    PaymentDisputeStatus,
    PaymentSettlementBatch,
    PaymentSettlementSourceKind,
    PaymentTransaction,
    PaymentTransactionKind,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalTaskPointLotAllocation,
    PersonalWalletAccount,
    PersonalWorkspace,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    PointLotSourceKind,
    ProviderCostStatementBatch,
    ReconciliationDimensionStatus,
    ReconciliationRunStatus,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    UserAccountType,
)
from platform_api.services.errors import ConflictError
from platform_api.services.enterprise_billing import EnterpriseBillingService
from platform_api.services.financial_reconciliation import (
    RECONCILIATION_DIMENSIONS,
    FinancialReconciliationService,
    canonical_json,
    canonical_sha256,
)
from platform_api.services.payment_settlements import (
    PaymentSettlementImportService,
    PaymentSettlementLine,
    ProviderCostStatementImportService,
)
from .test_enterprise_monthly_billing import (
    _activate,
    _seed_point_company,
    _settle_contract_task,
)


UTC = timezone.utc
PERIOD_START = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)
PERIOD_END = PERIOD_START + timedelta(days=1)
OCCURRED_AT = PERIOD_START + timedelta(hours=3)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _import_payment_sources(
    session,
    *,
    suffix: str,
    lines: list[PaymentSettlementLine],
) -> tuple[str, str]:
    payout_amount = sum(item.net_amount_cents for item in lines)
    assert payout_amount > 0
    payout_reference = f"payout-{suffix}"
    psp_lines = [
        *lines,
        PaymentSettlementLine(
            provider_line_id=f"psp-payout-{suffix}",
            provider_transaction_id=payout_reference,
            related_provider_reference=None,
            line_type="payout",
            gross_amount_cents=payout_amount,
            fee_amount_cents=0,
            net_amount_cents=payout_amount,
            currency="CNY",
            occurred_at=OCCURRED_AT + timedelta(hours=1),
        ),
    ]
    psp_document = json.dumps(
        {
            "schema_version": 1,
            "provider": "testpay",
            "merchant_account": "merchant-main",
            "source_kind": "psp_statement",
            "period_start": PERIOD_START.isoformat(),
            "period_end": PERIOD_END.isoformat(),
            "lines": [
                {
                    "provider_line_id": line.provider_line_id,
                    "provider_transaction_id": line.provider_transaction_id,
                    "related_provider_reference": line.related_provider_reference,
                    "line_type": line.line_type,
                    "gross_amount_cents": line.gross_amount_cents,
                    "fee_amount_cents": line.fee_amount_cents,
                    "net_amount_cents": line.net_amount_cents,
                    "currency": line.currency,
                    "occurred_at": line.occurred_at.isoformat(),
                }
                for line in psp_lines
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    psp = PaymentSettlementImportService.import_document(
        session,
        provider="testpay",
        merchant_account="merchant-main",
        source_kind=PaymentSettlementSourceKind.PSP_STATEMENT,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        provider_document_id=None,
        source_document_sha256=hashlib.sha256(psp_document).hexdigest(),
        source_document_bytes=psp_document,
        source_object_key=f"settlements/{suffix}/psp.json",
        source_object_version="v1",
        source_size_bytes=len(psp_document),
        parser_version="payment-settlement-json-v1",
    )
    bank_line = PaymentSettlementLine(
        provider_line_id=f"bank-deposit-{suffix}",
        provider_transaction_id=f"deposit-{suffix}",
        related_provider_reference=payout_reference,
        line_type="bank_deposit",
        gross_amount_cents=payout_amount,
        fee_amount_cents=0,
        net_amount_cents=payout_amount,
        currency="CNY",
        occurred_at=OCCURRED_AT + timedelta(hours=2),
    )
    bank_document = json.dumps(
        {
            "schema_version": 1,
            "provider": "testpay",
            "merchant_account": "merchant-main",
            "source_kind": "bank_statement",
            "period_start": PERIOD_START.isoformat(),
            "period_end": PERIOD_END.isoformat(),
            "lines": [
                {
                    "provider_line_id": bank_line.provider_line_id,
                    "provider_transaction_id": bank_line.provider_transaction_id,
                    "related_provider_reference": bank_line.related_provider_reference,
                    "line_type": bank_line.line_type,
                    "gross_amount_cents": bank_line.gross_amount_cents,
                    "fee_amount_cents": bank_line.fee_amount_cents,
                    "net_amount_cents": bank_line.net_amount_cents,
                    "currency": bank_line.currency,
                    "occurred_at": bank_line.occurred_at.isoformat(),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bank = PaymentSettlementImportService.import_document(
        session,
        provider="testpay",
        merchant_account="merchant-main",
        source_kind=PaymentSettlementSourceKind.BANK_STATEMENT,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        provider_document_id=None,
        source_document_sha256=hashlib.sha256(bank_document).hexdigest(),
        source_document_bytes=bank_document,
        source_object_key=f"settlements/{suffix}/bank.json",
        source_object_version="v1",
        source_size_bytes=len(bank_document),
        parser_version="payment-settlement-json-v1",
    )
    return psp.batch.id, bank.batch.id


def _import_provider_cost_source(
    session,
    *,
    suffix: str,
    task: GenerationTask,
    cost: ChannelCostEntry,
) -> str:
    assert task.relay_job_id
    document = json.dumps(
        {
            "schema_version": 1,
            "supplier": "relay-supplier",
            "supplier_account": "supplier-main",
            "period_start": PERIOD_START.isoformat(),
            "period_end": PERIOD_END.isoformat(),
            "lines": [
                {
                    "provider_line_id": f"supplier-line-{suffix}",
                    "provider_job_reference": task.relay_job_id,
                    "channel_key": cost.channel_key,
                    "task_id": task.id,
                    "amount_cents": cost.amount_cents,
                    "currency": "CNY",
                    "occurred_at": (
                        cost.occurred_at
                        if cost.occurred_at.tzinfo is not None
                        else cost.occurred_at.replace(tzinfo=UTC)
                    ).isoformat(),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result = ProviderCostStatementImportService.import_document(
        session,
        supplier="relay-supplier",
        supplier_account="supplier-main",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        provider_document_id=None,
        source_document_sha256=hashlib.sha256(document).hexdigest(),
        source_document_bytes=document,
        source_object_key=f"provider-cost/{suffix}.json",
        source_object_version="v1",
        source_size_bytes=len(document),
        parser_version="provider-cost-json-v1",
    )
    return result.batch.id


def _run(
    session,
    *,
    key: str,
    provider_statement_available: bool | None = None,
    provider_cost_statement_available: bool | None = None,
):
    payment_batch_ids = tuple(
        session.scalars(
            select(PaymentSettlementBatch.id).where(
                PaymentSettlementBatch.provider == "testpay",
                PaymentSettlementBatch.merchant_account == "merchant-main",
                PaymentSettlementBatch.period_start == PERIOD_START,
                PaymentSettlementBatch.period_end == PERIOD_END,
            )
        ).all()
    )
    cost_batch_ids = tuple(
        session.scalars(
            select(ProviderCostStatementBatch.id).where(
                ProviderCostStatementBatch.period_start == PERIOD_START,
                ProviderCostStatementBatch.period_end == PERIOD_END,
            )
        ).all()
    )
    payment_available = (
        bool(payment_batch_ids)
        if provider_statement_available is None
        else provider_statement_available
    )
    cost_available = (
        bool(cost_batch_ids)
        if provider_cost_statement_available is None
        else provider_cost_statement_available
    )
    return FinancialReconciliationService.run(
        session,
        run_kind="daily",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        idempotency_key=key,
        provider="testpay",
        merchant_account="merchant-main",
        provider_statement_available=payment_available,
        payment_settlement_batch_ids=payment_batch_ids if payment_available else (),
        provider_cost_statement_available=cost_available,
        provider_cost_batch_ids=cost_batch_ids if cost_available else (),
        source_watermarks={"payment_statement": "statement-2026-08-30-v1"},
        started_at=PERIOD_END,
    )


def _seed_balanced_company_chain(session, *, separate_fee: bool = False):
    company = Company(name="Reconciliation company", billing_version=2)
    user = User(
        email="reconciliation-owner@example.com",
        display_name="Reconciliation owner",
        account_type=UserAccountType.COMPANY,
    )
    model = ModelDefinition(
        slug="reconciliation-video-model",
        display_name="Reconciliation video model",
        provider_key="reconciliation-provider",
        billing_mode="per_item",
        active=True,
        published_at=PERIOD_START - timedelta(days=1),
    )
    session.add_all((company, user, model))
    session.flush()

    order = PaymentOrder(
        company_id=company.id,
        personal_workspace_id=None,
        created_by_user_id=user.id,
        purpose=PaymentPurpose.POINT_PURCHASE,
        purpose_reference_id=None,
        provider="testpay",
        merchant_account="merchant-main",
        provider_order_id="provider-order-balanced",
        status=PaymentOrderStatus.PAID,
        currency="CNY",
        amount_cents=100,
        points=10,
        captured_amount_cents=100,
        refunded_amount_cents=0,
        disputed_amount_cents=0,
        fee_amount_cents=2,
        idempotency_key="payment-order-balanced",
        request_fingerprint="1" * 64,
        automatic=False,
        captured_at=OCCURRED_AT,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    session.add(order)
    session.flush()

    capture = PaymentTransaction(
        order_id=order.id,
        provider="testpay",
        provider_transaction_id="capture-balanced",
        kind=PaymentTransactionKind.CAPTURE,
        amount_cents=100,
        currency="CNY",
        occurred_at=OCCURRED_AT,
        created_at=OCCURRED_AT,
    )
    settlement_line = PaymentSettlementLine(
        provider_line_id="statement-line-balanced",
        provider_transaction_id="capture-balanced",
        related_provider_reference=None,
        line_type="capture",
        gross_amount_cents=100,
        fee_amount_cents=0 if separate_fee else 2,
        net_amount_cents=100 if separate_fee else 98,
        currency="CNY",
        occurred_at=OCCURRED_AT,
    )
    wallet = CompanyPointWalletAccount(
        company_id=company.id,
        available_points=0,
        reserved_points=0,
        reversal_reserved_points=0,
        debt_points=0,
        migration_idempotency_key="reconciliation-wallet-v2",
        migrated_from_available_cents=0,
        migration_remainder_cents=0,
        migration_rounding_grant_points=0,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    lot = CompanyPointLot(
        company_id=company.id,
        source_kind=PointLotSourceKind.PURCHASED,
        original_points=10,
        available_points=0,
        reserved_points=0,
        reversal_reserved_points=0,
        settled_points=10,
        reversed_points=0,
        cash_basis_cents=100,
        receivable_basis_cents=0,
        subsidy_cents=0,
        idempotency_key="reconciliation-purchased-lot",
        payment_order_id=order.id,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    credit = CompanyPointLedgerEntry(
        company_id=company.id,
        kind=PointLedgerKind.CREDIT,
        amount_points=10,
        available_delta_points=10,
        reserved_delta_points=0,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key="reconciliation-purchase-credit",
        payment_order_id=order.id,
        note="captured purchase",
        created_at=OCCURRED_AT,
    )
    session.add_all((capture, wallet, lot, credit))
    if separate_fee:
        session.add_all(
            (
                PaymentTransaction(
                    order_id=order.id,
                    provider="testpay",
                    provider_transaction_id="fee-balanced",
                    kind=PaymentTransactionKind.FEE,
                    amount_cents=2,
                    currency="CNY",
                    occurred_at=OCCURRED_AT,
                    created_at=OCCURRED_AT,
                ),
                PaymentTransaction(
                    order_id=order.id,
                    provider="testpay",
                    provider_transaction_id="payout-company",
                    kind=PaymentTransactionKind.PAYOUT,
                    amount_cents=98,
                    currency="CNY",
                    occurred_at=OCCURRED_AT + timedelta(hours=1),
                    created_at=OCCURRED_AT + timedelta(hours=1),
                ),
            )
        )
    session.flush()

    task = GenerationTask(
        company_id=company.id,
        personal_workspace_id=None,
        user_id=user.id,
        model_id=model.id,
        idempotency_key="reconciliation-task",
        request_fingerprint="3" * 64,
        status=TaskStatus.SUCCEEDED,
        request_payload={"prompt": "reconciliation fixture"},
        billing_unit=BillingUnit.POINT,
        billing_version=2,
        quote_cents=None,
        quote_points=10,
        pricing_snapshot={"points": 10},
        capability_snapshot={"revision": "fixture"},
        relay_job_id="11111111-1111-4111-8111-111111111111",
        reserved_cents=0,
        reserved_points=0,
        actual_cost_cents=None,
        actual_cost_points=10,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    session.add(task)
    session.flush()

    reserve = CompanyPointLedgerEntry(
        company_id=company.id,
        kind=PointLedgerKind.RESERVE,
        amount_points=10,
        available_delta_points=-10,
        reserved_delta_points=10,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key="reconciliation-task-reserve",
        task_id=task.id,
        note="task reserve",
        created_at=OCCURRED_AT,
    )
    settle = CompanyPointLedgerEntry(
        company_id=company.id,
        kind=PointLedgerKind.SETTLE,
        amount_points=10,
        available_delta_points=0,
        reserved_delta_points=-10,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key="reconciliation-task-settle",
        task_id=task.id,
        note="task settle",
        created_at=OCCURRED_AT,
    )
    allocation = TaskPointLotAllocation(
        company_id=company.id,
        task_id=task.id,
        lot_id=lot.id,
        allocated_points=10,
        reserved_points=0,
        settled_points=10,
        released_points=0,
        created_at=OCCURRED_AT,
    )
    session.add_all((reserve, settle, allocation))
    session.flush()

    value = PointLotSettlementValueAllocation(
        company_id=company.id,
        personal_workspace_id=None,
        task_id=task.id,
        company_task_allocation_id=allocation.id,
        personal_task_allocation_id=None,
        company_settle_ledger_id=settle.id,
        personal_settle_ledger_id=None,
        settled_points=10,
        cash_basis_cents=100,
        receivable_basis_cents=0,
        subsidy_cents=0,
        created_at=OCCURRED_AT,
    )
    cost = ChannelCostEntry(
        amount_cents=40,
        idempotency_key="reconciliation-provider-cost",
        channel_key="official-test-channel",
        channel_type=ChannelType.OFFICIAL,
        occurred_at=OCCURRED_AT,
        external_reference="provider-cost-balanced",
        company_id=company.id,
        personal_workspace_id=None,
        task_id=task.id,
        relay_job_id=task.relay_job_id,
        relay_event_id="11111111-1111-4111-8111-111111111112",
        relay_event_timestamp=OCCURRED_AT,
        relay_payload_sha256="2" * 64,
        note="provider cost",
        evidence_source="supplier_statement",
        evidence_reference=task.relay_job_id,
        source_document_sha256="3" * 64,
        source=ChannelCostSource.RELAY,
        recorded_by_user_id=None,
        created_at=OCCURRED_AT,
    )
    session.add_all((value, cost))
    session.flush()
    settlement_lines = [settlement_line]
    if separate_fee:
        settlement_lines.append(
            PaymentSettlementLine(
                provider_line_id="statement-fee-balanced",
                provider_transaction_id="fee-balanced",
                related_provider_reference="capture-balanced",
                line_type="fee",
                gross_amount_cents=0,
                fee_amount_cents=2,
                net_amount_cents=-2,
                currency="CNY",
                occurred_at=OCCURRED_AT,
            )
        )
    _import_payment_sources(session, suffix="company", lines=settlement_lines)
    _import_provider_cost_source(session, suffix="company", task=task, cost=cost)
    return company, task


def _seed_balanced_personal_chain(session):
    user = User(
        email="reconciliation-personal@example.com",
        display_name="Personal reconciliation",
        account_type=UserAccountType.PERSONAL,
    )
    model = ModelDefinition(
        slug="reconciliation-personal-model",
        display_name="Personal reconciliation model",
        provider_key="reconciliation-provider",
        billing_mode="per_item",
        active=True,
        published_at=PERIOD_START - timedelta(days=1),
    )
    session.add_all((user, model))
    session.flush()
    workspace = PersonalWorkspace(user_id=user.id, active=True)
    session.add(workspace)
    session.flush()

    order = PaymentOrder(
        company_id=None,
        personal_workspace_id=workspace.id,
        created_by_user_id=user.id,
        purpose=PaymentPurpose.POINT_PURCHASE,
        purpose_reference_id=None,
        provider="testpay",
        merchant_account="merchant-main",
        provider_order_id="provider-order-personal",
        status=PaymentOrderStatus.PAID,
        currency="CNY",
        amount_cents=100,
        points=10,
        captured_amount_cents=100,
        refunded_amount_cents=0,
        disputed_amount_cents=0,
        fee_amount_cents=0,
        idempotency_key="payment-order-personal",
        request_fingerprint="5" * 64,
        automatic=False,
        captured_at=OCCURRED_AT,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    session.add(order)
    session.flush()
    session.add_all(
        (
            PaymentTransaction(
                order_id=order.id,
                provider="testpay",
                provider_transaction_id="capture-personal",
                kind=PaymentTransactionKind.CAPTURE,
                amount_cents=100,
                currency="CNY",
                occurred_at=OCCURRED_AT,
                created_at=OCCURRED_AT,
            ),
            PersonalWalletAccount(
                workspace_id=workspace.id,
                available_points=0,
                reserved_points=0,
                reversal_reserved_points=0,
                debt_points=0,
                created_at=OCCURRED_AT,
                updated_at=OCCURRED_AT,
            ),
        )
    )
    lot = PersonalPointLot(
        workspace_id=workspace.id,
        source_kind=PointLotSourceKind.PURCHASED,
        original_points=10,
        available_points=0,
        reserved_points=0,
        reversal_reserved_points=0,
        settled_points=10,
        reversed_points=0,
        cash_basis_cents=100,
        receivable_basis_cents=0,
        subsidy_cents=0,
        refundable=True,
        idempotency_key="reconciliation-personal-lot",
        payment_order_id=order.id,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    recharge = PersonalLedgerEntry(
        workspace_id=workspace.id,
        kind=LedgerKind.RECHARGE,
        amount_points=10,
        available_delta_points=10,
        reserved_delta_points=0,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key="reconciliation-personal-recharge",
        payment_order_id=order.id,
        note="captured purchase",
        created_at=OCCURRED_AT,
    )
    session.add_all((lot, recharge))
    session.flush()

    task = GenerationTask(
        company_id=None,
        personal_workspace_id=workspace.id,
        user_id=user.id,
        model_id=model.id,
        idempotency_key="reconciliation-personal-task",
        request_fingerprint="7" * 64,
        status=TaskStatus.SUCCEEDED,
        request_payload={"prompt": "personal reconciliation fixture"},
        billing_unit=BillingUnit.POINT,
        billing_version=2,
        quote_cents=None,
        quote_points=10,
        pricing_snapshot={"points": 10},
        capability_snapshot={"revision": "fixture"},
        relay_job_id="22222222-2222-4222-8222-222222222221",
        reserved_cents=0,
        reserved_points=0,
        actual_cost_cents=None,
        actual_cost_points=10,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    session.add(task)
    session.flush()
    reserve = PersonalLedgerEntry(
        workspace_id=workspace.id,
        kind=LedgerKind.RESERVE,
        amount_points=10,
        available_delta_points=-10,
        reserved_delta_points=10,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key="reconciliation-personal-reserve",
        task_id=task.id,
        note="task reserve",
        created_at=OCCURRED_AT,
    )
    settle = PersonalLedgerEntry(
        workspace_id=workspace.id,
        kind=LedgerKind.SETTLE,
        amount_points=10,
        available_delta_points=0,
        reserved_delta_points=-10,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key="reconciliation-personal-settle",
        task_id=task.id,
        note="task settle",
        created_at=OCCURRED_AT,
    )
    allocation = PersonalTaskPointLotAllocation(
        workspace_id=workspace.id,
        task_id=task.id,
        lot_id=lot.id,
        allocated_points=10,
        reserved_points=0,
        settled_points=10,
        released_points=0,
        created_at=OCCURRED_AT,
    )
    session.add_all((reserve, settle, allocation))
    session.flush()
    value = PointLotSettlementValueAllocation(
        company_id=None,
        personal_workspace_id=workspace.id,
        task_id=task.id,
        company_task_allocation_id=None,
        personal_task_allocation_id=allocation.id,
        company_settle_ledger_id=None,
        personal_settle_ledger_id=settle.id,
        settled_points=10,
        cash_basis_cents=100,
        receivable_basis_cents=0,
        subsidy_cents=0,
        created_at=OCCURRED_AT,
    )
    cost = ChannelCostEntry(
        amount_cents=35,
        idempotency_key="reconciliation-personal-provider-cost",
        channel_key="official-personal-channel",
        channel_type=ChannelType.OFFICIAL,
        occurred_at=OCCURRED_AT,
        external_reference="provider-cost-personal",
        company_id=None,
        personal_workspace_id=workspace.id,
        task_id=task.id,
        relay_job_id=task.relay_job_id,
        relay_event_id="22222222-2222-4222-8222-222222222222",
        relay_event_timestamp=OCCURRED_AT,
        relay_payload_sha256="6" * 64,
        note="provider cost",
        evidence_source="supplier_statement",
        evidence_reference=task.relay_job_id,
        source_document_sha256="7" * 64,
        source=ChannelCostSource.RELAY,
        recorded_by_user_id=None,
        created_at=OCCURRED_AT,
    )
    session.add_all((value, cost))
    session.flush()
    _import_payment_sources(
        session,
        suffix="personal",
        lines=[
            PaymentSettlementLine(
                provider_line_id="statement-line-personal",
                provider_transaction_id="capture-personal",
                related_provider_reference=None,
                line_type="capture",
                gross_amount_cents=100,
                fee_amount_cents=0,
                net_amount_cents=100,
                currency="CNY",
                occurred_at=OCCURRED_AT,
            )
        ],
    )
    _import_provider_cost_source(session, suffix="personal", task=task, cost=cost)
    return workspace, task


def _seed_balanced_invoice_adjustment_chain(
    session,
    *,
    refund_settlement_gross: int = -200,
    refund_ar_debit: int = 200,
):
    company, user, model = _seed_point_company(session)
    contract_start = datetime(2026, 7, 1, tzinfo=UTC)
    contract_end = datetime(2026, 8, 1, tzinfo=UTC)
    _activate(session, company=company, user=user, effective_at=contract_start)
    cycle, lot, _ = EnterpriseBillingService.open_cycle(
        session, company_id=company.id,
        period_start=contract_start, period_end=contract_end,
    )
    assert lot is not None
    task, _, _ = _settle_contract_task(
        session, company=company, user=user, model=model, lot=lot,
        points=100, key="invoice-reconciliation-task",
        settled_at=contract_start + timedelta(days=1),
    )
    task.created_at = contract_start + timedelta(days=1)
    task.updated_at = contract_start + timedelta(days=1)
    _, invoice, _, issued = EnterpriseBillingService.close_and_issue_cycle(
        session, company_id=company.id, cycle_id=cycle.id, issued_at=contract_end,
    )
    assert issued and invoice.total_cents == 1000
    invoice.status = EnterpriseInvoiceStatus.PARTIALLY_PAID
    invoice.paid_cents = 800
    invoice.updated_at = OCCURRED_AT
    order = PaymentOrder(
        company_id=company.id,
        personal_workspace_id=None,
        created_by_user_id=user.id,
        purpose=PaymentPurpose.INVOICE_PAYMENT,
        purpose_reference_id=invoice.id,
        provider="testpay",
        merchant_account="merchant-main",
        provider_order_id="provider-order-invoice",
        status=PaymentOrderStatus.PARTIALLY_REFUNDED,
        currency="CNY",
        amount_cents=1_000,
        points=0,
        captured_amount_cents=1_000,
        refunded_amount_cents=200,
        disputed_amount_cents=0,
        fee_amount_cents=0,
        idempotency_key="payment-order-invoice",
        request_fingerprint="9" * 64,
        automatic=False,
        captured_at=OCCURRED_AT,
        created_at=OCCURRED_AT,
        updated_at=OCCURRED_AT,
    )
    session.add(order)
    session.flush()
    refund = PaymentRefund(
        order_id=order.id, provider="testpay", provider_refund_id="invoice-refund-original",
        status=PaymentRefundStatus.SUCCEEDED, amount_cents=200, points=0, currency="CNY",
        reason="invoice payment refund", idempotency_key="invoice-refund-request",
        request_fingerprint="a" * 64, requested_by_user_id=user.id,
        completed_at=OCCURRED_AT + timedelta(minutes=1),
    )
    dispute = PaymentDispute(
        order_id=order.id, provider="testpay", provider_dispute_id="invoice-dispute-original",
        status=PaymentDisputeStatus.WON, amount_cents=100, points=0, currency="CNY",
        reason_code="fraudulent", recovered_available_points=0, debt_points=0,
        opened_at=OCCURRED_AT + timedelta(minutes=2),
        closed_at=OCCURRED_AT + timedelta(minutes=3),
    )
    session.add_all((refund, dispute))
    session.flush()
    transaction_specs = (
        (PaymentTransactionKind.CAPTURE, "invoice-capture", 1_000, 0),
        (PaymentTransactionKind.REFUND, "invoice-refund", 200, 1),
        (PaymentTransactionKind.CHARGEBACK, "invoice-chargeback", 100, 2),
        (PaymentTransactionKind.DISPUTE_REVERSAL, "invoice-reversal", 100, 3),
    )
    transactions: dict[PaymentTransactionKind, PaymentTransaction] = {}
    for kind, provider_transaction_id, amount_cents, minute in transaction_specs:
        transaction = PaymentTransaction(
            order_id=order.id,
            refund_id=refund.id if kind == PaymentTransactionKind.REFUND else None,
            dispute_id=dispute.id if kind in {
                PaymentTransactionKind.CHARGEBACK, PaymentTransactionKind.DISPUTE_REVERSAL
            } else None,
            provider="testpay",
            provider_transaction_id=provider_transaction_id,
            kind=kind,
            amount_cents=amount_cents,
            currency="CNY",
            occurred_at=OCCURRED_AT + timedelta(minutes=minute),
            created_at=OCCURRED_AT + timedelta(minutes=minute),
        )
        transactions[kind] = transaction
        session.add(transaction)
    session.flush()

    settlement_gross = {
        PaymentTransactionKind.CAPTURE: 1_000,
        PaymentTransactionKind.REFUND: refund_settlement_gross,
        PaymentTransactionKind.CHARGEBACK: -100,
        PaymentTransactionKind.DISPUTE_REVERSAL: 100,
    }
    settlement_lines: list[PaymentSettlementLine] = []
    for minute, (kind, transaction) in enumerate(transactions.items()):
        gross = settlement_gross[kind]
        settlement_lines.append(
            PaymentSettlementLine(
                provider_line_id=f"invoice-statement-{kind.value}",
                provider_transaction_id=transaction.provider_transaction_id,
                related_provider_reference=None,
                line_type=kind.value,
                gross_amount_cents=gross,
                fee_amount_cents=0,
                net_amount_cents=gross,
                currency="CNY",
                occurred_at=OCCURRED_AT + timedelta(minutes=minute),
            )
        )

    ar_amounts = {
        PaymentTransactionKind.CAPTURE: (0, 1_000),
        PaymentTransactionKind.REFUND: (refund_ar_debit, 0),
        PaymentTransactionKind.CHARGEBACK: (100, 0),
        PaymentTransactionKind.DISPUTE_REVERSAL: (0, 100),
    }
    for minute, (kind, transaction) in enumerate(transactions.items()):
        debit_cents, credit_cents = ar_amounts[kind]
        session.add(
            AccountsReceivableLedgerEntry(
                company_id=company.id,
                invoice_id=invoice.id,
                payment_transaction_id=transaction.id,
                kind=f"INVOICE_PAYMENT_{kind.name}",
                debit_cents=debit_cents,
                credit_cents=credit_cents,
                idempotency_key=f"invoice-ar-{kind.value}",
                note="invoice payment reconciliation fixture",
                created_at=OCCURRED_AT + timedelta(minutes=minute),
            )
        )
    session.flush()
    _import_payment_sources(session, suffix="invoice", lines=settlement_lines)
    return company, invoice, order


def test_canonical_json_and_sha256_are_stable():
    left = {
        "z": [3, 2, 1],
        "at": OCCURRED_AT,
        "status": ReconciliationRunStatus.BALANCED,
        "nested": {"b": True, "a": None},
    }
    right = {
        "nested": {"a": None, "b": True},
        "status": ReconciliationRunStatus.BALANCED,
        "at": OCCURRED_AT,
        "z": [3, 2, 1],
    }

    assert canonical_json(left) == canonical_json(right)
    assert canonical_sha256(left) == canonical_sha256(right)
    assert SHA256_PATTERN.fullmatch(canonical_sha256(left))


def test_empty_run_without_external_batches_is_fail_closed_and_idempotent(app):
    with app.state.session_factory() as session:
        result = _run(session, key="empty-balanced")
        session.commit()

        assert result.created is True
        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert result.run.completed_at == PERIOD_END
        assert SHA256_PATTERN.fullmatch(result.run.snapshot_sha256 or "")
        assert result.run.control_totals["total_exception_count"] == 2
        assert {item.code for item in result.exceptions} == {
            "PAYMENT_SETTLEMENT_SOURCE_UNAVAILABLE",
            "PROVIDER_COST_STATEMENT_SOURCE_UNAVAILABLE",
        }
        assert {item.dimension for item in result.snapshots} == set(
            RECONCILIATION_DIMENSIONS
        )
        statuses = {item.dimension: item.status for item in result.snapshots}
        assert statuses == {
            "cash": ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
            "points": ReconciliationDimensionStatus.MATCHED,
            "tasks": ReconciliationDimensionStatus.MATCHED,
            "provider_cost": ReconciliationDimensionStatus.SOURCE_UNAVAILABLE,
        }
        assert all(
            SHA256_PATTERN.fullmatch(item.evidence_sha256)
            for item in result.snapshots
        )
        run_id = result.run.id
        snapshot_sha256 = result.run.snapshot_sha256

    with app.state.session_factory() as session:
        replay = _run(session, key="empty-balanced")

        assert replay.created is False
        assert replay.run.id == run_id
        assert replay.run.snapshot_sha256 == snapshot_sha256
        assert len(replay.snapshots) == 4
        assert len(replay.exceptions) == 2
        assert session.query(FinanceReconciliationRun).count() == 1
        assert session.query(FinanceReconciliationSnapshot).count() == 4
        assert session.query(FinanceReconciliationException).count() == 2

        with pytest.raises(ConflictError, match="不同意图"):
            FinancialReconciliationService.run(
                session,
                run_kind="daily",
                period_start=PERIOD_START,
                period_end=PERIOD_END + timedelta(days=1),
                idempotency_key="empty-balanced",
                provider="testpay",
                merchant_account="merchant-main",
                provider_statement_available=False,
                payment_settlement_batch_ids=(),
                provider_cost_statement_available=False,
                provider_cost_batch_ids=(),
                source_watermarks={
                    "payment_statement": "statement-2026-08-30-v1"
                },
            )


def test_unavailable_provider_statement_cannot_report_balanced(app):
    with app.state.session_factory() as session:
        result = _run(
            session,
            key="statement-unavailable",
            provider_statement_available=False,
        )
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        cash = next(item for item in result.snapshots if item.dimension == "cash")
        assert cash.status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        assert {item.code for item in result.exceptions} == {
            "PAYMENT_SETTLEMENT_SOURCE_UNAVAILABLE",
            "PROVIDER_COST_STATEMENT_SOURCE_UNAVAILABLE",
        }
        assert result.run.control_totals["total_exception_count"] == 2


def test_order_created_in_period_but_captured_later_is_not_misbucketed(app):
    with app.state.session_factory() as session:
        company = Company(name="Later capture company", billing_version=2)
        user = User(
            email="later-capture@example.com",
            display_name="Later capture",
            account_type=UserAccountType.COMPANY,
        )
        session.add_all((company, user))
        session.flush()
        session.add(
            PaymentOrder(
                company_id=company.id,
                personal_workspace_id=None,
                created_by_user_id=user.id,
                purpose=PaymentPurpose.POINT_PURCHASE,
                purpose_reference_id=None,
                provider="testpay",
                merchant_account="merchant-main",
                provider_order_id="provider-order-captured-later",
                status=PaymentOrderStatus.PAID,
                currency="CNY",
                amount_cents=100,
                points=10,
                captured_amount_cents=100,
                refunded_amount_cents=0,
                disputed_amount_cents=0,
                fee_amount_cents=0,
                idempotency_key="payment-order-captured-later",
                request_fingerprint="b" * 64,
                automatic=False,
                captured_at=PERIOD_END + timedelta(hours=1),
                created_at=OCCURRED_AT,
                updated_at=PERIOD_END + timedelta(hours=1),
            )
        )
        session.flush()

        result = _run(session, key="capture-belongs-to-next-period")
        session.commit()

        cash = next(item for item in result.snapshots if item.dimension == "cash")
        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert cash.status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        assert cash.totals["order_count"] == 1
        assert cash.totals["captured_order_count"] == 0
        assert cash.totals["capture_amount_cents"] == 0


def test_complete_four_way_chain_has_no_accounting_gap_but_upload_authenticity_blocks_balance(app):
    with app.state.session_factory() as session:
        _, task = _seed_balanced_company_chain(session)
        result = _run(session, key="balanced-fact-chain")
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert {item.code for item in result.exceptions} == {
            "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
            "PROVIDER_COST_SOURCE_AUTHENTICITY_UNVERIFIED",
        }
        snapshots = {item.dimension: item for item in result.snapshots}
        assert snapshots["points"].status == ReconciliationDimensionStatus.MATCHED
        assert snapshots["tasks"].status == ReconciliationDimensionStatus.MATCHED
        assert snapshots["cash"].status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        assert snapshots["provider_cost"].status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        assert snapshots["cash"].totals == {
            "source_status": "available",
            "order_count": 1,
            "captured_order_count": 1,
            "point_purchase_order_count": 1,
            "invoice_payment_order_count": 0,
            "capture_transaction_count": 1,
            "capture_amount_cents": 100,
            "invoice_count": 0,
            "unbilled_receivable_requires_action_count": 0,
            "unbilled_receivable_requires_action_cents": 0,
            "invoice_paid_cents": 0,
            "invoice_projected_paid_cents": 0,
            "invoice_ar_credit_cents": 0,
            "invoice_ar_debit_cents": 0,
            "adjustment_transaction_count": 0,
            "adjustment_amount_cents": 0,
            "operational_transaction_count": 0,
            "settlement_batch_count": 2,
            "settlement_line_count": 3,
            "settlement_gross_amount_cents": 296,
            "settlement_fee_amount_cents": 2,
            "settlement_net_amount_cents": 294,
            "expected_payout_cents": 98,
            "payout_cents": 98,
            "bank_deposit_cents": 98,
            "purchased_lot_count": 1,
            "credit_ledger_count": 1,
            "debt_recovery_allocation_count": 0,
            "debt_recovered_points": 0,
            "granted_points": 10,
        }
        assert snapshots["tasks"].totals["succeeded_task_count"] == 1
        assert snapshots["tasks"].totals["settled_points"] == 10
        assert snapshots["provider_cost"].totals["succeeded_task_count"] == 1
        assert snapshots["provider_cost"].totals["provider_cost_cents"] == 40
        assert task.id


def test_separate_fee_payout_and_bank_deposit_close_without_double_counting(app):
    with app.state.session_factory() as session:
        _seed_balanced_company_chain(session, separate_fee=True)
        result = _run(session, key="separate-fee-payout-balanced")
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert {item.code for item in result.exceptions} == {
            "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
            "PROVIDER_COST_SOURCE_AUTHENTICITY_UNVERIFIED",
        }
        cash = next(item for item in result.snapshots if item.dimension == "cash")
        assert cash.totals["capture_amount_cents"] == 100
        assert cash.totals["operational_transaction_count"] == 2
        assert cash.totals["settlement_fee_amount_cents"] == 2
        assert cash.totals["expected_payout_cents"] == 98
        assert cash.totals["payout_cents"] == 98
        assert cash.totals["bank_deposit_cents"] == 98


def test_reconciliation_requires_complete_batch_bindings_and_replays_exact_sources(app):
    with app.state.session_factory() as session:
        _seed_balanced_company_chain(session)
        payment_ids = tuple(session.scalars(select(PaymentSettlementBatch.id)).all())
        cost_ids = tuple(session.scalars(select(ProviderCostStatementBatch.id)).all())
        psp_id = session.scalar(
            select(PaymentSettlementBatch.id).where(
                PaymentSettlementBatch.source_kind
                == PaymentSettlementSourceKind.PSP_STATEMENT
            )
        )
        common = {
            "run_kind": "daily",
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "provider": "testpay",
            "merchant_account": "merchant-main",
            "provider_statement_available": True,
            "provider_cost_statement_available": True,
        }
        with pytest.raises(ConflictError, match="同时绑定"):
            FinancialReconciliationService.run(
                session,
                **common,
                idempotency_key="missing-bank-batch",
                payment_settlement_batch_ids=(psp_id,),
                provider_cost_batch_ids=cost_ids,
            )
        with pytest.raises(ConflictError, match="必须绑定账单批次"):
            FinancialReconciliationService.run(
                session,
                **common,
                idempotency_key="missing-supplier-batch",
                payment_settlement_batch_ids=payment_ids,
                provider_cost_batch_ids=(),
            )

        result = _run(session, key="exact-source-bindings")
        session.commit()
        sources = tuple(
            session.scalars(
                select(FinanceReconciliationRunSource).where(
                    FinanceReconciliationRunSource.run_id == result.run.id
                )
            ).all()
        )
        assert len(sources) == 3
        assert {
            item.payment_settlement_batch_id
            for item in sources
            if item.source_kind == "payment_settlement"
        } == set(payment_ids)
        assert {
            item.provider_cost_batch_id
            for item in sources
            if item.source_kind == "provider_cost"
        } == set(cost_ids)
        replay = _run(session, key="exact-source-bindings")
        assert replay.created is False
        assert replay.run.snapshot_sha256 == result.run.snapshot_sha256


@pytest.mark.parametrize("tampered_field", ["status", "snapshot_totals"])
def test_replay_derives_conclusion_from_hash_verified_immutable_snapshots(
    app, tampered_field
):
    with app.state.session_factory() as session:
        _seed_balanced_company_chain(session)
        result = _run(session, key=f"snapshot-replay-{tampered_field}")
        session.commit()
        if tampered_field == "status":
            set_committed_value(
                result.run,
                "status",
                ReconciliationRunStatus.BALANCED,
            )
            expected_message = "结论与不可变快照不一致"
        else:
            cash = next(item for item in result.snapshots if item.dimension == "cash")
            set_committed_value(cash, "totals", {**cash.totals, "payout_cents": 0})
            expected_message = "快照证据摘要校验失败"
        with pytest.raises(ConflictError, match=expected_message):
            _run(session, key=f"snapshot-replay-{tampered_field}")


def test_internal_provider_cost_never_substitutes_for_supplier_statement(app):
    with app.state.session_factory() as session:
        _seed_balanced_company_chain(session)
        result = _run(
            session,
            key="supplier-source-unavailable",
            provider_cost_statement_available=False,
        )
        session.commit()
        provider_cost = next(
            item for item in result.snapshots if item.dimension == "provider_cost"
        )
        assert provider_cost.status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        assert result.run.status != ReconciliationRunStatus.BALANCED


@pytest.mark.parametrize("batch_model", [PaymentSettlementBatch, ProviderCostStatementBatch])
@pytest.mark.parametrize("tamper", ["bytes", "size", "parsed_lines"])
def test_reconciliation_reparses_archived_bytes_and_replay_rejects_tampering(app, batch_model, tamper):
    with app.state.session_factory() as session:
        _seed_balanced_company_chain(session)
        _run(session, key="archive-replay")
        session.commit()
        batch = session.scalar(select(batch_model))
        assert batch is not None
        if tamper == "bytes":
            set_committed_value(batch, "source_document_bytes", batch.source_document_bytes + b" ")
        elif tamper == "size":
            set_committed_value(batch, "source_size_bytes", batch.source_size_bytes + 1)
        else:
            set_committed_value(batch, "lines_sha256", "f" * 64)

        with pytest.raises(ConflictError, match="字节|文件大小|封存行摘要"):
            _run(session, key="archive-replay")
        fresh = _run(session, key="archive-corruption-detected")
        assert fresh.run.status != ReconciliationRunStatus.BALANCED
        expected_code = (
            "PAYMENT_SETTLEMENT_SOURCE_DOCUMENT_INVALID"
            if batch_model is PaymentSettlementBatch
            else "PROVIDER_COST_SOURCE_DOCUMENT_INVALID"
        )
        assert expected_code in {item.code for item in fresh.exceptions}


def test_duplicate_supplier_job_lines_cannot_be_reported_as_matched(app):
    with app.state.session_factory() as session:
        _, task = _seed_balanced_company_chain(session)
        cost = session.scalar(
            select(ChannelCostEntry).where(ChannelCostEntry.task_id == task.id)
        )
        assert cost is not None
        _import_provider_cost_source(
            session, suffix="company-duplicate", task=task, cost=cost
        )
        result = _run(session, key="duplicate-supplier-job")
        session.commit()
        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert "PROVIDER_COST_STATEMENT_LOGICAL_DUPLICATE" in {
            item.code for item in result.exceptions
        }


def test_personal_purchase_and_task_chain_uses_the_same_four_way_controls(app):
    with app.state.session_factory() as session:
        workspace, task = _seed_balanced_personal_chain(session)
        result = _run(session, key="balanced-personal-fact-chain")
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert {item.code for item in result.exceptions} == {
            "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
            "PROVIDER_COST_SOURCE_AUTHENTICITY_UNVERIFIED",
        }
        snapshots = {item.dimension: item for item in result.snapshots}
        assert snapshots["cash"].totals["captured_order_count"] == 1
        assert snapshots["points"].totals["personal_scope_count"] == 1
        assert snapshots["tasks"].totals["succeeded_task_count"] == 1
        assert snapshots["provider_cost"].totals["provider_cost_cents"] == 35
        assert workspace.id and task.id


@pytest.mark.parametrize("debt_points", [5, 10])
def test_point_purchase_capture_splits_grant_and_debt_recovery_and_reversal_facts(
    app, debt_points,
):
    debt_cents = debt_points * 10
    granted_points = 10 - debt_points
    with app.state.session_factory() as session:
        user = User(
            email="reconciliation-debt@example.com",
            display_name="Debt recovery reconciliation",
            account_type=UserAccountType.PERSONAL,
        )
        session.add(user)
        session.flush()
        workspace = PersonalWorkspace(user_id=user.id, active=True)
        session.add(workspace)
        session.flush()

        prior_time = PERIOD_START - timedelta(days=2)
        disputed_order = PaymentOrder(
            company_id=None,
            personal_workspace_id=workspace.id,
            created_by_user_id=user.id,
            purpose=PaymentPurpose.POINT_PURCHASE,
            purpose_reference_id=None,
            provider="testpay",
            merchant_account="merchant-main",
            provider_order_id="provider-order-debt-original",
            status=PaymentOrderStatus.PAID,
            currency="CNY",
            amount_cents=debt_cents,
            points=debt_points,
            captured_amount_cents=debt_cents,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key="payment-order-debt-original",
            request_fingerprint="c" * 64,
            automatic=False,
            captured_at=prior_time,
            created_at=prior_time,
            updated_at=prior_time,
        )
        recovery_order = PaymentOrder(
            company_id=None,
            personal_workspace_id=workspace.id,
            created_by_user_id=user.id,
            purpose=PaymentPurpose.POINT_PURCHASE,
            purpose_reference_id=None,
            provider="testpay",
            merchant_account="merchant-main",
            provider_order_id="provider-order-debt-recovery",
            status=PaymentOrderStatus.PAID,
            currency="CNY",
            amount_cents=100,
            points=10,
            captured_amount_cents=100,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key="payment-order-debt-recovery",
            request_fingerprint="d" * 64,
            automatic=False,
            captured_at=OCCURRED_AT,
            created_at=OCCURRED_AT,
            updated_at=OCCURRED_AT,
        )
        session.add_all((disputed_order, recovery_order))
        session.flush()
        dispute = PaymentDispute(
            order_id=disputed_order.id,
            provider="testpay",
            provider_dispute_id="dispute-debt-recovery",
            status=PaymentDisputeStatus.WON,
            amount_cents=debt_cents,
            points=debt_points,
            currency="CNY",
            reason_code="fraudulent",
            recovered_available_points=0,
            debt_points=debt_points,
            opened_at=OCCURRED_AT - timedelta(minutes=30),
            closed_at=OCCURRED_AT + timedelta(minutes=2),
            created_at=prior_time + timedelta(hours=1),
            updated_at=OCCURRED_AT + timedelta(minutes=2),
        )
        session.add(dispute)
        session.flush()
        prior_chargeback = PaymentTransaction(
            order_id=disputed_order.id,
            dispute_id=dispute.id,
            provider="testpay",
            provider_transaction_id="debt-original-chargeback",
            kind=PaymentTransactionKind.CHARGEBACK,
            amount_cents=debt_cents,
            currency="CNY",
            occurred_at=OCCURRED_AT - timedelta(minutes=30),
            created_at=OCCURRED_AT - timedelta(minutes=30),
        )
        recovery_capture = PaymentTransaction(
            order_id=recovery_order.id,
            provider="testpay",
            provider_transaction_id="debt-recovery-capture",
            kind=PaymentTransactionKind.CAPTURE,
            amount_cents=100,
            currency="CNY",
            occurred_at=OCCURRED_AT,
            created_at=OCCURRED_AT,
        )
        dispute_reversal = PaymentTransaction(
            order_id=disputed_order.id,
            dispute_id=dispute.id,
            provider="testpay",
            provider_transaction_id="debt-dispute-reversal",
            kind=PaymentTransactionKind.DISPUTE_REVERSAL,
            amount_cents=debt_cents,
            currency="CNY",
            occurred_at=OCCURRED_AT + timedelta(minutes=2),
            created_at=OCCURRED_AT + timedelta(minutes=2),
        )
        session.add_all((prior_chargeback, recovery_capture, dispute_reversal))
        session.flush()
        allocation = PaymentDisputeDebtRecoveryAllocation(
            dispute_id=dispute.id,
            recovery_payment_transaction_id=recovery_capture.id,
            company_id=None,
            personal_workspace_id=workspace.id,
            recovered_points=debt_points,
            idempotency_key="debt-recovery-allocation",
            created_at=OCCURRED_AT,
        )
        purchased_lot = PersonalPointLot(
            workspace_id=workspace.id,
            source_kind=PointLotSourceKind.PURCHASED,
            original_points=granted_points,
            available_points=granted_points,
            reserved_points=0,
            reversal_reserved_points=0,
            settled_points=0,
            reversed_points=0,
            cash_basis_cents=granted_points * 10,
            receivable_basis_cents=0,
            subsidy_cents=0,
            refundable=True,
            idempotency_key="debt-recovery-purchased-lot",
            payment_order_id=recovery_order.id,
            created_at=OCCURRED_AT,
            updated_at=OCCURRED_AT,
        )
        compensation_lot = PersonalPointLot(
            workspace_id=workspace.id,
            source_kind=PointLotSourceKind.COMPENSATION,
            original_points=debt_points,
            available_points=debt_points,
            reserved_points=0,
            reversal_reserved_points=0,
            settled_points=0,
            reversed_points=0,
            cash_basis_cents=debt_cents,
            receivable_basis_cents=0,
            subsidy_cents=0,
            refundable=False,
            idempotency_key="debt-recovery-compensation-lot",
            payment_order_id=None,
            created_at=OCCURRED_AT + timedelta(minutes=2),
            updated_at=OCCURRED_AT + timedelta(minutes=2),
        )
        session.add_all((allocation, compensation_lot))
        if granted_points:
            session.add(purchased_lot)
        session.flush()
        chargeback_ledger = PersonalLedgerEntry(
            workspace_id=workspace.id,
            kind=LedgerKind.CHARGEBACK,
            amount_points=debt_points,
            available_delta_points=0,
            reserved_delta_points=0,
            reversal_reserved_delta_points=0,
            debt_delta_points=debt_points,
            idempotency_key="debt-original-chargeback-ledger",
            payment_order_id=disputed_order.id,
            payment_dispute_id=dispute.id,
            note="original dispute debt",
            created_at=OCCURRED_AT - timedelta(minutes=30),
        )
        recovery_ledger = PersonalLedgerEntry(
            workspace_id=workspace.id,
            kind=LedgerKind.DEBT_RECOVERY,
            amount_points=debt_points,
            available_delta_points=0,
            reserved_delta_points=0,
            reversal_reserved_delta_points=0,
            debt_delta_points=-debt_points,
            idempotency_key="debt-recovery-ledger",
            payment_order_id=recovery_order.id,
            note="later purchase recovered debt",
            created_at=OCCURRED_AT,
        )
        recharge_ledger = PersonalLedgerEntry(
            workspace_id=workspace.id,
            kind=LedgerKind.RECHARGE,
            amount_points=granted_points,
            available_delta_points=granted_points,
            reserved_delta_points=0,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key="debt-recovery-recharge-ledger",
            payment_order_id=recovery_order.id,
            note="remaining grant",
            created_at=OCCURRED_AT,
        )
        reversal_ledger = PersonalLedgerEntry(
            workspace_id=workspace.id,
            kind=LedgerKind.DISPUTE_REVERSAL,
            amount_points=debt_points,
            available_delta_points=debt_points,
            reserved_delta_points=0,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key="debt-dispute-reversal-ledger",
            payment_order_id=disputed_order.id,
            payment_dispute_id=dispute.id,
            note="won dispute restored recovered debt",
            created_at=OCCURRED_AT + timedelta(minutes=2),
        )
        wallet = PersonalWalletAccount(
            workspace_id=workspace.id,
            available_points=10,
            reserved_points=0,
            reversal_reserved_points=0,
            debt_points=0,
            created_at=OCCURRED_AT,
            updated_at=OCCURRED_AT,
        )
        session.add_all(
            (
                chargeback_ledger,
                recovery_ledger,
                reversal_ledger,
                wallet,
            )
        )
        if granted_points:
            session.add(recharge_ledger)
        session.flush()
        session.add(
            PaymentDisputeDebtRecoveryReversal(
                allocation_id=allocation.id,
                dispute_id=dispute.id,
                restored_points=debt_points,
                company_ledger_entry_id=None,
                personal_ledger_entry_id=reversal_ledger.id,
                company_point_lot_id=None,
                personal_point_lot_id=compensation_lot.id,
                idempotency_key="debt-recovery-reversal",
                created_at=OCCURRED_AT + timedelta(minutes=2),
            )
        )
        session.flush()
        _import_payment_sources(
            session,
            suffix="debt-recovery",
            lines=[
                PaymentSettlementLine(
                    provider_line_id="debt-original-chargeback-line",
                    provider_transaction_id="debt-original-chargeback",
                    related_provider_reference=None,
                    line_type="chargeback",
                    gross_amount_cents=-debt_cents,
                    fee_amount_cents=0,
                    net_amount_cents=-debt_cents,
                    currency="CNY",
                    occurred_at=OCCURRED_AT - timedelta(minutes=30),
                ),
                PaymentSettlementLine(
                    provider_line_id="debt-recovery-capture-line",
                    provider_transaction_id="debt-recovery-capture",
                    related_provider_reference=None,
                    line_type="capture",
                    gross_amount_cents=100,
                    fee_amount_cents=0,
                    net_amount_cents=100,
                    currency="CNY",
                    occurred_at=OCCURRED_AT,
                ),
                PaymentSettlementLine(
                    provider_line_id="debt-dispute-reversal-line",
                    provider_transaction_id="debt-dispute-reversal",
                    related_provider_reference=None,
                    line_type="dispute_reversal",
                    gross_amount_cents=debt_cents,
                    fee_amount_cents=0,
                    net_amount_cents=debt_cents,
                    currency="CNY",
                    occurred_at=OCCURRED_AT + timedelta(minutes=2),
                ),
            ],
        )

        result = _run(session, key="debt-recovery-balanced-cash")
        session.commit()

        cash = next(item for item in result.snapshots if item.dimension == "cash")
        points = next(item for item in result.snapshots if item.dimension == "points")
        assert cash.status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        assert points.status == ReconciliationDimensionStatus.MATCHED
        assert {item.code for item in result.exceptions if item.dimension == "cash"} == {
            "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED"
        }
        assert cash.totals["debt_recovery_allocation_count"] == 1
        assert cash.totals["debt_recovered_points"] == debt_points
        assert cash.totals["granted_points"] == granted_points
        assert cash.totals["purchased_lot_count"] == int(granted_points > 0)
        assert cash.totals["credit_ledger_count"] == int(granted_points > 0)
        assert cash.totals["adjustment_transaction_count"] == 2


def test_invoice_payment_refund_chargeback_and_reversal_balance_without_points(app):
    with app.state.session_factory() as session:
        _, invoice, order = _seed_balanced_invoice_adjustment_chain(session)
        result = _run(session, key="balanced-invoice-adjustment-chain")
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert {item.code for item in result.exceptions} == {
            "PROVIDER_COST_STATEMENT_SOURCE_UNAVAILABLE",
            "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
        }, [(item.code, item.details) for item in result.exceptions]
        snapshots = {item.dimension: item for item in result.snapshots}
        assert snapshots["cash"].status == ReconciliationDimensionStatus.SOURCE_UNAVAILABLE
        cash = snapshots["cash"].totals
        assert cash["captured_order_count"] == 1
        assert cash["point_purchase_order_count"] == 0
        assert cash["invoice_payment_order_count"] == 1
        assert cash["purchased_lot_count"] == 0
        assert cash["credit_ledger_count"] == 0
        assert cash["invoice_count"] == 1
        assert cash["invoice_paid_cents"] == 800
        assert cash["invoice_projected_paid_cents"] == 800
        assert cash["invoice_ar_credit_cents"] == 1_100
        assert cash["invoice_ar_debit_cents"] == 300
        assert cash["adjustment_transaction_count"] == 3
        assert cash["adjustment_amount_cents"] == 400
        assert cash["expected_payout_cents"] == 800
        assert cash["payout_cents"] == 800
        assert cash["bank_deposit_cents"] == 800
        # The one lot is the pre-existing contract credit, not a payment grant.
        assert snapshots["points"].totals["lot_count"] == 1
        assert invoice.id == order.purpose_reference_id


def test_invoice_adjustment_amount_drift_creates_cash_exceptions(app):
    with app.state.session_factory() as session:
        _, invoice, _ = _seed_balanced_invoice_adjustment_chain(
            session,
            refund_settlement_gross=-199,
            refund_ar_debit=199,
        )
        result = _run(session, key="broken-invoice-adjustment-chain")
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        codes = {item.code for item in result.exceptions}
        assert {
            "PAYMENT_ADJUSTMENT_SETTLEMENT_AMOUNT_MISMATCH",
            "INVOICE_AR_ENTRY_AMOUNT_MISMATCH",
            "INVOICE_PAID_PROJECTION_MISMATCH",
        } <= codes
        projection = next(
            item
            for item in result.exceptions
            if item.code == "INVOICE_PAID_PROJECTION_MISMATCH"
        )
        assert projection.entity_id == invoice.id
        assert projection.expected_amount == 801
        assert projection.actual_amount == 800


@pytest.mark.parametrize("tamper", ["line_amount", "line_task", "contract_hash", "cycle_period", "value_amount"])
def test_invoice_document_corruption_is_a_structured_exception_not_a_failed_run(app, tamper):
    with app.state.session_factory() as session:
        _, invoice, _ = _seed_balanced_invoice_adjustment_chain(session)
        line = session.scalar(select(CompanyInvoiceLine).where(CompanyInvoiceLine.invoice_id == invoice.id))
        assert line is not None
        if tamper == "line_amount":
            set_committed_value(line, "amount_cents", 999)
        elif tamper == "line_task":
            set_committed_value(line, "task_id", "missing-task")
        elif tamper == "contract_hash":
            contract = session.get(CompanyBillingContractVersion, line.contract_version_id)
            set_committed_value(contract, "content_sha256", "f" * 64)
        elif tamper == "cycle_period":
            cycle = session.get(CompanyBillingCycle, invoice.cycle_id)
            set_committed_value(cycle, "period_end", datetime(2026, 9, 1, tzinfo=UTC))
        else:
            value = session.get(PointLotSettlementValueAllocation, line.value_allocation_id)
            set_committed_value(value, "receivable_basis_cents", 999)
        result = _run(session, key=f"invoice-document-corruption-{tamper}")
        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert result.run.completed_at is not None
        issue = next(item for item in result.exceptions if item.code == "INVOICE_DOCUMENT_CHAIN_INVALID")
        assert issue.entity_id == invoice.id
        assert issue.details["reason"]


@pytest.mark.parametrize("invoice_bound", [False, True])
def test_all_receivables_include_orphan_invoice_and_missing_payment_facts(app, invoice_bound):
    with app.state.session_factory() as session:
        company, invoice, _ = _seed_balanced_invoice_adjustment_chain(session)
        orphan = AccountsReceivableLedgerEntry(
            company_id=company.id,
            invoice_id=invoice.id if invoice_bound else None,
            payment_transaction_id=None,
            kind="INVOICE_PAYMENT_REFUND",
            debit_cents=1, credit_cents=0,
            idempotency_key="orphan-receivable-fact",
            note="unlinked accounting fact must be surfaced",
        )
        session.add(orphan)
        session.flush()
        result = _run(session, key=f"orphan-receivable-{invoice_bound}")
        expected_code = (
            "ACCOUNTS_RECEIVABLE_PAYMENT_FACT_MISSING" if invoice_bound
            else "ACCOUNTS_RECEIVABLE_ENTRY_ORPHANED"
        )
        issue = next(item for item in result.exceptions if item.code == expected_code)
        assert issue.entity_id == orphan.id
        assert issue.actual_amount == 1
        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS


def test_duplicate_settlement_and_missing_value_and_cost_are_immutable_exceptions(app):
    with app.state.session_factory() as session:
        company, good_task = _seed_balanced_company_chain(session)
        user_id = good_task.user_id
        model_id = good_task.model_id
        bad_task = GenerationTask(
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user_id,
            model_id=model_id,
            idempotency_key="reconciliation-bad-task",
            request_fingerprint="4" * 64,
            status=TaskStatus.SUCCEEDED,
            relay_job_id="33333333-3333-4333-8333-333333333331",
            request_payload={"prompt": "broken reconciliation fixture"},
            billing_unit=BillingUnit.POINT,
            billing_version=2,
            quote_cents=None,
            quote_points=10,
            pricing_snapshot={"points": 10},
            capability_snapshot={"revision": "fixture"},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=None,
            actual_cost_points=10,
            created_at=OCCURRED_AT + timedelta(minutes=1),
            updated_at=OCCURRED_AT + timedelta(minutes=1),
        )
        session.add(bad_task)
        session.flush()
        session.add(
            ChannelCostEntry(
                amount_cents=40,
                idempotency_key="bad-task-cross-period-cost",
                channel_key="official-test-channel",
                channel_type=ChannelType.OFFICIAL,
                occurred_at=PERIOD_START - timedelta(minutes=1),
                external_reference="bad-task-prior-period",
                company_id=company.id,
                personal_workspace_id=None,
                task_id=bad_task.id,
                relay_job_id=bad_task.relay_job_id,
                relay_event_id="33333333-3333-4333-8333-333333333332",
                relay_event_timestamp=PERIOD_START - timedelta(minutes=1),
                relay_payload_sha256="4" * 64,
                source=ChannelCostSource.RELAY,
                recorded_by_user_id=None,
                note="prior-period cost must not satisfy current task",
                created_at=PERIOD_START - timedelta(minutes=1),
            )
        )
        for suffix in ("a", "b"):
            session.add(
                CompanyPointLedgerEntry(
                    company_id=company.id,
                    kind=PointLedgerKind.SETTLE,
                    amount_points=5,
                    available_delta_points=0,
                    reserved_delta_points=-5,
                    reversal_reserved_delta_points=0,
                    debt_delta_points=0,
                    idempotency_key=f"bad-task-settle-{suffix}",
                    task_id=bad_task.id,
                    note="duplicate settlement fixture",
                    created_at=OCCURRED_AT + timedelta(minutes=1),
                )
            )
        session.flush()

        result = _run(session, key="broken-fact-chain")
        session.commit()

        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        codes = {item.code for item in result.exceptions}
        assert {
            "TASK_SETTLEMENT_DUPLICATE",
            "TASK_LOT_SETTLEMENT_MISMATCH",
            "TASK_VALUE_ALLOCATION_MISSING",
            "TASK_PROVIDER_COST_MISSING",
            "TASK_PROVIDER_COST_CROSS_PERIOD",
        } <= codes
        assert result.run.control_totals["total_exception_count"] == len(
            result.exceptions
        )
        immutable = result.exceptions[0]
        assert SHA256_PATTERN.fullmatch(immutable.evidence_sha256)

        immutable.code = "MUTATED"
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()
