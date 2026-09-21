from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    AccountsReceivableLedgerEntry,
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyInvoice,
    CompanyInvoiceLine,
    CompanyMembership,
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
    ModelDefinition,
    PaymentOrder,
    PaymentOrderStatus,
    PaymentPurpose,
    PaymentTransaction,
    PaymentTransactionKind,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    PointLotSourceKind,
    TaskPointLotAllocation,
    TaskStatus,
    User,
    UserAccountType,
    new_id,
)
from platform_api.services.enterprise_billing import EnterpriseBillingService
from platform_api.services.errors import ConflictError


def _seed_point_company(session) -> tuple[Company, User, ModelDefinition]:
    suffix = uuid.uuid4().hex
    company = Company(
        name=f"Enterprise monthly {suffix}",
        billing_version=2,
    )
    user = User(
        email=f"enterprise-monthly-{suffix}@example.test",
        display_name="Enterprise billing owner",
        account_type=UserAccountType.COMPANY,
    )
    model = ModelDefinition(
        slug=f"enterprise-monthly-{suffix}",
        display_name="Enterprise monthly model",
        provider_key="enterprise-monthly-test",
        billing_mode="per_item",
    )
    session.add_all([company, user, model])
    session.flush()
    session.add(CompanyMembership(company_id=company.id, user_id=user.id))
    session.add(
        CompanyPointWalletAccount(
            company_id=company.id,
            available_points=0,
            reserved_points=0,
            reversal_reserved_points=0,
            debt_points=0,
            migration_idempotency_key=f"native-v2:{company.id}",
            migrated_from_available_cents=0,
            migration_remainder_cents=0,
            migration_rounding_grant_points=0,
        )
    )
    session.flush()
    return company, user, model


def _activate(
    session,
    *,
    company: Company,
    user: User,
    effective_at: datetime,
    credit_limit_points: int = 100,
    contract_reference: str = "MSA-2026-001",
):
    return EnterpriseBillingService.activate_contract(
        session,
        company_id=company.id,
        contract_reference=contract_reference,
        currency="CNY",
        timezone_name="Asia/Shanghai",
        cycle_day=1,
        payment_terms_days=30,
        credit_limit_points=credit_limit_points,
        effective_at=effective_at,
        created_by_user_id=user.id,
    )


def _task(
    *,
    company: Company,
    user: User,
    model: ModelDefinition,
    points: int,
    key: str,
    status: TaskStatus,
    reserved_points: int = 0,
    actual_cost_points: int | None = None,
) -> GenerationTask:
    return GenerationTask(
        id=new_id(),
        company_id=company.id,
        personal_workspace_id=None,
        user_id=user.id,
        model_id=model.id,
        idempotency_key=key,
        request_fingerprint=(key.encode("utf-8").hex() + ("0" * 64))[:64],
        status=status,
        request_payload={"prompt": key, "output_count": 1},
        billing_unit=BillingUnit.POINT,
        billing_version=2,
        quote_cents=None,
        quote_points=points,
        pricing_snapshot={
            "schema_version": 2,
            "billing_unit": BillingUnit.POINT.value,
            "billing_version": 2,
            "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
            "unit_price_points": points,
            "quantity": 1,
            "quote_points": points,
        },
        capability_snapshot={},
        reserved_cents=0,
        reserved_points=reserved_points,
        actual_cost_cents=None,
        actual_cost_points=actual_cost_points,
    )


