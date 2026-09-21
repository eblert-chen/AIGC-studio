from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    AccountsReceivableLedgerEntry,
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    EnterpriseBillingCycleStatus,
    EnterpriseContractStatus,
    EnterpriseInvoiceStatus,
    PaymentDispute,
    PaymentDisputeStatus,
    PaymentOrder,
    PaymentRefund,
    PaymentRefundStatus,
    PaymentTransaction,
    PaymentTransactionKind,
    PointLedgerKind,
    PointLotSourceKind,
    TaskPointLotAllocation,
    TaskStatus,
    new_id,
)
from platform_api.services import company_points_billing, enterprise_billing
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.enterprise_billing import EnterpriseBillingService
from platform_api.services.errors import ConflictError

from .test_enterprise_monthly_billing import (
    _activate,
    _invoice_capture,
    _seed_point_company,
    _settle_contract_task,
    _task,
)


START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _credit_wallet(session, *, condition="valid"):
    company, user, model = _seed_point_company(session)
    contract = CompanyBillingContractVersion(
        id=new_id(), company_id=company.id,
        status=(
            EnterpriseContractStatus.TERMINATED
            if condition == "terminated"
            else EnterpriseContractStatus.SUPERSEDED
            if condition == "superseded"
            else EnterpriseContractStatus.ACTIVE
        ),
        contract_reference="credit-admission", currency="CNY",
        timezone_name="Asia/Shanghai", cycle_day=1, payment_terms_days=30,
        credit_limit_points=100, receivable_per_point_cents=10,
        content_sha256="1" * 64, effective_at=START,
        expires_at=None if condition == "cycle_ended" else END,
        created_by_user_id=user.id,
    )
    session.add(contract)
    session.flush()
    account = None
    if condition != "no_account":
        account = CompanyBillingAccount(
            company_id=company.id, active_contract_version_id=contract.id,
            unbilled_receivable_cents=0,
            billing_hold=condition == "held",
            billing_hold_reason="delinquent_invoice" if condition == "held" else None,
            billing_hold_since=START if condition == "held" else None,
            dunning_level=1 if condition == "held" else 0,
        )
        session.add(account)
    cycle = None
    if condition != "no_cycle":
        cycle = CompanyBillingCycle(
            id=new_id(), company_id=company.id, contract_version_id=contract.id,
            period_start=START, period_end=END,
            status=(
                EnterpriseBillingCycleStatus.CLOSED
                if condition == "closed"
                else EnterpriseBillingCycleStatus.FROZEN
                if condition == "frozen"
                else EnterpriseBillingCycleStatus.OPEN
            ),
            frozen_at=START if condition == "frozen" else None,
        )
        session.add(cycle)
        session.flush()
    lot = CompanyPointLot(
        company_id=company.id, source_kind=PointLotSourceKind.CONTRACT,
        original_points=100, available_points=100, reserved_points=0,
        reversal_reserved_points=0, settled_points=0, reversed_points=0,
        cash_basis_cents=0, receivable_basis_cents=1000, subsidy_cents=0,
        idempotency_key="contract-credit", expires_at=None,
        contract_version_id=contract.id, billing_cycle_id=cycle.id if cycle else None,
    )
    wallet = session.get(CompanyPointWalletAccount, company.id)
    wallet.available_points = 100
    session.add(lot)
    session.add(CompanyPointLedgerEntry(
        company_id=company.id, kind=PointLedgerKind.CREDIT,
        amount_points=100, available_delta_points=100, reserved_delta_points=0,
        idempotency_key="contract-credit",
    ))
    session.flush()
    return company, user, model, account, contract, cycle, lot, wallet


def _reserve_task(session, company, user, model, *, key="admission", points=10):
    task = _task(company=company, user=user, model=model, points=points,
                 key=key, status=TaskStatus.DRAFT)
    session.add(task)
    session.flush()
    return task


