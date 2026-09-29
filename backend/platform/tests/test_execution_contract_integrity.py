"""Isolated migration/ORM evidence, not paid-provider or PG16 qualification."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from platform_api import database_privileges as facade
from platform_api import database_privileges_behavior_v29 as behavior
from platform_api import database_privileges_v21 as prior_policy
from platform_api import database_privileges_v30 as policy
from platform_api.models import (
    GenerationTask, PointLotSourceKind, RelaySubmissionOutbox, TaskStatus,
)
from platform_api.services.billing import WalletService
from platform_api.services.company_points_billing import CompanyPointBillingService
from .test_enterprise_monthly_billing import _seed_point_company, _task
from .test_execution_contract import wire


HEAD = "0066_relay_outbox_recovery"
PREVIOUS = "0056_billing_integrity_guards"


def _migrate(url, revision):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        command.upgrade(config, revision)


def _seed(engine, *, pinned, empty_quote=False, explicit_null_contract=False):
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        company, user, model = _seed_point_company(session)
        task = _task(company=company, user=user, model=model, points=10,
                     key=uuid4().hex, status=TaskStatus.DRAFT)
        payload = {"metadata": {"platform_task_id": task.id}}
        if pinned:
            task.pricing_snapshot = {**task.pricing_snapshot,
                "execution_contract_sha256": "sha256:" + "a" * 64,
                "commercial_price_authority": {"plan_id": str(uuid4()), "plan_revision": 1,
                                                "plan_content_sha256": "b" * 64}}
            payload["execution_contract"] = wire()
        if empty_quote:
            task.pricing_snapshot = {}
        if explicit_null_contract:
            payload["execution_contract"] = None
        session.add(task)
        session.flush()
        outbox = RelaySubmissionOutbox(company_id=company.id, task_id=task.id,
                                      idempotency_key=f"platform-task-{task.id}", relay_payload=payload)
        session.add(outbox)
        session.flush()
        return task.id, outbox.id, deepcopy(task.pricing_snapshot), deepcopy(payload)


@pytest.fixture(scope="module", params=["sqlite", "postgresql"])
def guarded_database(request, tmp_path_factory):
    schema = None
    admin = None
    if request.param == "sqlite":
        url = "sqlite+pysqlite:///" + (tmp_path_factory.mktemp("execution-integrity") / "test.db").as_posix()
    else:
        url = os.getenv("PLATFORM_TEST_DATABASE_URL", "")
        if not url.startswith("postgresql"):
            pytest.skip("requires explicit isolated PLATFORM_TEST_DATABASE_URL")
        # Only this generated schema is ours; permission failures are test
        # failures, not qualification skips. Never upgrade the business schema.
        schema = "execution_integrity_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        # Bring schema to the latest revision BEFORE any ORM-level seeding.
        # models.py may declare columns added by newer migrations (e.g. 0066
        # recovery_attempt_count on relay_submission_outbox); seeding against
        # a pre-HEAD schema would fail even when _migrate later upgrades past
        # that revision — ORM + DB version must be aligned for every insert.
        _migrate(url, "head")
        engine.dispose()
        engine = create_engine(url)
        # legacy / pinned seeded on the fully-migrated schema — this fixture
        # does not exercise cross-revision data fidelity at the head boundary;
        # the behavioural assertions below only need the current column set.
        legacy = _seed(engine, pinned=False)
        pinned = _seed(engine, pinned=True)
        with engine.connect() as connection:
            _version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert _version == "0066_relay_outbox_recovery", _version
        yield engine, legacy, pinned
    finally:
        engine.dispose()
        if admin is not None:
            assert schema.startswith("execution_integrity_") and len(schema) == 52
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            admin.dispose()


def _stored(engine, model, row_id, column):
    with engine.connect() as connection:
        return connection.scalar(select(model.__table__.c[column]).where(model.id == row_id))


@pytest.mark.parametrize("pinned", [False, True])
def test_direct_sql_cannot_add_change_or_remove_quote_authority(guarded_database, pinned):
    engine, legacy, commercial = guarded_database
    task_id, _, original, _ = commercial if pinned else legacy
    mutations = [{}, None, dict(original, quote_points=1),
                 dict(original, execution_contract_sha256="sha256:" + "f" * 64),
                 dict(original, commercial_price_authority={"plan_id": "replacement"})]
    if pinned:
        mutations.append({k: v for k, v in original.items() if k != "execution_contract_sha256"})
        mutations.append({k: v for k, v in original.items() if k != "commercial_price_authority"})
    for value in mutations:
        with pytest.raises(DBAPIError, match="task pricing snapshot is immutable"):
            with engine.begin() as connection:
                connection.execute(update(GenerationTask.__table__).where(GenerationTask.id == task_id)
                                   .values(pricing_snapshot=value))
        assert _stored(engine, GenerationTask, task_id, "pricing_snapshot") == original


@pytest.mark.parametrize("pinned", [False, True])
def test_direct_sql_cannot_add_change_or_remove_private_contract(guarded_database, pinned):
    engine, legacy, commercial = guarded_database
    _, outbox_id, _, original = commercial if pinned else legacy
    mutations = [dict(original, execution_contract=None), dict(original, execution_contract={})]
    if pinned:
        mutations.append({"metadata": original["metadata"]})
        changed = deepcopy(original)
        changed["execution_contract"]["routes"][0]["cost_sha256"] = "sha256:" + "e" * 64
        mutations.append(changed)
    else:
        mutations.append(dict(original, execution_contract=wire()))
    for value in mutations:
        with pytest.raises(DBAPIError, match="outbox execution contract is immutable"):
            with engine.begin() as connection:
                connection.execute(update(RelaySubmissionOutbox.__table__).where(RelaySubmissionOutbox.id == outbox_id)
                                   .values(relay_payload=value))
        assert _stored(engine, RelaySubmissionOutbox, outbox_id, "relay_payload") == original


def test_json_key_order_and_metadata_updates_are_not_contract_mutations(guarded_database):
    engine, legacy, commercial = guarded_database
    for task_id, outbox_id, quote, payload in (legacy, commercial):
        reordered = dict(reversed(list(quote.items())))
        if "commercial_price_authority" in reordered:
            reordered["commercial_price_authority"] = dict(reversed(list(reordered["commercial_price_authority"].items())))
        with engine.begin() as connection:
            connection.execute(update(GenerationTask.__table__).where(GenerationTask.id == task_id)
                               .values(pricing_snapshot=reordered, failure_reason="observed"))
            changed = deepcopy(payload)
            changed["metadata"]["_platform_materialized_request_sha256"] = "d" * 64
            if "execution_contract" in changed:
                changed["execution_contract"] = dict(reversed(list(changed["execution_contract"].items())))
            connection.execute(update(RelaySubmissionOutbox.__table__).where(RelaySubmissionOutbox.id == outbox_id)
                               .values(relay_payload=changed, materialized_relay_payload=payload, attempt_count=1))
        assert _stored(engine, GenerationTask, task_id, "pricing_snapshot") == quote
        assert _stored(engine, RelaySubmissionOutbox, outbox_id, "relay_payload")["metadata"] == changed["metadata"]


def test_empty_quote_and_explicit_null_contract_cannot_gain_or_lose_authority(guarded_database):
    engine, _, _ = guarded_database
    task_id, outbox_id, _, payload = _seed(engine, pinned=False, empty_quote=True,
                                         explicit_null_contract=True)
    with pytest.raises(DBAPIError, match="task pricing snapshot is immutable"):
        with engine.begin() as connection:
            connection.execute(update(GenerationTask.__table__).where(GenerationTask.id == task_id)
                               .values(pricing_snapshot={"execution_contract_sha256": "sha256:" + "e" * 64}))
    # json null is an existing fragment, not the absence of a fragment.
    for value in ({"metadata": payload["metadata"]}, dict(payload, execution_contract=wire())):
        with pytest.raises(DBAPIError, match="outbox execution contract is immutable"):
            with engine.begin() as connection:
                connection.execute(update(RelaySubmissionOutbox.__table__).where(RelaySubmissionOutbox.id == outbox_id)
                                   .values(relay_payload=value))
    assert _stored(engine, GenerationTask, task_id, "pricing_snapshot") == {}
    assert _stored(engine, RelaySubmissionOutbox, outbox_id, "relay_payload") == payload


@pytest.mark.parametrize("pinned", [False, True])
def test_delete_reinsert_cannot_replace_quote_or_contract(guarded_database, pinned):
    engine, _, _ = guarded_database
    task_id, outbox_id, quote, payload = _seed(engine, pinned=pinned)
    factory = sessionmaker(engine)
    for model, row_id, column, original in (
        (GenerationTask, task_id, "pricing_snapshot", quote),
        (RelaySubmissionOutbox, outbox_id, "relay_payload", payload),
    ):
        with pytest.raises(DBAPIError, match="execution contract facts are durable"):
            with engine.begin() as connection:
                connection.execute(delete(model.__table__).where(model.id == row_id))
                pytest.fail("deletion must not reach a replacement INSERT")
        with pytest.raises(RuntimeError, match="execution contract facts are durable"):
            with factory.begin() as session:
                session.delete(session.get(model, row_id))
        assert _stored(engine, model, row_id, column) == original


def test_postgres_truncate_cannot_erase_contract_evidence(guarded_database):
    engine, legacy, commercial = guarded_database
    if engine.dialect.name != "postgresql":
        return  # SQLite has no TRUNCATE statement; DELETE is tested above.
    # These tables are in this fixture's generated, disposable schema only.
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_schema()" )).startswith("execution_integrity_")
    for table in ("generation_tasks", "relay_submission_outbox"):
        with pytest.raises(DBAPIError, match="execution contract facts are durable"):
            with engine.begin() as connection:
                connection.exec_driver_sql(f"TRUNCATE TABLE {table} CASCADE")
    assert _stored(engine, GenerationTask, commercial[0], "pricing_snapshot") == commercial[2]
    assert _stored(engine, RelaySubmissionOutbox, legacy[1], "relay_payload") is not None


@pytest.mark.parametrize("pinned", [False, True])
def test_orm_guards_and_normal_reserve_settle_release_work(guarded_database, pinned):
    engine, _, _ = guarded_database
    task_id, outbox_id, original, payload = _seed(engine, pinned=pinned)
    factory = sessionmaker(engine, expire_on_commit=False)
    for target in ("quote", "outbox"):
        with pytest.raises(RuntimeError, match="is immutable"):
            with factory.begin() as session:
                if target == "quote":
                    task = session.get(GenerationTask, task_id)
                    task.pricing_snapshot = dict(task.pricing_snapshot, execution_contract_sha256="sha256:" + "f" * 64)
                else:
                    outbox = session.get(RelaySubmissionOutbox, outbox_id)
                    outbox.relay_payload = dict(outbox.relay_payload, execution_contract={"replacement": True})
    assert _stored(engine, GenerationTask, task_id, "pricing_snapshot") == original
    assert _stored(engine, RelaySubmissionOutbox, outbox_id, "relay_payload") == payload
    with factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        company_id = task.company_id
        CompanyPointBillingService.credit(session, company_id=company_id, amount_points=20,
            source_kind=PointLotSourceKind.PROMOTIONAL, cash_basis_cents=0, subsidy_cents=200,
            idempotency_key=uuid4().hex)
        WalletService.reserve(session, company_id=company_id, task_id=task_id,
                              amount_points=10, idempotency_key=uuid4().hex)
    with factory.begin() as session:
        WalletService.settle_success(session, company_id=company_id, task_id=task_id,
                                     idempotency_key=uuid4().hex)
    assert _stored(engine, GenerationTask, task_id, "status") == TaskStatus.SUCCEEDED
    assert _stored(engine, GenerationTask, task_id, "pricing_snapshot") == original
    release_id, _, release_quote, _ = _seed(engine, pinned=pinned)
    with factory.begin() as session:
        task = session.get(GenerationTask, release_id)
        company_id = task.company_id
        CompanyPointBillingService.credit(session, company_id=company_id, amount_points=20,
            source_kind=PointLotSourceKind.PROMOTIONAL, cash_basis_cents=0, subsidy_cents=200,
            idempotency_key=uuid4().hex)
        WalletService.reserve(session, company_id=company_id, task_id=release_id,
                              amount_points=10, idempotency_key=uuid4().hex)
    with factory.begin() as session:
        WalletService.release_failure(session, company_id=company_id, task_id=release_id,
                                      failure_reason="synthetic failure", idempotency_key=uuid4().hex)
    assert _stored(engine, GenerationTask, release_id, "status") == TaskStatus.FAILED
    assert _stored(engine, GenerationTask, release_id, "pricing_snapshot") == release_quote


def test_empty_0057_upgrade_and_v22_remains_unqualified(tmp_path, monkeypatch):
    url = "sqlite+pysqlite:///" + (tmp_path / "fresh.db").as_posix()
    _migrate(url, HEAD)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
            names = set(connection.scalars(text("SELECT name FROM sqlite_master WHERE type='trigger'")))
            assert {"trg_task_pricing_snapshot_immutable", "trg_outbox_execution_contract_immutable"} <= names
        _seed(engine, pinned=True)
    finally:
        engine.dispose()
    assert facade.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY[PREVIOUS][0] is prior_policy
    assert facade.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY[HEAD] == (policy, behavior)
    assert policy.CATALOG_SHA256 == policy.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    with pytest.raises(behavior.PlatformDatabaseAttestationError, match="v29 catalog is UNQUALIFIED"):
        behavior.validate_platform_database_evidence(None, "api", require_runtime_acl=True, require_head=True)