def _settle_contract_task(
    session,
    *,
    company: Company,
    user: User,
    model: ModelDefinition,
    lot: CompanyPointLot,
    points: int,
    key: str,
    settled_at: datetime,
) -> tuple[
    GenerationTask,
    CompanyPointLedgerEntry,
    PointLotSettlementValueAllocation,
]:
    wallet = session.get(CompanyPointWalletAccount, company.id)
    account = session.get(CompanyBillingAccount, company.id)
    assert wallet is not None
    assert account is not None
    assert lot.available_points >= points

    task = _task(
        company=company,
        user=user,
        model=model,
        points=points,
        key=key,
        status=TaskStatus.SUCCEEDED,
        actual_cost_points=points,
    )
    session.add(task)
    session.flush()
    allocation = TaskPointLotAllocation(
        id=new_id(),
        company_id=company.id,
        task_id=task.id,
        lot_id=lot.id,
        allocated_points=points,
        reserved_points=0,
        settled_points=points,
        released_points=0,
    )
    session.add(allocation)
    # These models intentionally expose immutable foreign-key ids without ORM
    # relationships. Flush parents explicitly so PostgreSQL cannot choose an
    # insert order that places the value allocation before its task allocation.
    session.flush()
    session.add(
        CompanyPointLedgerEntry(
            company_id=company.id,
            kind=PointLedgerKind.RESERVE,
            amount_points=points,
            available_delta_points=-points,
            reserved_delta_points=points,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=f"reserve:{key}",
            task_id=task.id,
        )
    )
    settle = CompanyPointLedgerEntry(
        id=new_id(),
        company_id=company.id,
        kind=PointLedgerKind.SETTLE,
        amount_points=points,
        available_delta_points=0,
        reserved_delta_points=-points,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key=f"settle:{key}",
        task_id=task.id,
        created_at=settled_at,
    )
    session.add(settle)
    session.flush()
    value = PointLotSettlementValueAllocation(
        id=new_id(),
        company_id=company.id,
        personal_workspace_id=None,
        task_id=task.id,
        company_task_allocation_id=allocation.id,
        personal_task_allocation_id=None,
        company_settle_ledger_id=settle.id,
        personal_settle_ledger_id=None,
        settled_points=points,
        cash_basis_cents=0,
        receivable_basis_cents=points * 10,
        subsidy_cents=0,
        created_at=settled_at,
    )
    session.add(value)
    lot.available_points -= points
    lot.settled_points += points
    wallet.available_points -= points
    account.unbilled_receivable_cents += points * 10
    session.flush()
    return task, settle, value


def _reserve_without_settlement(
    session,
    *,
    company: Company,
    user: User,
    model: ModelDefinition,
    lot: CompanyPointLot,
    points: int,
    key: str,
    reserved_at: datetime,
) -> GenerationTask:
    wallet = session.get(CompanyPointWalletAccount, company.id)
    assert wallet is not None
    task = _task(
        company=company,
        user=user,
        model=model,
        points=points,
        key=key,
        status=TaskStatus.QUEUED,
        reserved_points=points,
    )
    session.add(task)
    session.flush()
    session.add(
        TaskPointLotAllocation(
            company_id=company.id,
            task_id=task.id,
            lot_id=lot.id,
            allocated_points=points,
            reserved_points=points,
            settled_points=0,
            released_points=0,
        )
    )
    session.add(
        CompanyPointLedgerEntry(
            company_id=company.id,
            kind=PointLedgerKind.RESERVE,
            amount_points=points,
            available_delta_points=-points,
            reserved_delta_points=points,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=f"reserve:{key}",
            task_id=task.id,
            created_at=reserved_at,
        )
    )
    lot.available_points -= points
    lot.reserved_points += points
    wallet.available_points -= points
    wallet.reserved_points += points
    session.flush()
    return task