@pytest.mark.parametrize("condition, now", [
    ("valid", END), ("cycle_ended", END),
    ("valid", START - timedelta(microseconds=1)),
    ("no_cycle", START), ("closed", START), ("frozen", START),
    ("terminated", START), ("superseded", START), ("no_account", START),
    ("held", START),
])
def test_new_contract_reserve_requires_current_contract_and_open_cycle(
    app, monkeypatch, condition, now,
):
    monkeypatch.setattr(company_points_billing, "utcnow", lambda: now)
    with app.state.session_factory.begin() as session:
        company, user, model, account, _, _, lot, wallet = _credit_wallet(
            session, condition=condition
        )
        task = _reserve_task(session, company, user, model)
        with pytest.raises(ConflictError, match="合同授信积分"):
            CompanyPointBillingService.reserve(
                session, company_id=company.id, task_id=task.id,
                amount_points=10, idempotency_key="reserve-admission",
            )
        assert (wallet.available_points, wallet.reserved_points) == (100, 0)
        assert (lot.available_points, lot.reserved_points) == (100, 0)
        assert task.status == TaskStatus.DRAFT
        assert account is None or account.unbilled_receivable_cents == 0
        assert session.scalar(select(func.count(TaskPointLotAllocation.id))) == 0


@pytest.mark.parametrize("now", [START, END - timedelta(microseconds=1)])
def test_contract_cycle_admission_uses_half_open_utc_boundaries(app, monkeypatch, now):
    monkeypatch.setattr(company_points_billing, "utcnow", lambda: now)
    with app.state.session_factory.begin() as session:
        company, user, model, _, _, _, lot, wallet = _credit_wallet(session)
        task = _reserve_task(session, company, user, model)
        CompanyPointBillingService.reserve(
            session, company_id=company.id, task_id=task.id,
            amount_points=10, idempotency_key="reserve-admission",
        )
        assert (wallet.available_points, wallet.reserved_points) == (90, 10)
        assert (lot.available_points, lot.reserved_points) == (90, 10)


def test_expired_contract_does_not_confiscate_purchased_points(app, monkeypatch):
    monkeypatch.setattr(company_points_billing, "utcnow", lambda: END)
    with app.state.session_factory.begin() as session:
        company, user, model, _, _, _, contract_lot, wallet = _credit_wallet(session)
        paid = CompanyPointLot(
            company_id=company.id, source_kind=PointLotSourceKind.PURCHASED,
            original_points=10, available_points=10, cash_basis_cents=100,
            receivable_basis_cents=0, subsidy_cents=0,
            idempotency_key="owned-cash", expires_at=None,
        )
        session.add(paid)
        wallet.available_points += 10
        task = _reserve_task(session, company, user, model)
        CompanyPointBillingService.reserve(
            session, company_id=company.id, task_id=task.id,
            amount_points=10, idempotency_key="reserve-owned",
        )
        allocations = session.scalars(select(TaskPointLotAllocation)).all()
        assert [row.lot_id for row in allocations] == [paid.id]
        assert contract_lot.available_points == 100
        assert wallet.available_points == 100


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
def test_expiry_and_hold_preserve_inflight_reserve_replay_and_terminal_settlement(
    app, monkeypatch, outcome,
):
    monkeypatch.setattr(company_points_billing, "utcnow", lambda: START)
    with app.state.session_factory.begin() as session:
        company, user, model, account, _, _, lot, wallet = _credit_wallet(session)
        task = _reserve_task(session, company, user, model)
        _, original = CompanyPointBillingService.reserve(
            session, company_id=company.id, task_id=task.id,
            amount_points=10, idempotency_key="reserve-admission",
        )
        monkeypatch.setattr(company_points_billing, "utcnow", lambda: END)
        account.billing_hold = True
        account.billing_hold_reason = "delinquent_invoice"
        account.billing_hold_since = END
        account.dunning_level = 1
        session.flush()
        _, replay = CompanyPointBillingService.reserve(
            session, company_id=company.id, task_id=task.id,
            amount_points=10, idempotency_key="reserve-admission",
        )
        assert replay.id == original.id
        if outcome == "success":
            CompanyPointBillingService.settle_success(
                session, company_id=company.id, task_id=task.id,
                actual_cost_points=10, idempotency_key="settle-admission",
            )
            assert account.unbilled_receivable_cents == 100
            assert lot.settled_points == 10
        else:
            CompanyPointBillingService.release_failure(
                session, company_id=company.id, task_id=task.id,
                idempotency_key="release-admission", failure_reason=outcome,
                terminal_status=(TaskStatus.FAILED if outcome == "failed"
                                 else TaskStatus.CANCELLED),
            )
            assert account.unbilled_receivable_cents == 0
            assert lot.available_points == 100
        assert wallet.reserved_points == 0


