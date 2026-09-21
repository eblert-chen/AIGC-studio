from __future__ import annotations

from contextlib import contextmanager
from threading import Barrier, Lock, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from platform_api.dependencies import get_finance_snapshot_db
from platform_api.models import (
    CompanyPointWalletAccount, FinanceReconciliationRun,
    FinanceReconciliationSnapshot, PlatformAdminActivity, PointLotSourceKind, User,
    UserAccountType,
)
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.errors import ConflictError
from platform_api.services.finance_snapshot import begin_finance_snapshot
from platform_api.services.financial_reconciliation import FinancialReconciliationService

from .test_financial_reconciliation import (
    PERIOD_START, PERIOD_END, _run, _seed_balanced_company_chain,
)
from .test_payment_settlements_postgres import settlement_postgres_factory


def _request(factory):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=factory)))


def _empty_run(session, key):
    return FinancialReconciliationService.run(
        session, run_kind="daily", period_start=PERIOD_START,
        period_end=PERIOD_END, idempotency_key=key,
        provider_statement_available=False,
        provider_cost_statement_available=False,
    )


def test_fresh_finance_session_chooses_rr_and_does_not_change_other_sessions(
    settlement_postgres_factory,
):
    factory = settlement_postgres_factory
    with factory() as session:
        assert not session.in_transaction()
        begin_finance_snapshot(session)
        assert session.connection().get_isolation_level() == "REPEATABLE READ"
        result = _empty_run(session, "fresh-snapshot")
        assert len(result.snapshots) == 4
        session.commit()
    with factory() as session:
        assert session.connection().get_isolation_level() == "READ COMMITTED"


def test_started_rc_transaction_is_rejected_without_committing_caller_work(
    settlement_postgres_factory,
):
    factory = settlement_postgres_factory
    email = f"uncommitted-snapshot-{uuid4()}@example.test"
    with factory() as session:
        session.add(User(email=email, display_name="uncommitted caller"))
        session.flush()
        assert session.connection().get_isolation_level() == "READ COMMITTED"
        with pytest.raises(ConflictError, match="一致性快照"):
            _empty_run(session, "invalid-rc-snapshot")
        assert session.in_transaction()
        assert session.scalar(select(User.id).where(User.email == email))
        session.rollback()
    with factory() as session:
        assert session.scalar(select(User.id).where(User.email == email)) is None
        assert session.scalar(select(func.count()).select_from(FinanceReconciliationRun)) == 0


def test_legal_concurrent_credit_between_wallet_and_lot_reads_keeps_four_dimensions_consistent(
    settlement_postgres_factory,
):
    factory = settlement_postgres_factory
    engine = factory.kw["bind"]
    with factory.begin() as session:
        company, _ = _seed_balanced_company_chain(session)
        company_id = company.id
        old_available = session.get(CompanyPointWalletAccount, company_id).available_points
    marker = f"snapshot-reader-{uuid4()}"
    credits = []

    def credit_after_wallet_select(connection, cursor, statement, parameters, context, executemany):
        if (connection.info.get(marker) and not credits
                and "from company_point_wallet_accounts" in " ".join(statement.lower().split())):
            credits.append("executing")
            with factory.begin() as writer:
                CompanyPointBillingService.credit(
                    writer, company_id=company_id, amount_points=50,
                    source_kind=PointLotSourceKind.PROMOTIONAL,
                    cash_basis_cents=0, subsidy_cents=500,
                    idempotency_key="concurrent-promotional-credit",
                )
            credits.append("committed")

    event.listen(engine, "after_cursor_execute", credit_after_wallet_select)
    try:
        with contextmanager(get_finance_snapshot_db)(_request(factory)) as session:
            connection = session.connection()
            connection.info[marker] = True
            try:
                result = _run(session, key="concurrent-credit-snapshot")
                assert {item.dimension for item in result.snapshots} == {
                    "cash", "points", "tasks", "provider_cost",
                }
                assert {item.code for item in result.exceptions} == {
                    "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
                    "PROVIDER_COST_SOURCE_AUTHENTICITY_UNVERIFIED",
                }
                points = next(item for item in result.snapshots if item.dimension == "points")
                assert points.totals["wallet_available_points"] == old_available
                assert points.totals["lot_available_points"] == old_available
            finally:
                connection.info.pop(marker, None)
    finally:
        event.remove(engine, "after_cursor_execute", credit_after_wallet_select)
    assert credits == ["executing", "committed"]
    with factory() as session:
        assert session.get(CompanyPointWalletAccount, company_id).available_points == old_available + 50
    with contextmanager(get_finance_snapshot_db)(_request(factory)) as session:
        result = _run(session, key="after-concurrent-credit")
        points = next(item for item in result.snapshots if item.dimension == "points")
        assert points.totals["wallet_available_points"] == old_available + 50
        assert points.totals["lot_available_points"] == old_available + 50