def _invoice_capture(
    session,
    *,
    invoice: CompanyInvoice,
    company: Company,
    user: User,
    amount_cents: int,
    key: str,
    occurred_at: datetime,
) -> PaymentTransaction:
    order = PaymentOrder(
        id=new_id(),
        company_id=company.id,
        personal_workspace_id=None,
        created_by_user_id=user.id,
        purpose=PaymentPurpose.INVOICE_PAYMENT,
        purpose_reference_id=invoice.id,
        provider="test-pay",
        merchant_account="merchant-cny",
        provider_order_id=f"provider-{key}",
        status=PaymentOrderStatus.PAID,
        currency="CNY",
        amount_cents=amount_cents,
        points=0,
        captured_amount_cents=amount_cents,
        refunded_amount_cents=0,
        disputed_amount_cents=0,
        fee_amount_cents=0,
        idempotency_key=f"order-{key}",
        request_fingerprint=(key.encode("utf-8").hex() + ("0" * 64))[:64],
    )
    session.add(order)
    session.flush()
    transaction = PaymentTransaction(
        id=new_id(),
        order_id=order.id,
        provider=order.provider,
        provider_transaction_id=f"capture-{key}",
        kind=PaymentTransactionKind.CAPTURE,
        amount_cents=amount_cents,
        currency="CNY",
        occurred_at=occurred_at,
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_contract_activation_is_idempotent_and_links_immutable_versions(app) -> None:
    effective = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        first, account, created = _activate(
            session,
            company=company,
            user=user,
            effective_at=effective,
        )
        assert created is True
        replay, replay_account, replay_created = _activate(
            session,
            company=company,
            user=user,
            effective_at=effective,
        )
        assert replay_created is False
        assert replay.id == first.id
        assert replay_account.active_contract_version_id == first.id

        second, updated_account, second_created = _activate(
            session,
            company=company,
            user=user,
            effective_at=effective + timedelta(days=31),
            credit_limit_points=250,
            contract_reference="MSA-2026-002",
        )
        assert second_created is True
        assert second.supersedes_version_id == first.id
        assert updated_account.active_contract_version_id == second.id
        assert first.status == EnterpriseContractStatus.ACTIVE
        assert session.scalar(
            select(func.count(CompanyBillingContractVersion.id)).where(
                CompanyBillingContractVersion.company_id == company.id
            )
        ) == 2


def test_contract_activation_rejects_legacy_cents_company(app) -> None:
    effective = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        suffix = uuid.uuid4().hex
        company = Company(name=f"Legacy enterprise {suffix}", billing_version=1)
        user = User(
            email=f"legacy-enterprise-{suffix}@example.test",
            display_name="Legacy enterprise owner",
            account_type=UserAccountType.COMPANY,
        )
        session.add_all([company, user])
        session.flush()
        with pytest.raises(ConflictError, match="POINT/v2"):
            _activate(
                session,
                company=company,
                user=user,
                effective_at=effective,
            )


def test_open_cycle_is_idempotent_and_contract_credit_is_not_cash(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        contract, _, _ = _activate(
            session,
            company=company,
            user=user,
            effective_at=start,
        )
        cycle, lot, created = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )
        assert created is True
        assert lot is not None
        assert lot.source_kind == PointLotSourceKind.CONTRACT
        assert lot.contract_version_id == contract.id
        assert lot.billing_cycle_id == cycle.id
        assert lot.original_points == 100
        assert lot.cash_basis_cents == 0
        assert lot.receivable_basis_cents == 1_000
        assert lot.subsidy_cents == 0
        wallet = session.get(CompanyPointWalletAccount, company.id)
        assert wallet is not None
        assert wallet.available_points == 100

        replay_cycle, replay_lot, replay_created = (
            EnterpriseBillingService.open_cycle(
                session,
                company_id=company.id,
                period_start=start,
                period_end=end,
            )
        )
        assert replay_created is False
        assert replay_cycle.id == cycle.id
        assert replay_lot is not None and replay_lot.id == lot.id
        assert session.scalar(
            select(func.count(CompanyPointLot.id)).where(
                CompanyPointLot.company_id == company.id,
                CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
            )
        ) == 1


def test_cycle_close_uses_half_open_settlements_and_replays_one_invoice(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(
            session,
            company=company,
            user=user,
            effective_at=start,
        )
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )
        assert lot is not None
        _, _, at_start = _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="at-start",
            settled_at=start,
        )
        _, _, before_end = _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=5,
            key="before-end",
            settled_at=end - timedelta(microseconds=1),
        )
        _, _, at_end = _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=7,
            key="at-end",
            settled_at=end,
        )
        session.add(
            _task(
                company=company,
                user=user,
                model=model,
                points=4,
                key="failed-no-charge",
                status=TaskStatus.FAILED,
            )
        )
        _reserve_without_settlement(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=3,
            key="reserved-no-charge",
            reserved_at=end - timedelta(hours=1),
        )

        closed_cycle, invoice, lines, issued = (
            EnterpriseBillingService.close_and_issue_cycle(
                session,
                company_id=company.id,
                cycle_id=cycle.id,
                issued_at=end,
            )
        )
        assert issued is True
        assert closed_cycle.status == EnterpriseBillingCycleStatus.ISSUED
        assert invoice.status == EnterpriseInvoiceStatus.ISSUED
        assert invoice.subtotal_cents == 150
        assert invoice.total_cents == 150
        assert {line.value_allocation_id for line in lines} == {
            at_start.id,
            before_end.id,
        }
        assert at_end.id not in {line.value_allocation_id for line in lines}
        assert sum(line.points for line in lines) == 15
        assert sum(line.amount_cents for line in lines) == 150
        account = session.get(CompanyBillingAccount, company.id)
        assert account is not None
        assert account.unbilled_receivable_cents == 70
        debit = session.scalar(
            select(AccountsReceivableLedgerEntry).where(
                AccountsReceivableLedgerEntry.invoice_id == invoice.id,
                AccountsReceivableLedgerEntry.kind == "INVOICE_ISSUED",
            )
        )
        assert debit is not None and debit.debit_cents == 150

        _, replay_invoice, replay_lines, replay_issued = (
            EnterpriseBillingService.close_and_issue_cycle(
                session,
                company_id=company.id,
                cycle_id=cycle.id,
                issued_at=end + timedelta(minutes=1),
            )
        )
        assert replay_issued is False
        assert replay_invoice.id == invoice.id
        assert [line.id for line in replay_lines] == [line.id for line in lines]
        assert session.scalar(
            select(func.count(CompanyInvoice.id)).where(
                CompanyInvoice.cycle_id == cycle.id
            )
        ) == 1
        assert session.scalar(
            select(func.count(CompanyInvoiceLine.id)).where(
                CompanyInvoiceLine.invoice_id == invoice.id
            )
        ) == 2