def _issue_invoice(session, company, user, model, *, start=START, end=END, key="one"):
    cycle, lot, _ = EnterpriseBillingService.open_cycle(
        session, company_id=company.id, period_start=start, period_end=end,
    )
    if lot is None:
        lot = session.scalar(select(CompanyPointLot).where(
            CompanyPointLot.company_id == company.id,
            CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
            CompanyPointLot.available_points >= 10,
        ))
    _settle_contract_task(session, company=company, user=user, model=model,
                         lot=lot, points=10, key=key,
                         settled_at=start + timedelta(days=1))
    _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
        session, company_id=company.id, cycle_id=cycle.id, issued_at=end,
    )
    return cycle, invoice


def _invoice_context(session):
    company, user, model = _seed_point_company(session)
    _activate(session, company=company, user=user, effective_at=START)
    cycle, invoice = _issue_invoice(session, company, user, model)
    account = session.get(CompanyBillingAccount, company.id)
    return company, user, model, cycle, invoice, account


@pytest.mark.parametrize("processing_delta, expected", [
    (timedelta(microseconds=-1), EnterpriseInvoiceStatus.PARTIALLY_PAID),
    (timedelta(0), EnterpriseInvoiceStatus.OVERDUE),
    (timedelta(days=2), EnterpriseInvoiceStatus.OVERDUE),
])
def test_delayed_capture_uses_current_due_risk_and_preserves_provider_time(
    app, monkeypatch, processing_delta, expected,
):
    with app.state.session_factory.begin() as session:
        company, user, _, cycle, invoice, account = _invoice_context(session)
        processing_time = (_utc(invoice.due_at) + processing_delta).astimezone(
            timezone(timedelta(hours=8))
        )
        monkeypatch.setattr(enterprise_billing, "utcnow", lambda: processing_time)
        original_time = _utc(invoice.issued_at) + timedelta(hours=1)
        capture = _invoice_capture(
            session, invoice=invoice, company=company, user=user, amount_cents=40,
            key="late-partial", occurred_at=original_time,
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=invoice.id, payment_transaction_id=capture.id,
        )
        assert invoice.status == expected
        assert invoice.paid_cents == 40
        assert account.billing_hold == (expected == EnterpriseInvoiceStatus.OVERDUE)
        assert capture.occurred_at == original_time
        if account.billing_hold:
            assert account.billing_hold_since == processing_time
        assert cycle.status == (EnterpriseBillingCycleStatus.OVERDUE
                                if account.billing_hold else EnterpriseBillingCycleStatus.ISSUED)


def test_proven_overdue_cannot_be_undone_by_late_capture_or_a_backward_clock(
    app, monkeypatch,
):
    with app.state.session_factory.begin() as session:
        company, user, _, _, invoice, account = _invoice_context(session)
        EnterpriseBillingService.run_dunning(
            session, company_id=company.id, as_of=_utc(invoice.due_at),
            idempotency_key="proven-overdue",
        )
        monkeypatch.setattr(enterprise_billing, "utcnow",
                            lambda: _utc(invoice.due_at) - timedelta(seconds=1))
        capture = _invoice_capture(
            session, invoice=invoice, company=company, user=user, amount_cents=40,
            key="old-capture", occurred_at=invoice.issued_at + timedelta(hours=1),
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=invoice.id, payment_transaction_id=capture.id,
        )
        assert invoice.status == EnterpriseInvoiceStatus.OVERDUE
        assert account.billing_hold
        # The next run does not try to repeat an already immutable mark_overdue action.
        EnterpriseBillingService.run_dunning(
            session, company_id=company.id, as_of=_utc(invoice.due_at) + timedelta(days=1),
            idempotency_key="proven-overdue-next",
        )


