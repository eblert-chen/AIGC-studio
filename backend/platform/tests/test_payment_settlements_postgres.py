from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime
from pathlib import Path
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from platform_api.database import Base
from platform_api.models import (
    PaymentSettlementBatch,
    PaymentSettlementEntry,
    ProviderCostStatementBatch,
    ProviderCostStatementLine,
    ReconciliationRunStatus,
    User,
    UserAccountType,
)
from platform_api.services.errors import ConflictError
from platform_api.services.payment_settlements import (
    PaymentSettlementImportService,
    ProviderCostStatementImportService,
    validate_archived_statement,
)

from .test_finance_operations_api import _provider_cost_body, _settlement_body
from .test_financial_reconciliation import (
    _run, _seed_balanced_company_chain, _seed_balanced_invoice_adjustment_chain,
)


@pytest.fixture
def settlement_postgres_factory(request, monkeypatch):
    database_url = os.getenv("PLATFORM_TEST_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("requires explicit PLATFORM_TEST_DATABASE_URL PostgreSQL test database")
    schema_name = f"payment_settlements_{uuid4().hex}"
    administration_engine = create_engine(database_url, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema_name}"},
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        if getattr(request, "param", None) == "migrated":
            schema_url = make_url(database_url).update_query_dict(
                {"options": f"-csearch_path={schema_name}"}
            ).render_as_string(hide_password=False)
            monkeypatch.setenv("DATABASE_URL", schema_url)
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            command.upgrade(config, "head")
        else:
            Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        assert schema_name.startswith("payment_settlements_") and len(schema_name) == 52
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()


def _arguments(body):
    return {
        **{key: value for key, value in body.items() if key != "source_document_base64"},
        "source_document_bytes": base64.b64decode(body["source_document_base64"]),
        "period_start": datetime.fromisoformat(body["period_start"]),
        "period_end": datetime.fromisoformat(body["period_end"]),
    }


@pytest.mark.parametrize("source", ["payment", "supplier"])
@pytest.mark.parametrize("second_intent", ["exact", "different_metadata", "different_bytes"])
def test_concurrent_imports_serialize_exact_replay_or_conflict_without_poisoning_transaction(
    settlement_postgres_factory, source, second_intent
):
    factory = settlement_postgres_factory
    payment = source == "payment"
    service = PaymentSettlementImportService if payment else ProviderCostStatementImportService
    batch_model = PaymentSettlementBatch if payment else ProviderCostStatementBatch
    line_model = PaymentSettlementEntry if payment else ProviderCostStatementLine
    first = _arguments(_settlement_body() if payment else _provider_cost_body())
    second = dict(first)
    if second_intent == "different_metadata":
        second["source_object_version"] = "conflicting-version-2"
    elif second_intent == "different_bytes":
        second["source_document_bytes"] += b"\n"
        second["source_document_sha256"] = hashlib.sha256(second["source_document_bytes"]).hexdigest()
        second["source_size_bytes"] = len(second["source_document_bytes"])

    # Both real PostgreSQL SELECTs must observe no batch before either writer
    # proceeds. Product queries and persistence code are not mocked.
    barrier = Barrier(2)
    engine = factory.kw["bind"]
    marker = f"settlement-race-{uuid4().hex}"
    result_lock = Lock()
    results = []

    def synchronize_empty_lookup(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if (
            f"from {batch_model.__tablename__}" in normalized
            and "source_document_sha256 =" in normalized
            and "for update" in normalized
            and not connection.info.get(marker)
        ):
            connection.info[marker] = True
            barrier.wait(timeout=10)

    event.listen(engine, "after_cursor_execute", synchronize_empty_lookup)

    def execute(index, arguments):
        try:
            with factory.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
                session.add(
                    User(
                        email=f"{marker}-{index}@example.com",
                        display_name="Unrelated outer transaction marker",
                        account_type=UserAccountType.PERSONAL,
                    )
                )
                session.flush()
                try:
                    imported = service.import_document(session, **arguments)
                    outcome = ("ok", imported.batch.id, imported.created_count)
                except ConflictError:
                    assert session.scalar(select(1)) == 1
                    outcome = ("conflict", None, None)
            with result_lock:
                results.append(outcome)
        except Exception as exc:
            with result_lock:
                results.append(("error", type(exc).__name__, str(exc)))

    threads = [Thread(target=execute, args=(index, args)) for index, args in enumerate((first, second))]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            assert not thread.is_alive(), "settlement importer PostgreSQL race deadlocked"
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_empty_lookup)

    assert len(results) == 2
    if second_intent == "exact":
        assert sorted(item[0] for item in results) == ["ok", "ok"], results
        assert sorted(item[2] for item in results) == [0, 1]
        assert results[0][1] == results[1][1]
    else:
        assert sorted(item[0] for item in results) == ["conflict", "ok"], results
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(batch_model)) == 1
        assert session.scalar(select(func.count()).select_from(line_model)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 2
        batch = session.scalar(select(batch_model))
        assert batch is not None
        validate_archived_statement(batch)
        assert batch.verification_method == "uploaded_file_digest"


@pytest.mark.parametrize("settlement_postgres_factory", ["migrated"], indirect=True)
@pytest.mark.parametrize("scenario", ["prepaid", "invoice"])
def test_migrated_postgres_complete_financial_run_finalizes_and_replays_immutable_evidence(
    settlement_postgres_factory, scenario,
):
    factory = settlement_postgres_factory
    with factory.begin() as session:
        if scenario == "prepaid":
            _seed_balanced_company_chain(session, separate_fee=True)
        else:
            _seed_balanced_invoice_adjustment_chain(session)
    snapshot_factory = sessionmaker(
        bind=factory.kw["bind"].execution_options(isolation_level="REPEATABLE READ"),
        expire_on_commit=False,
    )
    with snapshot_factory.begin() as session:
        result = _run(session, key="migrated-pg-complete-financial-chain")
        assert result.run.status == ReconciliationRunStatus.BALANCED_WITH_EXCEPTIONS
        assert {item.code for item in result.exceptions} == {
            "PAYMENT_SETTLEMENT_SOURCE_AUTHENTICITY_UNVERIFIED",
            "PROVIDER_COST_SOURCE_AUTHENTICITY_UNVERIFIED" if scenario == "prepaid"
            else "PROVIDER_COST_STATEMENT_SOURCE_UNAVAILABLE",
        }
        snapshot_hash = result.run.snapshot_sha256
        assert snapshot_hash
    with snapshot_factory.begin() as session:
        replay = _run(session, key="migrated-pg-complete-financial-chain")
        assert replay.created is False
        assert replay.run.snapshot_sha256 == snapshot_hash
        cash = next(item for item in replay.snapshots if item.dimension == "cash")
        assert cash.totals["capture_amount_cents"] == (100 if scenario == "prepaid" else 1000)
        assert cash.totals["settlement_fee_amount_cents"] == (2 if scenario == "prepaid" else 0)
        assert cash.totals["payout_cents"] == cash.totals["bank_deposit_cents"] == (98 if scenario == "prepaid" else 800)