def test_invoice_payment_is_idempotent_and_rejects_currency_and_overpayment(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(
            session,
            company=company,
            user=user,
            effective_at=start,
        )
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )
        assert lot is not None
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="invoice-payment-task",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=end,
        )

        partial_order = PaymentOrder(
            id=new_id(),
            company_id=company.id,
            personal_workspace_id=None,
            created_by_user_id=user.id,
            purpose=PaymentPurpose.INVOICE_PAYMENT,
            purpose_reference_id=invoice.id,
            provider="test-pay",
            merchant_account="merchant-cny",
            provider_order_id="provider-order-partial",
            status=PaymentOrderStatus.PAID,
            currency="CNY",
            amount_cents=60,
            points=0,
            captured_amount_cents=60,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key="invoice-partial-order",
            request_fingerprint="a" * 64,
        )
        session.add(partial_order)
        session.flush()
        partial_tx = PaymentTransaction(
            id=new_id(),
            order_id=partial_order.id,
            provider="test-pay",
            provider_transaction_id="capture-partial",
            kind=PaymentTransactionKind.CAPTURE,
            amount_cents=60,
            currency="CNY",
            occurred_at=end + timedelta(hours=1),
        )
        session.add(partial_tx)
        session.flush()

        paid_invoice, payment_entry, applied = (
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=invoice.id,
                payment_transaction_id=partial_tx.id,
            )
        )
        assert applied is True
        assert paid_invoice.paid_cents == 60
        assert paid_invoice.status == EnterpriseInvoiceStatus.PARTIALLY_PAID
        assert payment_entry.credit_cents == 60
        replay_invoice, replay_entry, replay_applied = (
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=invoice.id,
                payment_transaction_id=partial_tx.id,
            )
        )
        assert replay_applied is False
        assert replay_invoice.paid_cents == 60
        assert replay_entry.id == payment_entry.id

        over_order = PaymentOrder(
            id=new_id(),
            company_id=company.id,
            personal_workspace_id=None,
            created_by_user_id=user.id,
            purpose=PaymentPurpose.INVOICE_PAYMENT,
            purpose_reference_id=invoice.id,
            provider="test-pay",
            merchant_account="merchant-cny",
            provider_order_id="provider-order-over",
            status=PaymentOrderStatus.PAID,
            currency="CNY",
            amount_cents=41,
            points=0,
            captured_amount_cents=41,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key="invoice-over-order",
            request_fingerprint="b" * 64,
        )
        session.add(over_order)
        session.flush()
        over_tx = PaymentTransaction(
            id=new_id(),
            order_id=over_order.id,
            provider="test-pay",
            provider_transaction_id="capture-over",
            kind=PaymentTransactionKind.CAPTURE,
            amount_cents=41,
            currency="CNY",
            occurred_at=end + timedelta(hours=2),
        )
        session.add(over_tx)
        session.flush()
        with pytest.raises(ConflictError, match="超过企业发票未付金额"):
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=invoice.id,
                payment_transaction_id=over_tx.id,
            )

        # CompanyInvoice is a mutable projection. Deliberately corrupt its
        # currency in-memory to prove the service refuses a CNY transaction
        # instead of silently applying it to a differently denominated bill.
        invoice.currency = "USD"
        wrong_currency_order = PaymentOrder(
            id=new_id(),
            company_id=company.id,
            personal_workspace_id=None,
            created_by_user_id=user.id,
            purpose=PaymentPurpose.INVOICE_PAYMENT,
            purpose_reference_id=invoice.id,
            provider="test-pay",
            merchant_account="merchant-cny",
            provider_order_id="provider-order-currency",
            status=PaymentOrderStatus.PAID,
            currency="CNY",
            amount_cents=40,
            points=0,
            captured_amount_cents=40,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key="invoice-currency-order",
            request_fingerprint="c" * 64,
        )
        session.add(wrong_currency_order)
        session.flush()
        wrong_currency_tx = PaymentTransaction(
            id=new_id(),
            order_id=wrong_currency_order.id,
            provider="test-pay",
            provider_transaction_id="capture-currency",
            kind=PaymentTransactionKind.CAPTURE,
            amount_cents=40,
            currency="CNY",
            occurred_at=end + timedelta(hours=3),
        )
        session.add(wrong_currency_tx)
        session.flush()
        with pytest.raises(ConflictError, match="币种"):
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=invoice.id,
                payment_transaction_id=wrong_currency_tx.id,
            )
        invoice.currency = "CNY"

        final_order = PaymentOrder(
            id=new_id(),
            company_id=company.id,
            personal_workspace_id=None,
            created_by_user_id=user.id,
            purpose=PaymentPurpose.INVOICE_PAYMENT,
            purpose_reference_id=invoice.id,
            provider="test-pay",
            merchant_account="merchant-cny",
            provider_order_id="provider-order-final",
            status=PaymentOrderStatus.PAID,
            currency="CNY",
            amount_cents=40,
            points=0,
            captured_amount_cents=40,
            refunded_amount_cents=0,
            disputed_amount_cents=0,
            fee_amount_cents=0,
            idempotency_key="invoice-final-order",
            request_fingerprint="d" * 64,
        )
        session.add(final_order)
        session.flush()
        final_tx = PaymentTransaction(
            id=new_id(),
            order_id=final_order.id,
            provider="test-pay",
            provider_transaction_id="capture-final",
            kind=PaymentTransactionKind.CAPTURE,
            amount_cents=40,
            currency="CNY",
            occurred_at=end + timedelta(hours=4),
        )
        session.add(final_tx)
        session.flush()
        final_invoice, _, final_applied = (
            EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=invoice.id,
                payment_transaction_id=final_tx.id,
            )
        )
        assert final_applied is True
        assert final_invoice.paid_cents == 100
        assert final_invoice.status == EnterpriseInvoiceStatus.PAID
        assert cycle.status == EnterpriseBillingCycleStatus.PAID
        wallet = session.get(CompanyPointWalletAccount, company.id)
        assert wallet is not None
        # Applying invoice cash never mints another point lot or wallet credit.
        assert wallet.available_points == 90

        final_order.refunded_amount_cents = 20
        fabricated_refund = PaymentTransaction(
            id=new_id(),
            order_id=final_order.id,
            refund_id=None,
            provider="test-pay",
            provider_transaction_id="fabricated-refund-without-refund-fact",
            kind=PaymentTransactionKind.REFUND,
            amount_cents=20,
            currency="CNY",
            occurred_at=end + timedelta(hours=5),
        )
        session.add(fabricated_refund)
        session.flush()
        with pytest.raises(ConflictError, match="原始退款事实"):
            EnterpriseBillingService.apply_payment_reversal(
                session,
                invoice_id=invoice.id,
                payment_transaction_id=fabricated_refund.id,
            )
        assert invoice.paid_cents == 100


