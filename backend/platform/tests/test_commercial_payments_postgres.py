from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, func, select, text
from sqlalchemy.orm import sessionmaker

from platform_api.database import Base
from platform_api.models import (
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    GenerationTask,
    PaymentDispute,
    PaymentDisputeDebtRecoveryAllocation,
    PaymentDisputeDebtRecoveryReversal,
    PaymentDisputeStatus,
    PaymentOrder,
    PaymentOrderStatus,
    PaymentRefund,
    PaymentTransaction,
    PaymentTransactionKind,
    PaymentWebhookOutcome,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    TaskStatus,
)
from platform_api.payment_providers import DeterministicFakePaymentProvider
from platform_api.services.commercial_payments import CommercialPaymentService
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.errors import ConflictError, InsufficientBalanceError
from platform_api.services.payment_webhooks import (
    DisputeOpenedEvent,
    DisputeWonEvent,
    PaymentCapturedEvent,
    PaymentWebhookEvidence,
)

from .test_enterprise_monthly_billing import _seed_point_company, _task


DATABASE_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")
PROVIDER = "deterministic-test"
MERCHANT = "merchant-cny-main"
KEY_ID = "payment-key-2026-08"
OCCURRED_AT = datetime(2033, 5, 18, tzinfo=timezone.utc)


@pytest.fixture
def commercial_postgres_factory():
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database; SQLite cannot prove lock order")

    schema_name = f"commercial_payments_{uuid4().hex}"
    assert schema_name.startswith("commercial_payments_")
    administration_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    engine = create_engine(
        DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema_name}"},
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()


def _dispatched_order(factory, *, company_id: str, user_id: str, points: int):
    with factory.begin() as session:
        order, created = CommercialPaymentService.create_point_order(
            session,
            company_id=company_id,
            workspace_id=None,
            user_id=user_id,
            points=points,
            provider=PROVIDER,
            merchant_account=MERCHANT,
            idempotency_key=f"postgres-payment-{uuid4().hex}",
        )
        assert created
        command = CommercialPaymentService.claim_next_provider_command(session)
        assert command is not None and command.order_id == order.id
        command_id, lease_token, order_id = command.id, command.lease_token, order.id
    # Exercise the same commit-before-PSP path as the dedicated payment worker.
    with factory() as session:
        asyncio.run(
            CommercialPaymentService.execute_claimed_provider_command(
                session,
                command_id=command_id,
                lease_token=lease_token,
                provider=DeterministicFakePaymentProvider(enabled_for_tests=True),
            )
        )
        session.commit()
        order = session.get(PaymentOrder, order_id)
        assert order is not None and order.provider_order_id
        session.expunge(order)
        return order


def _payment_event(event_type, order, *, occurred_at=OCCURRED_AT, dispute_id=None):
    payload = {
        "api_version": "v1",
        "schema_version": 1,
        "event_id": str(uuid4()),
        "provider": PROVIDER,
        "key_id": KEY_ID,
        "occurred_at": occurred_at,
        "type": event_type,
        "data": {
            "order_id": order.id,
            "provider_payment_id": order.provider_order_id,
            "amount_cents": order.amount_cents,
            "currency": "CNY",
        },
    }
    if dispute_id is not None:
        payload["data"]["provider_dispute_id"] = dispute_id
    return {
        "payment.captured": PaymentCapturedEvent,
        "dispute.opened": DisputeOpenedEvent,
        "dispute.won": DisputeWonEvent,
    }[event_type].model_validate(payload)


