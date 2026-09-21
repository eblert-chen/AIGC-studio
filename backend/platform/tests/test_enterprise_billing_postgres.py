from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from platform_api.database import Base
from platform_api.models import (
    CompanyBillingAccount,
    CompanyBillingCycle,
    CompanyInvoice,
    CompanyPointLot,
    EnterpriseBillingCycleStatus,
    EnterpriseDunningAction,
    EnterpriseDunningRun,
    EnterpriseDunningRunStatus,
    EnterpriseInvoiceStatus,
    PointLotSourceKind,
)
from platform_api.services.enterprise_billing import EnterpriseBillingService
from platform_api.services.errors import ConflictError

from .test_enterprise_monthly_billing import (
    _activate,
    _invoice_capture,
    _seed_point_company,
    _settle_contract_task,
)


DATABASE_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


@pytest.fixture
def enterprise_postgres_factory():
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    schema_name = f"enterprise_billing_{uuid.uuid4().hex}"
    assert schema_name.startswith("enterprise_billing_")
    administration_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    engine = create_engine(
        DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema_name}"},
        pool_size=6,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        yield factory
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()


def _run_concurrently(*operations):
    ready = Barrier(len(operations))
    result_lock = Lock()
    results: list[tuple[str, object]] = []

    def execute(operation):
        ready.wait(timeout=10)
        try:
            result = ("ok", operation())
        except Exception as error:  # concrete tests assert no error/deadlock.
            result = ("error", error)
        with result_lock:
            results.append(result)

    threads = [Thread(target=execute, args=(operation,)) for operation in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "enterprise billing PostgreSQL lock order deadlocked"
    return results


def test_concurrent_cycle_open_serializes_to_one_cycle_and_one_credit_lot(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        company_id = company.id

    def open_cycle():
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            cycle, lot, created = EnterpriseBillingService.open_cycle(
                session,
                company_id=company_id,
                period_start=start,
                period_end=end,
            )
            return cycle.id, lot.id if lot is not None else None, created

    results = _run_concurrently(open_cycle, open_cycle)
    assert [status for status, _ in results] == ["ok", "ok"]
    values = [value for _, value in results]
    assert len({value[0] for value in values}) == 1
    assert len({value[1] for value in values}) == 1
    assert sorted(value[2] for value in values) == [False, True]
    with factory() as session:
        assert session.scalar(
            select(func.count(CompanyBillingCycle.id)).where(
                CompanyBillingCycle.company_id == company_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(CompanyPointLot.id)).where(
                CompanyPointLot.company_id == company_id,
                CompanyPointLot.source_kind == PointLotSourceKind.CONTRACT,
            )
        ) == 1


def test_dunning_and_payment_share_lock_order_without_deadlock(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with factory.begin() as session:
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
            key="postgres-dunning-payment",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=end,
        )
        assert invoice.due_at is not None
        transaction = _invoice_capture(
            session,
            invoice=invoice,
            company=company,
            user=user,
            amount_cents=invoice.total_cents,
            key="postgres-dunning-payment",
            occurred_at=invoice.due_at + timedelta(hours=1),
        )
        company_id = company.id
        cycle_id = cycle.id
        invoice_id = invoice.id
        transaction_id = transaction.id
        as_of = invoice.due_at

    def dunning():
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            run, _, created = EnterpriseBillingService.run_dunning(
                session,
                company_id=company_id,
                as_of=as_of,
                idempotency_key="postgres-dunning-payment-run",
            )
            return run.id, created

    def payment():
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            paid, _, applied = EnterpriseBillingService.apply_payment_transaction(
                session,
                invoice_id=invoice_id,
                payment_transaction_id=transaction_id,
            )
            return paid.id, applied

    results = _run_concurrently(dunning, payment)
    assert [status for status, _ in results] == ["ok", "ok"]
    with factory() as session:
        invoice = session.get(CompanyInvoice, invoice_id)
        cycle = session.get(CompanyBillingCycle, cycle_id)
        account = session.get(CompanyBillingAccount, company_id)
        run = session.scalar(
            select(EnterpriseDunningRun).where(
                EnterpriseDunningRun.idempotency_key
                == "postgres-dunning-payment-run"
            )
        )
        assert invoice is not None and invoice.status == EnterpriseInvoiceStatus.PAID
        assert invoice.paid_cents == invoice.total_cents
        assert cycle is not None and cycle.status == EnterpriseBillingCycleStatus.PAID
        assert account is not None and account.billing_hold is False
        assert account.billing_hold_reason is None
        assert account.billing_hold_since is None
        assert account.dunning_level == 0
        assert run is not None and run.status == EnterpriseDunningRunStatus.COMPLETED


def test_concurrent_identical_dunning_intent_creates_one_run_and_replays(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with factory.begin() as session:
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
            key="postgres-dunning-replay",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session,
            company_id=company.id,
            cycle_id=cycle.id,
            issued_at=end,
        )
        assert invoice.due_at is not None
        company_id = company.id
        invoice_id = invoice.id
        as_of = invoice.due_at

    def dunning():
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            run, actions, created = EnterpriseBillingService.run_dunning(
                session,
                company_id=company_id,
                as_of=as_of,
                idempotency_key="postgres-identical-dunning-run",
            )
            return run.id, tuple(action.id for action in actions), created

    results = _run_concurrently(dunning, dunning)
    assert [status for status, _ in results] == ["ok", "ok"]
    values = [value for _, value in results]
    assert len({value[0] for value in values}) == 1
    assert len({value[1] for value in values}) == 1
    assert sorted(value[2] for value in values) == [False, True]
    with factory() as session:
        assert session.scalar(
            select(func.count(EnterpriseDunningRun.id)).where(
                EnterpriseDunningRun.company_id == company_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(EnterpriseDunningAction.id)).where(
                EnterpriseDunningAction.invoice_id == invoice_id
            )
        ) == 2


def test_concurrent_cross_company_dunning_key_collision_is_typed_conflict(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    effective_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with factory.begin() as session:
        first_company, first_user, _ = _seed_point_company(session)
        second_company, second_user, _ = _seed_point_company(session)
        _activate(
            session,
            company=first_company,
            user=first_user,
            effective_at=effective_at,
        )
        _activate(
            session,
            company=second_company,
            user=second_user,
            effective_at=effective_at,
        )
        company_ids = (first_company.id, second_company.id)

    def dunning(company_id: str):
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            run, _, created = EnterpriseBillingService.run_dunning(
                session,
                company_id=company_id,
                as_of=effective_at,
                idempotency_key="postgres-cross-company-collision",
            )
            return run.id, created

    results = _run_concurrently(
        lambda: dunning(company_ids[0]),
        lambda: dunning(company_ids[1]),
    )
    assert [status for status, _ in results].count("ok") == 1
    errors = [value for status, value in results if status == "error"]
    assert len(errors) == 1 and isinstance(errors[0], ConflictError)
    assert "另一意图" in str(errors[0])
    with factory() as session:
        assert session.scalar(
            select(func.count(EnterpriseDunningRun.id)).where(
                EnterpriseDunningRun.idempotency_key
                == "postgres-cross-company-collision"
            )
        ) == 1


def test_concurrent_scheduler_workers_do_not_duplicate_company_cycle_or_daily_run(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    effective_at = datetime(2026, 7, 31, 16, tzinfo=timezone.utc)
    as_of = datetime(2026, 8, 15, tzinfo=timezone.utc)
    with factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=effective_at)
        company_id = company.id

    def run_once():
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            return EnterpriseBillingService.run_once(session, as_of=as_of)

    results = _run_concurrently(run_once, run_once)
    assert [status for status, _ in results] == ["ok", "ok"], repr(results)
    assert sorted(value["processed"] for _, value in results) == [False, True]
    with factory() as session:
        assert session.scalar(
            select(func.count(CompanyBillingCycle.id)).where(
                CompanyBillingCycle.company_id == company_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(EnterpriseDunningRun.id)).where(
                EnterpriseDunningRun.company_id == company_id
            )
        ) == 1


def test_concurrent_scheduler_workers_skip_busy_company_and_process_next(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    effective_at = datetime(2026, 7, 31, 16, tzinfo=timezone.utc)
    as_of = datetime(2026, 8, 15, tzinfo=timezone.utc)
    with factory.begin() as session:
        company_ids = []
        for _ in range(2):
            company, user, _ = _seed_point_company(session)
            _activate(session, company=company, user=user, effective_at=effective_at)
            company_ids.append(company.id)

    def run_once():
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            return EnterpriseBillingService.run_once(session, as_of=as_of)

    results = _run_concurrently(run_once, run_once)
    assert [status for status, _ in results] == ["ok", "ok"], repr(results)
    assert all(value["processed"] for _, value in results)
    assert {value["company_id"] for _, value in results} == set(company_ids)
    with factory() as session:
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 2
        assert session.scalar(select(func.count(EnterpriseDunningRun.id))) == 2


def test_payment_reloads_invoice_after_an_interleaved_dunning_commit(
    enterprise_postgres_factory,
) -> None:
    factory = enterprise_postgres_factory
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=start, period_end=end
        )
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="postgres-stale-invoice-task",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session, company_id=company.id, cycle_id=cycle.id, issued_at=end
        )
        transaction = _invoice_capture(
            session,
            invoice=invoice,
            company=company,
            user=user,
            amount_cents=invoice.total_cents,
            key="postgres-stale-invoice-payment",
            occurred_at=invoice.due_at + timedelta(hours=1),
        )
        company_id, invoice_id, transaction_id = company.id, invoice.id, transaction.id
        due_at = invoice.due_at

    with factory() as payment_session:
        payment_session.execute(text("SET LOCAL lock_timeout = '5s'"))
        # Keep this object strongly referenced: SELECT FOR UPDATE does not
        # refresh an already loaded SQLAlchemy entity unless explicitly told.
        probed_invoice = payment_session.get(CompanyInvoice, invoice_id)
        assert probed_invoice.status == EnterpriseInvoiceStatus.ISSUED
        with factory.begin() as dunning_session:
            dunning_session.execute(text("SET LOCAL lock_timeout = '5s'"))
            EnterpriseBillingService.run_dunning(
                dunning_session,
                company_id=company_id,
                as_of=due_at,
                idempotency_key="postgres-stale-invoice-dunning",
            )
        paid, _, applied = EnterpriseBillingService.apply_payment_transaction(
            payment_session,
            invoice_id=invoice_id,
            payment_transaction_id=transaction_id,
        )
        assert applied is True and paid.status == EnterpriseInvoiceStatus.PAID
        assert paid.paid_cents == paid.total_cents
        payment_session.commit()
    with factory() as session:
        assert session.get(CompanyBillingAccount, company_id).billing_hold is False