def _reversal(session, capture, user, *, kind, amount, occurred_at, key):
    order = session.get(PaymentOrder, capture.order_id)
    kwargs = {}
    if kind == PaymentTransactionKind.REFUND:
        refund = PaymentRefund(
            id=new_id(), order_id=order.id, provider=order.provider,
            provider_refund_id=key, status=PaymentRefundStatus.SUCCEEDED,
            amount_cents=amount, points=0, currency="CNY", reason="refund",
            idempotency_key=key, request_fingerprint="5" * 64,
            requested_by_user_id=user.id, completed_at=occurred_at,
        )
        session.add(refund)
        order.refunded_amount_cents += amount
        kwargs["refund_id"] = refund.id
    else:
        dispute = PaymentDispute(
            id=new_id(), order_id=order.id, provider=order.provider,
            provider_dispute_id=key, status=PaymentDisputeStatus.OPEN,
            amount_cents=amount, points=0, currency="CNY", reason_code="test",
            recovered_available_points=0, debt_points=0, opened_at=occurred_at,
        )
        session.add(dispute)
        order.disputed_amount_cents += amount
        kwargs["dispute_id"] = dispute.id
    session.flush()
    transaction = PaymentTransaction(
        id=new_id(), order_id=order.id, provider=order.provider,
        provider_transaction_id=key, kind=kind, amount_cents=amount,
        currency="CNY", occurred_at=occurred_at, **kwargs,
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_late_refund_reopens_overdue_receivable_at_processing_time(app, monkeypatch):
    with app.state.session_factory.begin() as session:
        company, user, _, cycle, invoice, account = _invoice_context(session)
        event_time = _utc(invoice.issued_at) + timedelta(hours=1)
        monkeypatch.setattr(enterprise_billing, "utcnow", lambda: event_time)
        capture = _invoice_capture(
            session, invoice=invoice, company=company, user=user, amount_cents=100,
            key="paid-before-due", occurred_at=event_time,
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=invoice.id, payment_transaction_id=capture.id,
        )
        assert invoice.status == EnterpriseInvoiceStatus.PAID
        processing_time = _utc(invoice.due_at) + timedelta(days=1)
        monkeypatch.setattr(enterprise_billing, "utcnow", lambda: processing_time)
        refund = _reversal(session, capture, user, kind=PaymentTransactionKind.REFUND,
                           amount=40, occurred_at=event_time + timedelta(hours=1),
                           key="late-refund")
        EnterpriseBillingService.apply_payment_reversal(
            session, invoice_id=invoice.id, payment_transaction_id=refund.id,
        )
        assert (invoice.paid_cents, invoice.status) == (60, EnterpriseInvoiceStatus.OVERDUE)
        assert cycle.status == EnterpriseBillingCycleStatus.OVERDUE
        assert account.billing_hold and account.billing_hold_since == processing_time


@pytest.mark.parametrize("capture_amount", [40, 100])
def test_late_dispute_win_preserves_remaining_overdue_then_full_payment_clears(
    app, monkeypatch, capture_amount,
):
    with app.state.session_factory.begin() as session:
        company, user, _, cycle, invoice, account = _invoice_context(session)
        event_time = _utc(invoice.issued_at) + timedelta(hours=1)
        monkeypatch.setattr(enterprise_billing, "utcnow", lambda: event_time)
        capture = _invoice_capture(
            session, invoice=invoice, company=company, user=user, amount_cents=capture_amount,
            key="disputed-partial", occurred_at=event_time,
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=invoice.id, payment_transaction_id=capture.id,
        )
        chargeback = _reversal(
            session, capture, user, kind=PaymentTransactionKind.CHARGEBACK,
            amount=capture_amount, occurred_at=event_time + timedelta(hours=1), key="chargeback",
        )
        EnterpriseBillingService.apply_payment_reversal(
            session, invoice_id=invoice.id, payment_transaction_id=chargeback.id,
        )
        assert invoice.status == EnterpriseInvoiceStatus.DISPUTED
        dispute = session.get(PaymentDispute, chargeback.dispute_id)
        won_time = event_time + timedelta(hours=2)
        dispute.status = PaymentDisputeStatus.WON
        dispute.closed_at = won_time
        session.get(PaymentOrder, capture.order_id).disputed_amount_cents = 0
        won = PaymentTransaction(
            id=new_id(), order_id=capture.order_id, dispute_id=dispute.id,
            provider=capture.provider, provider_transaction_id="late-win",
            kind=PaymentTransactionKind.DISPUTE_REVERSAL, amount_cents=capture_amount,
            currency="CNY", occurred_at=won_time,
        )
        session.add(won)
        session.flush()
        processing_time = _utc(invoice.due_at) + timedelta(days=1)
        monkeypatch.setattr(enterprise_billing, "utcnow", lambda: processing_time)
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=invoice.id, payment_transaction_id=won.id,
        )
        if capture_amount == 100:
            assert invoice.status == EnterpriseInvoiceStatus.PAID
            assert cycle.status == EnterpriseBillingCycleStatus.PAID
            assert invoice.paid_cents == 100
            assert not account.billing_hold
            return
        assert (invoice.paid_cents, invoice.status) == (40, EnterpriseInvoiceStatus.OVERDUE)
        assert cycle.status == EnterpriseBillingCycleStatus.OVERDUE
        assert account.billing_hold
        final = _invoice_capture(
            session, invoice=invoice, company=company, user=user, amount_cents=60,
            key="late-final", occurred_at=won_time + timedelta(hours=1),
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=invoice.id, payment_transaction_id=final.id,
        )
        assert invoice.status == EnterpriseInvoiceStatus.PAID
        assert not account.billing_hold
        assert account.dunning_level == 0