def _process(session, event):
    evidence = PaymentWebhookEvidence(
        provider=PROVIDER,
        merchant_account=MERCHANT,
        key_id=KEY_ID,
        event_id=str(event.event_id),
        delivery_timestamp=event.occurred_at,
        payload_sha256=hashlib.sha256(
            json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    )
    receipt = CommercialPaymentService.process_webhook(
        session, event=event, evidence=evidence
    )
    assert receipt.outcome == PaymentWebhookOutcome.PROCESSED
    return receipt.id


def _interleaved_wallet_race(factory, *, holder, operations):
    """Force contention after a real wallet lock, not a timing-based happy path.

    The holder waits until its contender has reached the wallet SELECT FOR UPDATE.
    A regressed lot->wallet / dispute->wallet implementation then retains the
    earlier row lock, so the holder's next lock reliably exposes the deadlock.
    No product method, SQL statement or transaction isolation is mocked.
    """
    engine = factory.kw["bind"]
    ready = Barrier(2)
    wallet_held = Event()
    contender_at_wallet = Event()
    results = {}
    results_lock = Lock()

    def is_wallet_lock(statement):
        normalized = " ".join(statement.lower().split())
        return (
            "from company_point_wallet_accounts" in normalized
            and "for update" in normalized
        )

    def before_cursor_execute(connection, _cursor, statement, _parameters, _context, _many):
        role = connection.info.get("payment_race_role")
        if role in operations and role != holder and is_wallet_lock(statement):
            contender_at_wallet.set()

    def after_cursor_execute(connection, _cursor, statement, _parameters, _context, _many):
        if (
            connection.info.get("payment_race_role") == holder
            and is_wallet_lock(statement)
            and not wallet_held.is_set()
        ):
            wallet_held.set()
            assert contender_at_wallet.wait(8), "contender did not attempt the real wallet lock"

    def execute(role, operation):
        try:
            ready.wait(timeout=10)
            if role != holder:
                assert wallet_held.wait(8), "holder did not acquire the real wallet lock"
            with factory.begin() as session:
                connection = session.connection()
                connection.info["payment_race_role"] = role
                try:
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    session.execute(text("SET LOCAL statement_timeout = '12s'"))
                    result = ("ok", operation(session))
                finally:
                    connection.info.pop("payment_race_role", None)
        except Exception as error:  # callers require success or one exact domain error.
            result = ("error", error)
        with results_lock:
            results[role] = result

    sqlalchemy_event.listen(engine, "before_cursor_execute", before_cursor_execute)
    sqlalchemy_event.listen(engine, "after_cursor_execute", after_cursor_execute)
    threads = [
        Thread(target=execute, args=(role, operation), daemon=True)
        for role, operation in operations.items()
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert not any(thread.is_alive() for thread in threads), "payment PG lock-order gate hung"
        assert wallet_held.is_set() and contender_at_wallet.is_set()
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", before_cursor_execute)
        sqlalchemy_event.remove(engine, "after_cursor_execute", after_cursor_execute)
    return results


def _assert_company_point_conservation(session, company_id):
    wallet = session.get(CompanyPointWalletAccount, company_id)
    assert wallet is not None
    lots = list(
        session.scalars(select(CompanyPointLot).where(CompanyPointLot.company_id == company_id))
    )
    ledger = list(
        session.scalars(
            select(CompanyPointLedgerEntry).where(CompanyPointLedgerEntry.company_id == company_id)
        )
    )
    for dimension in ("available", "reserved", "reversal_reserved", "debt"):
        balance = getattr(wallet, f"{dimension}_points")
        assert balance >= 0
        assert balance == sum(getattr(entry, f"{dimension}_delta_points") for entry in ledger)
        if dimension != "debt":
            assert balance == sum(getattr(lot, f"{dimension}_points") for lot in lots)
    for lot in lots:
        dimensions = ("available", "reserved", "reversal_reserved", "settled", "reversed")
        assert all(getattr(lot, f"{name}_points") >= 0 for name in dimensions)
        assert lot.original_points == sum(getattr(lot, f"{name}_points") for name in dimensions)
    return wallet


@pytest.mark.parametrize("iteration", range(3))
@pytest.mark.parametrize("holder", ["reserve", "refund"])
def test_refund_and_task_reservation_serialize_without_deadlock_or_double_spend(
    commercial_postgres_factory, holder, iteration
) -> None:
    factory = commercial_postgres_factory
    with factory.begin() as session:
        company, user, model = _seed_point_company(session)
        task = _task(
            company=company,
            user=user,
            model=model,
            points=10,
            key=f"postgres-refund-reserve-{iteration}",
            status=TaskStatus.DRAFT,
        )
        session.add(task)
        session.flush()
        company_id, user_id, task_id = company.id, user.id, task.id
    order = _dispatched_order(factory, company_id=company_id, user_id=user_id, points=10)
    order_id = order.id
    with factory.begin() as session:
        _process(session, _payment_event("payment.captured", order))

    def reserve(session):
        _, ledger = CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=task_id,
            amount_points=10,
            idempotency_key=f"postgres-reserve-{iteration}",
        )
        return ledger.id

    def refund(session):
        row, created = CommercialPaymentService.request_refund(
            session,
            order_id=order_id,
            amount_cents=100,
            reason="PostgreSQL concurrency proof",
            user_id=user_id,
            idempotency_key=f"postgres-refund-{iteration}",
        )
        return row.id, created

    results = _interleaved_wallet_race(
        factory, holder=holder, operations={"reserve": reserve, "refund": refund}
    )
    loser = "refund" if holder == "reserve" else "reserve"
    assert results[holder][0] == "ok", repr(results)
    assert results[loser][0] == "error", repr(results)
    expected_error = ConflictError if loser == "refund" else InsufficientBalanceError
    assert isinstance(results[loser][1], expected_error), repr(results)
    with factory.begin() as session:
        wallet = _assert_company_point_conservation(session, company_id)
        assert wallet.available_points == wallet.debt_points == 0
        assert (wallet.reserved_points, wallet.reversal_reserved_points) == (
            (10, 0) if holder == "reserve" else (0, 10)
        )
        task = session.get(GenerationTask, task_id)
        assert task is not None
        assert task.status == (TaskStatus.QUEUED if holder == "reserve" else TaskStatus.DRAFT)
        assert task.reserved_points == (10 if holder == "reserve" else 0)
        assert session.scalar(select(func.count(PaymentRefund.id))) == (holder == "refund")
        assert session.scalar(select(func.count(PaymentTransaction.id))) == 1
        # A retry of the winning command must not debit the wallet a second time.
        if holder == "reserve":
            assert reserve(session) == results[holder][1]
        else:
            assert refund(session) == (results[holder][1][0], False)
        _assert_company_point_conservation(session, company_id)


@pytest.mark.parametrize("iteration", range(3))
@pytest.mark.parametrize("holder", ["capture", "won"])
def test_new_capture_debt_recovery_and_old_dispute_won_share_wallet_lock_order(
    commercial_postgres_factory, holder, iteration
) -> None:
    factory = commercial_postgres_factory
    with factory.begin() as session:
        company, user, model = _seed_point_company(session)
        task = _task(
            company=company,
            user=user,
            model=model,
            points=8,
            key=f"postgres-dispute-task-{iteration}",
            status=TaskStatus.DRAFT,
        )
        session.add(task)
        session.flush()
        company_id, user_id, task_id = company.id, user.id, task.id
    disputed_order = _dispatched_order(
        factory, company_id=company_id, user_id=user_id, points=10
    )
    disputed_order_id = disputed_order.id
    provider_dispute_id = f"dp_pg_{uuid4().hex}"
    with factory.begin() as session:
        _process(session, _payment_event("payment.captured", disputed_order))
        CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=task_id,
            amount_points=8,
            idempotency_key=f"postgres-dispute-reserve-{iteration}",
        )
        CompanyPointBillingService.settle_success(
            session,
            company_id=company_id,
            task_id=task_id,
            actual_cost_points=8,
            idempotency_key=f"postgres-dispute-settle-{iteration}",
        )
        _process(
            session,
            _payment_event(
                "dispute.opened",
                disputed_order,
                occurred_at=OCCURRED_AT + timedelta(days=1),
                dispute_id=provider_dispute_id,
            ),
        )
        wallet = _assert_company_point_conservation(session, company_id)
        assert (wallet.available_points, wallet.debt_points) == (0, 8)
    recovery_order = _dispatched_order(
        factory, company_id=company_id, user_id=user_id, points=5
    )
    capture_event = _payment_event(
        "payment.captured", recovery_order, occurred_at=OCCURRED_AT + timedelta(days=2)
    )
    won_event = _payment_event(
        "dispute.won",
        disputed_order,
        occurred_at=OCCURRED_AT + timedelta(days=3),
        dispute_id=provider_dispute_id,
    )

    results = _interleaved_wallet_race(
        factory,
        holder=holder,
        operations={
            "capture": lambda session: _process(session, capture_event),
            "won": lambda session: _process(session, won_event),
        },
    )
    assert all(status == "ok" for status, _ in results.values()), repr(results)
    with factory.begin() as session:
        wallet = _assert_company_point_conservation(session, company_id)
        assert (wallet.available_points, wallet.debt_points, wallet.reserved_points) == (7, 0, 0)
        dispute = session.scalar(
            select(PaymentDispute).where(PaymentDispute.order_id == disputed_order_id)
        )
        assert dispute is not None and dispute.status == PaymentDisputeStatus.WON
        assert session.get(PaymentOrder, disputed_order_id).status == PaymentOrderStatus.PAID
        allocations = list(session.scalars(select(PaymentDisputeDebtRecoveryAllocation)))
        reversals = list(session.scalars(select(PaymentDisputeDebtRecoveryReversal)))
        assert len(allocations) == len(reversals) == (1 if holder == "capture" else 0)
        if allocations:
            assert allocations[0].dispute_id == dispute.id
            assert allocations[0].recovered_points == reversals[0].restored_points == 5
            assert reversals[0].allocation_id == allocations[0].id
        assert session.scalar(
            select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.kind == PaymentTransactionKind.CAPTURE
            )
        ) == 2
        assert session.scalar(select(func.count(PaymentTransaction.id))) == 4
        assert session.scalar(
            select(func.sum(PointLotSettlementValueAllocation.cash_basis_cents))
        ) == 80
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE
            )
        ) == 1
        assert _process(session, capture_event) == results["capture"][1]
        assert _process(session, won_event) == results["won"][1]
        wallet = _assert_company_point_conservation(session, company_id)
        assert (wallet.available_points, wallet.debt_points) == (7, 0)