def test_cycle_boundaries_follow_contract_day_and_cannot_overlap(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )

        with pytest.raises(ConflictError, match="重叠"):
            EnterpriseBillingService.open_cycle(
                session,
                company_id=company.id,
                period_start=start + timedelta(days=15),
                period_end=end + timedelta(days=15),
            )
        with pytest.raises(ConflictError, match="cycle_day"):
            EnterpriseBillingService.open_cycle(
                session,
                company_id=company.id,
                period_start=end + timedelta(days=1),
                period_end=datetime(2026, 10, 2, tzinfo=timezone.utc),
            )
        with pytest.raises(ConflictError, match="自然月"):
            EnterpriseBillingService.open_cycle(
                session,
                company_id=company.id,
                period_start=end,
                period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
            )


def test_cycle_cannot_issue_early_and_replay_fails_closed_on_state_drift(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, _, _ = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )

        with pytest.raises(ConflictError, match="结束前禁止开票"):
            EnterpriseBillingService.close_and_issue_cycle(
                session,
                company_id=company.id,
                cycle_id=cycle.id,
                issued_at=end - timedelta(microseconds=1),
            )
        assert session.scalar(
            select(func.count(CompanyInvoice.id)).where(
                CompanyInvoice.cycle_id == cycle.id
            )
        ) == 0

        _, invoice, _, issued = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=end,
        )
        assert issued is True
        assert invoice.status == EnterpriseInvoiceStatus.PAID
        cycle.status = EnterpriseBillingCycleStatus.OPEN
        with pytest.raises(ConflictError, match="发票与账期状态"):
            EnterpriseBillingService.close_and_issue_cycle(
                session,
                company_id=company.id,
                cycle_id=cycle.id,
                issued_at=end,
            )