def test_full_payment_of_one_invoice_keeps_hold_for_other_due_invoice_without_worker(
    app, monkeypatch,
):
    with app.state.session_factory.begin() as session:
        company, user, model, _, first, account = _invoice_context(session)
        _, second = _issue_invoice(
            session, company, user, model, start=END,
            end=datetime(2026, 10, 1, tzinfo=timezone.utc), key="second",
        )
        now = _utc(second.due_at)
        monkeypatch.setattr(enterprise_billing, "utcnow", lambda: now)
        first_capture = _invoice_capture(
            session, invoice=first, company=company, user=user, amount_cents=100,
            key="first-full", occurred_at=first.issued_at + timedelta(hours=1),
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=first.id, payment_transaction_id=first_capture.id,
        )
        assert first.status == EnterpriseInvoiceStatus.PAID
        assert account.billing_hold
        assert second.paid_cents == 0
        second_capture = _invoice_capture(
            session, invoice=second, company=company, user=user, amount_cents=100,
            key="second-full", occurred_at=second.issued_at + timedelta(hours=1),
        )
        EnterpriseBillingService.apply_payment_transaction(
            session, invoice_id=second.id, payment_transaction_id=second_capture.id,
        )
        assert second.status == EnterpriseInvoiceStatus.PAID
        assert not account.billing_hold
        assert session.scalar(select(func.sum(AccountsReceivableLedgerEntry.credit_cents))) == 200


def test_overdue_invoice_blocks_new_credit_without_waiting_for_dunning_worker(app, monkeypatch):
    with app.state.session_factory.begin() as session:
        company, user, model, _, invoice, account = _invoice_context(session)
        now = _utc(invoice.due_at)
        assert not account.billing_hold
        EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=now,
            period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        monkeypatch.setattr(company_points_billing, "utcnow", lambda: now)
        task = _reserve_task(session, company, user, model)
        with pytest.raises(ConflictError, match="合同授信积分不能用于新任务"):
            CompanyPointBillingService.reserve(
                session, company_id=company.id, task_id=task.id,
                amount_points=10, idempotency_key="overdue-without-worker",
            )
        assert task.status == TaskStatus.DRAFT
        assert session.scalar(select(func.count(TaskPointLotAllocation.id)).where(
            TaskPointLotAllocation.task_id == task.id,
        )) == 0