def test_rr_same_key_race_yields_one_run_and_retryable_conflict_then_exact_replay(
    settlement_postgres_factory,
):
    factory = settlement_postgres_factory
    engine = factory.kw["bind"]
    barrier, result_lock = Barrier(2), Lock()
    marker = f"same-key-{uuid4()}"
    outcomes = []

    def synchronize_empty_lookup(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if ("from finance_reconciliation_runs" in normalized
                and "idempotency_key =" in normalized and "for update" in normalized
                and not connection.info.get(marker)):
            connection.info[marker] = True
            barrier.wait(timeout=10)

    def reconcile():
        try:
            with contextmanager(get_finance_snapshot_db)(_request(factory)) as session:
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                result = _empty_run(session, marker)
                outcome = ("created", result.run.id, result.run.snapshot_sha256)
        except ConflictError as exc:
            outcome = ("conflict", str(exc))
        except Exception as exc:
            outcome = ("error", repr(exc))
        with result_lock:
            outcomes.append(outcome)

    event.listen(engine, "after_cursor_execute", synchronize_empty_lookup)
    try:
        threads = [Thread(target=reconcile), Thread(target=reconcile)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            assert not thread.is_alive(), "snapshot idempotency race stalled"
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_empty_lookup)
    assert sorted(row[0] for row in outcomes) == ["conflict", "created"], outcomes
    winner = next(row for row in outcomes if row[0] == "created")
    with contextmanager(get_finance_snapshot_db)(_request(factory)) as session:
        replay = _empty_run(session, marker)
        assert replay.created is False
        assert (replay.run.id, replay.run.snapshot_sha256) == winner[1:]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FinanceReconciliationRun)) == 1
        assert session.scalar(select(func.count()).select_from(FinanceReconciliationSnapshot)) == 4


def test_snapshot_dependency_maps_actual_serialization_failure_and_rolls_back(
    settlement_postgres_factory,
):
    factory = settlement_postgres_factory
    with factory.begin() as session:
        user = User(email="snapshot-serialization@example.test", display_name="initial")
        session.add(user)
        session.flush()
        user_id = user.id
    with pytest.raises(ConflictError, match="并发冲突"):
        with contextmanager(get_finance_snapshot_db)(_request(factory)) as snapshot:
            user = snapshot.get(User, user_id)
            with factory.begin() as writer:
                writer.get(User, user_id).display_name = "winner"
            user.display_name = "stale writer"
            snapshot.flush()
    with factory() as session:
        assert session.get(User, user_id).display_name == "winner"


def test_snapshot_dependency_does_not_hide_unrelated_database_failure(
    settlement_postgres_factory,
):
    with pytest.raises(DBAPIError) as caught:
        with contextmanager(get_finance_snapshot_db)(_request(settlement_postgres_factory)) as session:
            session.execute(text("SELECT 1 / 0"))
    assert getattr(caught.value.orig, "sqlstate", None) == "22012"


@pytest.mark.parametrize("path", ["operating-series", "model-profitability"])
def test_admin_report_snapshot_reuses_single_pool_connection_after_authorization(
    app, client, settlement_postgres_factory, path,
):
    factory = settlement_postgres_factory
    with factory() as session:
        schema = session.scalar(select(func.current_schema()))
    assert schema.startswith("payment_settlements_")
    engine = create_engine(
        factory.kw["bind"].url, connect_args={"options": f"-csearch_path={schema}"},
        pool_size=1, max_overflow=0, pool_timeout=0.5,
    )
    report_factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    previous_factory = app.state.session_factory
    try:
        with report_factory.begin() as session:
            admin = User(
                email=f"pool-admin-{path}@example.test", display_name="Pool admin",
                is_platform_admin=True, account_type=UserAccountType.PLATFORM_ADMIN,
            )
            ordinary = User(
                email=f"pool-user-{path}@example.test", display_name="Ordinary user",
                account_type=UserAccountType.PERSONAL,
            )
            session.add_all([admin, ordinary])
            session.flush()
            admin_id, ordinary_id = admin.id, ordinary.id
        app.state.session_factory = report_factory
        url = f"/api/v1/platform-admin/analytics/{path}"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"X-Platform-Admin-User-ID": ordinary_id}).status_code == 403
        response = client.get(url, headers={"X-Platform-Admin-User-ID": admin_id})
        assert response.status_code == 200, response.text
        with report_factory() as session:
            assert session.get(PlatformAdminActivity, admin_id) is not None
            assert session.connection().get_isolation_level() == "READ COMMITTED"
    finally:
        app.state.session_factory = previous_factory
        engine.dispose()