def test_dunning_run_is_durable_idempotent_and_holds_overdue_company(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )
        assert lot is not None
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="dunning-task",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=end,
        )
        assert invoice.due_at is not None
        run, actions, created = EnterpriseBillingService.run_dunning(
            session,
            company_id=company.id,
            as_of=invoice.due_at,
            idempotency_key="dunning-2026-10-company",
        )
        assert created is True
        assert run.status == EnterpriseDunningRunStatus.COMPLETED
        assert (run.scanned_count, run.overdue_count, run.hold_count) == (1, 1, 1)
        assert {action.action for action in actions} == {
            "mark_overdue",
            "apply_hold",
        }
        assert invoice.status == EnterpriseInvoiceStatus.OVERDUE
        assert cycle.status == EnterpriseBillingCycleStatus.OVERDUE
        account = session.get(CompanyBillingAccount, company.id)
        assert account is not None
        assert account.billing_hold is True
        assert account.billing_hold_reason == "delinquent_invoice"
        assert account.billing_hold_since is not None
        assert account.dunning_level == 1

        replay, replay_actions, replay_created = EnterpriseBillingService.run_dunning(
            session,
            company_id=company.id,
            as_of=invoice.due_at,
            idempotency_key="dunning-2026-10-company",
        )
        assert replay_created is False
        assert replay.id == run.id
        assert {action.id for action in replay_actions} == {
            action.id for action in actions
        }
        with pytest.raises(ConflictError, match="另一意图"):
            EnterpriseBillingService.run_dunning(
                session,
                company_id=company.id,
                as_of=invoice.due_at + timedelta(seconds=1),
                idempotency_key="dunning-2026-10-company",
            )
        assert session.scalar(select(func.count(EnterpriseDunningRun.id))) == 1
        assert session.scalar(select(func.count(EnterpriseDunningAction.id))) == 2


def test_overdue_partial_payment_stays_overdue_and_final_payment_clears_hold(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=start,
            period_end=end,
        )
        assert lot is not None
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="overdue-payment-task",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=end,
        )
        assert invoice.due_at is not None
        EnterpriseBillingService.run_dunning(
            session,
            company_id=company.id,
            as_of=invoice.due_at,
            idempotency_key="overdue-payment-dunning",
        )

        partial = _invoice_capture(
            session,
            invoice=invoice,
            company=company,
            user=user,
            amount_cents=40,
            key="overdue-partial",
            occurred_at=invoice.due_at + timedelta(hours=1),
        )
        partial_invoice, _, _ = EnterpriseBillingService.apply_payment_transaction(
            session,
            invoice_id=invoice.id,
            payment_transaction_id=partial.id,
        )
        assert partial_invoice.paid_cents == 40
        assert partial_invoice.status == EnterpriseInvoiceStatus.OVERDUE
        assert cycle.status == EnterpriseBillingCycleStatus.OVERDUE
        account = session.get(CompanyBillingAccount, company.id)
        assert account is not None and account.billing_hold is True

        final = _invoice_capture(
            session,
            invoice=invoice,
            company=company,
            user=user,
            amount_cents=60,
            key="overdue-final",
            occurred_at=invoice.due_at + timedelta(hours=2),
        )
        final_invoice, _, _ = EnterpriseBillingService.apply_payment_transaction(
            session,
            invoice_id=invoice.id,
            payment_transaction_id=final.id,
        )
        assert final_invoice.paid_cents == 100
        assert final_invoice.status == EnterpriseInvoiceStatus.PAID
        assert cycle.status == EnterpriseBillingCycleStatus.PAID
        assert account.billing_hold is False
        assert account.billing_hold_reason is None
        assert account.billing_hold_since is None
        assert account.dunning_level == 0
