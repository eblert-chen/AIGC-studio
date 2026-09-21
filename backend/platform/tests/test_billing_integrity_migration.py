from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from platform_api import database_privileges as facade
from platform_api import database_privileges_behavior_v21 as behavior
from platform_api import database_privileges_v20 as old_policy
from platform_api import database_privileges_v21 as policy
from platform_api.models import TaskStatus

from .test_enterprise_monthly_billing import _seed_point_company, _task


HEAD = "0056_billing_integrity_guards"
OLD_HEAD = "0055_commercial_plan_revisions"
PAYLOAD = {"account_id": "acct-test", "route": {"id": 7, "version": 2}}
ENCODED = json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":"))
DIGEST = hashlib.sha256(ENCODED.encode()).hexdigest()


def _config():
    return Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))


def _upgrade_current_and_check(config, engine):
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == facade.PLATFORM_ALEMBIC_HEAD


def _seed_task(engine, *, bound=False):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        company, user, model = _seed_point_company(session)
        task = _task(company=company, user=user, model=model, points=10,
                     key=f"route-proof-{uuid4().hex}", status=TaskStatus.DRAFT)
        if bound:
            task.provider_route_evidence = PAYLOAD
            task.provider_route_evidence_sha256 = DIGEST
        session.add(task)
        session.flush()
        return task.id


@pytest.fixture
def integrity_postgres_engine(monkeypatch):
    url = os.getenv("PLATFORM_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("requires explicit isolated PLATFORM_TEST_DATABASE_URL")
    schema = f"billing_integrity_{uuid4().hex}"
    admin = create_engine(url, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    schema_url = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", schema_url)
    engine = create_engine(schema_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
        assert schema.startswith("billing_integrity_") and len(schema) == 50
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def _assert_bound_mutation_rejected(engine, task_id, statement, values):
    with pytest.raises(DBAPIError, match="task provider route evidence is immutable"):
        with engine.begin() as connection:
            connection.execute(text(statement), {"id": task_id, **values})
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT provider_route_evidence, provider_route_evidence_sha256 "
            "FROM generation_tasks WHERE id=:id"
        ), {"id": task_id}).one()
        assert row.provider_route_evidence == PAYLOAD
        assert row.provider_route_evidence_sha256 == DIGEST


@pytest.mark.parametrize("source", ["empty", "0055"])
def test_postgres_0056_repairs_json_guard_without_loosening_bound_evidence(
    integrity_postgres_engine, source,
):
    engine = integrity_postgres_engine
    config = _config()
    if source == "0055":
        command.upgrade(config, OLD_HEAD)
        existing_id = _seed_task(engine, bound=True)
        # Prove the original regression against the unmodified prior migration.
        with pytest.raises(DBAPIError, match="operator does not exist: json = json"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE generation_tasks SET failure_reason='ordinary update' WHERE id=:id"
                ), {"id": existing_id})
    command.upgrade(config, HEAD)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
    if source == "0055":
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE generation_tasks SET failure_reason='recovery resumed' WHERE id=:id"
            ), {"id": existing_id})
    task_id = _seed_task(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE generation_tasks SET provider_route_evidence=CAST(:payload AS json), "
            "provider_route_evidence_sha256=:sha WHERE id=:id"
        ), {"id": task_id, "payload": ENCODED, "sha": DIGEST})
    with engine.begin() as connection:
        # Semantically identical JSON, reordered at both levels, is harmless.
        connection.execute(text(
            "UPDATE generation_tasks SET provider_route_evidence=CAST(:payload AS json), "
            "failure_reason='recovery observed' WHERE id=:id"
        ), {"id": task_id,
            "payload": '{"route":{"version":2,"id":7},"account_id":"acct-test"}'})
        connection.execute(text(
            "UPDATE generation_tasks SET timeout_checked_at=CURRENT_TIMESTAMP WHERE id=:id"
        ), {"id": task_id})
    for statement, values in [
        ("UPDATE generation_tasks SET provider_route_evidence=CAST(:payload AS json) WHERE id=:id",
         {"payload": '{"account_id":"acct-other","route":{"id":7,"version":2}}'}),
        ("UPDATE generation_tasks SET provider_route_evidence_sha256=:sha WHERE id=:id",
         {"sha": "f" * 64}),
        ("UPDATE generation_tasks SET provider_route_evidence=NULL, provider_route_evidence_sha256=NULL WHERE id=:id",
         {}),
    ]:
        _assert_bound_mutation_rejected(engine, task_id, statement, values)
    _upgrade_current_and_check(config, engine)


def test_sqlite_0056_fresh_head_preserves_existing_task_guard(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'billing-integrity.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = _config()
    command.upgrade(config, HEAD)
    engine = create_engine(url)
    try:
        task_id = _seed_task(engine)
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
            connection.execute(text(
                "UPDATE generation_tasks SET provider_route_evidence=:payload, "
                "provider_route_evidence_sha256=:sha WHERE id=:id"
            ), {"id": task_id, "payload": ENCODED, "sha": DIGEST})
            connection.execute(text(
                "UPDATE generation_tasks SET failure_reason='recovery observed' WHERE id=:id"
            ), {"id": task_id})
        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE generation_tasks SET provider_route_evidence_sha256=:sha WHERE id=:id"
                ), {"id": task_id, "sha": "f" * 64})
        _upgrade_current_and_check(config, engine)
    finally:
        engine.dispose()


def test_v21_preserves_acl_and_refuses_unqualified_runtime_catalog():
    assert facade.PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY[HEAD] == (policy, behavior)
    assert policy.TABLES == old_policy.TABLES
    assert policy.PRIVILEGES_BY_PROCESS == old_policy.PRIVILEGES_BY_PROCESS
    assert policy.EXPECTED_TABLE_ACL == old_policy.EXPECTED_TABLE_ACL
    assert policy.CATALOG_SHA256 == policy.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    # Fail closed before inspecting supplied catalog details or a principal.
    with pytest.raises(behavior.PlatformDatabaseAttestationError, match="UNQUALIFIED"):
        behavior.validate_platform_database_evidence(
            None, "api", require_runtime_acl=True, require_head=True,
        )