def test_late_settlement_after_final_cycle_is_explicit_unbilled_exception(app):
    with app.state.session_factory.begin() as session:
        company, user, model, _, old_invoice, account = _invoice_context(session)
        original_total = old_invoice.total_cents
        lot = session.scalar(select(CompanyPointLot).where(
            CompanyPointLot.company_id == company.id,
            CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
        ))
        settled_at = END + timedelta(days=1)
        task, settle, value = _settle_contract_task(
            session, company=company, user=user, model=model, lot=lot,
            points=10, key="late-after-final-cycle", settled_at=settled_at,
        )
        cutoff = settled_at + timedelta(seconds=1)
        exceptions = EnterpriseBillingService.unbilled_receivable_exceptions(
            session, company_id=company.id, as_of=cutoff,
        )
        assert exceptions == [{
            "company_id": company.id, "task_id": task.id,
            "value_allocation_id": value.id, "settle_ledger_id": settle.id,
            "contract_version_id": lot.contract_version_id,
            "source_cycle_id": lot.billing_cycle_id, "covering_cycle_ids": [],
            "receivable_cents": 100, "settled_at": settled_at.isoformat(),
            "reason": "no_covering_billing_cycle",
        }]
        assert EnterpriseBillingService.unbilled_receivable_exceptions(
            session, company_id=company.id, as_of=cutoff,
        ) == exceptions
        assert EnterpriseBillingService.unbilled_receivable_exceptions(
            session, company_id=company.id, as_of=settled_at,
        ) == []
        assert old_invoice.total_cents == original_total
        assert account.unbilled_receivable_cents == 100


def test_open_cycle_can_collect_late_settlement_but_closed_invoice_is_not_rewritten(app):
    with app.state.session_factory.begin() as session:
        company, user, model, _, _, account = _invoice_context(session)
        next_end = datetime(2026, 10, 1, tzinfo=timezone.utc)
        next_cycle, _, _ = EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=END, period_end=next_end,
        )
        lot = session.scalar(select(CompanyPointLot).where(
            CompanyPointLot.company_id == company.id,
            CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
        ))
        settled_at = END + timedelta(days=1)
        task, _, value = _settle_contract_task(
            session, company=company, user=user, model=model, lot=lot,
            points=10, key="late-with-next-open-cycle", settled_at=settled_at,
        )
        assert EnterpriseBillingService.unbilled_receivable_exceptions(
            session, company_id=company.id, as_of=next_end,
        ) == []
        _, invoice, lines, _ = EnterpriseBillingService.close_and_issue_cycle(
            session, company_id=company.id, cycle_id=next_cycle.id, issued_at=next_end,
        )
        assert [line.value_allocation_id for line in lines] == [value.id]
        assert invoice.total_cents == 100
        assert account.unbilled_receivable_cents == 0
        # A legacy/backfilled arrival with a timestamp in an already issued
        # period also requires explicit handling, never amendment of that invoice.
        late_task, _, late_value = _settle_contract_task(
            session, company=company, user=user, model=model, lot=lot,
            points=10, key="late-after-issue", settled_at=settled_at + timedelta(hours=1),
        )
        exceptions = EnterpriseBillingService.unbilled_receivable_exceptions(
            session, company_id=company.id, as_of=next_end,
        )
        assert len(exceptions) == 1
        assert exceptions[0]["task_id"] == late_task.id
        assert exceptions[0]["value_allocation_id"] == late_value.id
        assert exceptions[0]["reason"] == "covering_cycle_already_closed"
        assert exceptions[0]["covering_cycle_ids"] == [next_cycle.id]
        assert invoice.total_cents == 100
        assert account.unbilled_receivable_cents == 100
