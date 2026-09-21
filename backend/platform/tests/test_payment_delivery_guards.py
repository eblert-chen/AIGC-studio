from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from platform_api.models import (
    FinanceReconciliationRun,
    PaymentOrder,
    PaymentProviderCommand,
    PaymentProviderCommandOperation,
    PaymentProviderCommandStatus,
    PaymentPurpose,
    PaymentWebhookInboxEvent,
    PaymentWebhookInboxStatus,
    PersonalWorkspace,
    ReconciliationRunStatus,
    User,
    UserAccountType,
)


@pytest.fixture(scope="module", params=("sqlite", "postgresql"))
def guarded_engine(request, tmp_path_factory):
    administration_engine = None
    schema_name = None
    if request.param == "postgresql":
        database_url = os.getenv("PLATFORM_TEST_DATABASE_URL", "")
        if not database_url.startswith("postgresql"):
            pytest.skip("requires an explicit isolated PostgreSQL test database")
        administration_engine = create_engine(database_url)
        schema_name = f"payment_guards_{uuid4().hex}"
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
        database_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        ).render_as_string(hide_password=False)
    else:
        database_path = tmp_path_factory.mktemp("payment-delivery") / "guards.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", database_url)
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            command.upgrade(config, "head")
            yield engine
    finally:
        engine.dispose()
        if administration_engine is not None:
            with administration_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
            administration_engine.dispose()


def _seed_delivery(engine, kind):
    suffix = uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        if kind == "command":
            user = User(
                email=f"guard-{suffix}@example.test", display_name="Guard test",
                account_type=UserAccountType.PERSONAL,
            )
            session.add(user)
            session.flush()
            workspace = PersonalWorkspace(user_id=user.id)
            session.add(workspace)
            session.flush()
            order = PaymentOrder(
                personal_workspace_id=workspace.id, created_by_user_id=user.id,
                purpose=PaymentPurpose.POINT_PURCHASE, provider="guard-test",
                merchant_account="guard-merchant", amount_cents=1000, points=100,
                idempotency_key=suffix, request_fingerprint="a" * 64, automatic=False,
            )
            session.add(order)
            session.flush()
            record = PaymentProviderCommand(
                operation=PaymentProviderCommandOperation.CREATE_PAYMENT,
                order_id=order.id, provider=order.provider,
                merchant_account=order.merchant_account,
                idempotency_key=suffix, dedupe_key=suffix,
                request_payload={"amount_cents": 1000}, request_sha256="b" * 64,
            )
        else:
            record = PaymentWebhookInboxEvent(
                provider="guard-test", merchant_account="guard-merchant",
                provider_event_id=suffix, event_type="payment.captured",
                payload_sha256="c" * 64, payload_json={"amount_cents": 1000},
                signature_key_id="guard-key", signature_timestamp=now,
                signature_verified_at=now, provider_occurred_at=now,
            )
        session.add(record)
        session.flush()
        identity = record.id
        session.commit()
    return identity


@pytest.mark.parametrize("kind", ("command", "inbox"))
def test_delivery_identity_is_immutable_in_orm_but_status_can_advance(guarded_engine, kind):
    identity = _seed_delivery(guarded_engine, kind)
    model = PaymentProviderCommand if kind == "command" else PaymentWebhookInboxEvent
    with Session(guarded_engine) as session:
        record = session.get(model, identity)
        record.status = (
            PaymentProviderCommandStatus.FAILED if kind == "command"
            else PaymentWebhookInboxStatus.BLOCKED
        )
        record.attempt_count += 1
        session.commit()
        record.merchant_account = "different-merchant"
        with pytest.raises(RuntimeError, match="payment delivery identity is immutable"):
            session.flush()
        session.rollback()
        record = session.get(model, identity)
        session.delete(record)
        with pytest.raises(RuntimeError, match="payment delivery records are durable"):
            session.flush()
        session.rollback()


@pytest.mark.parametrize("kind", ("command", "inbox"))
def test_sql_cannot_rewrite_verified_payload_or_delete_delivery(guarded_engine, kind):
    identity = _seed_delivery(guarded_engine, kind)
    table = "payment_provider_commands" if kind == "command" else "payment_webhook_inbox_events"
    payload_column = "request_payload" if kind == "command" else "payload_json"
    hash_column = "request_sha256" if kind == "command" else "payload_sha256"
    for column, value in (
        (payload_column, '{"amount_cents":2000}'),
        (hash_column, "f" * 64),
        ("merchant_account", "different-merchant"),
    ):
        with pytest.raises(DBAPIError, match="payment delivery identity is immutable"):
            with guarded_engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE {table} SET {column}=:value WHERE id=:id"),
                    {"value": value, "id": identity},
                )
    with pytest.raises(DBAPIError, match="immutable|durable"):
        with guarded_engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": identity})


def test_finance_json_identity_finalizes_once_and_cannot_be_rewritten(guarded_engine):
    now = datetime.now(timezone.utc)
    with Session(guarded_engine) as session:
        run = FinanceReconciliationRun(
            run_kind="full_chain", period_start=now - timedelta(days=1), period_end=now,
            status=ReconciliationRunStatus.RUNNING, source_watermarks={"bank": "v1"},
            control_totals={}, idempotency_key=uuid4().hex, started_at=now,
        )
        session.add(run)
        session.flush()
        identity = run.id
        session.commit()
    with guarded_engine.begin() as connection:
        connection.execute(
            text("UPDATE finance_reconciliation_runs SET status='BALANCED_WITH_EXCEPTIONS', "
                 "snapshot_sha256=:sha, completed_at=:at WHERE id=:id"),
            {"sha": "d" * 64, "at": now, "id": identity},
        )
    with pytest.raises(DBAPIError, match="finance reconciliation conclusion is immutable"):
        with guarded_engine.begin() as connection:
            connection.execute(
                text("UPDATE finance_reconciliation_runs SET source_watermarks=:payload "
                     "WHERE id=:id"),
                {"payload": '{"bank":"v2"}', "id": identity},
            )


@pytest.mark.parametrize("table", (
    "payment_provider_commands", "payment_webhook_inbox_events", "payment_transactions",
    "accounts_receivable_ledger_entries", "payment_settlement_batches",
    "provider_cost_statement_batches", "finance_reconciliation_runs",
))
def test_postgres_cannot_truncate_payment_evidence(guarded_engine, table):
    if guarded_engine.dialect.name != "postgresql":
        pytest.skip("SQLite has no TRUNCATE operation")
    with pytest.raises(DBAPIError, match="immutable"):
        with guarded_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
