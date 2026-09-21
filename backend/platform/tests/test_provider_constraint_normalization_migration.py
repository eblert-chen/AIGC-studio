from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from platform_api.models import (
    ChannelCostEntry, GenerationTask, ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan, RelayTaskStageEvent,
)
from .test_billing_integrity_migration import (
    HEAD, OLD_HEAD, _config, _seed_task, _upgrade_current_and_check, integrity_postgres_engine,
)


IDENTITY = {
    "schema_version": 2, "route_id": 7, "provider_identity_status": "bound",
    "provider_name": "google_gemini", "provider_account_id": "fixture-account",
    "provider_channel_id": 17, "provider_route_id": 7, "provider_key_index": 0,
    "provider_key_fingerprint": "a" * 64,
    "provider_credential_version": "11111111-1111-4111-8111-111111111111",
    "routing_release_sha256": "sha256:" + "b" * 64,
}
OLD_CHECKS = {
    "ck_channel_cost_provider_identity", "ck_relay_task_stage_provider_identity",
    "ck_model_commercial_plan_route_identity", "ck_model_commercial_execution_receipt",
}
TARGET_CHECKS = {
    "ck_channel_cost_schema", "ck_channel_cost_provider_identity_status",
    "ck_channel_cost_provider_identity_complete", "ck_relay_task_stage_provider_identity_status",
    "ck_relay_task_stage_provider_identity_complete", "ck_relay_task_stage_unassigned_route",
    "ck_task_provider_route_evidence_complete", "ck_task_provider_route_evidence_sha",
    "ck_model_commercial_plan_route_identity_complete", "ck_model_commercial_plan_route_identity_sha",
    "ck_model_commercial_execution_receipt_complete", "ck_model_commercial_execution_receipt_sha",
    "ck_model_commercial_execution_route_sha",
}


def _insert(engine, model, values):
    with engine.begin() as connection:
        connection.execute(model.__table__.insert().values(**values))
        return dict(connection.execute(select(model.__table__).where(model.id == values["id"])).mappings().one())


def _seed_facts(engine):
    task_id = _seed_task(engine, bound=True)
    now = datetime.now(timezone.utc)
    with engine.connect() as connection:
        task = connection.execute(select(GenerationTask.__table__).where(GenerationTask.id == task_id)).mappings().one()
    cost = _insert(engine, ChannelCostEntry, {
        "id": str(uuid4()), "amount_cents": 125, "idempotency_key": uuid4().hex,
        "channel_key": "official.fixture", "channel_type": "OFFICIAL",
        "occurred_at": now, "external_reference": "synthetic-fixture",
        "company_id": task["company_id"], "task_id": task_id, "source": "RELAY",
        **IDENTITY,
    })
    stage = _insert(engine, RelayTaskStageEvent, {
        "id": str(uuid4()), "company_id": task["company_id"], "task_id": task_id,
        "relay_job_id": str(uuid4()), "stage": "artifact_stored", "occurred_at": now,
        "channel_key": "official.fixture", "channel_type": "official", "duration_ms": 1,
        "error_code": "", "delivery_timestamp": now, "payload_sha256": "c" * 64,
        "request_id": uuid4().hex, "received_at": now, **IDENTITY,
    })
    plan = _insert(engine, ModelCommercialReleasePlan, {
        "id": str(uuid4()), "model_id": task["model_id"],
        "candidate_revision": "sha256:" + "d" * 64,
        "candidate_catalog_revision": "sha256:" + "e" * 64,
        "capability_version": 1, "billing_mode": "per_item",
        "provider_cost_currency": "CNY", "provider_cost_formula": {"unit": "item"},
        "provider_cost_micros": 100, "provider_cost_cny_micros": 100,
        "provider_cost_evidence_kind": "fixture", "provider_cost_evidence_reference": "fixture",
        "provider_cost_evidence_sha256": "a" * 64, "provider_cost_effective_at": now,
        "fx_cny_micros_per_currency_unit": 1_000_000, "fx_source": "fixture", "fx_version": "1",
        "fx_evidence_sha256": "b" * 64, "fx_effective_at": now,
        "minimum_price_points": 1, "personal_price_points": 1, "enterprise_price_points": 1,
        "enterprise_distribution_scope": "all_active_point_companies_at_release",
        "approval_reason": "synthetic migration fixture", "approved_by_user_id": task["user_id"],
        "idempotency_key": uuid4().hex, "content_sha256": "c" * 64,
        "approved_route_identity": {"route_id": 7}, "approved_route_identity_sha256": "f" * 64,
    })
    execution = _insert(engine, ModelCommercialReleaseExecution, {
        "id": str(uuid4()), "plan_id": plan["id"], "state": "approved",
    })
    return cost, stage, plan, execution, task_id


def _clone(model, row, changes):
    unique = {"id": str(uuid4())}
    if model in (ChannelCostEntry, ModelCommercialReleasePlan, GenerationTask):
        unique["idempotency_key"] = uuid4().hex
    if model is ModelCommercialReleasePlan:
        unique.update(content_sha256=uuid4().hex * 2, candidate_revision="sha256:" + uuid4().hex * 2)
    return {**row, **unique, **changes}


def _snapshot(engine):
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id")).scalars().all()
            for table in ("generation_tasks", "channel_cost_entries", "relay_task_stage_events",
                          "model_commercial_release_plans", "model_commercial_release_executions")
        }


def test_postgres_0056_normalizes_checks_without_rewriting_history(integrity_postgres_engine):
    engine = integrity_postgres_engine
    config = _config()
    command.upgrade(config, OLD_HEAD)
    cost, stage, plan, execution, _ = _seed_facts(engine)
    before = _snapshot(engine)
    command.upgrade(config, HEAD)
    assert _snapshot(engine) == before
    # Alembic's metadata check requires current head. Inspect the exact 0056
    # constraints and historical facts here, then check metadata after the
    # explicit upgrade to current head at the end of this regression.
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE connamespace=current_schema()::regnamespace AND contype='c'"
        )).all()
        assert all(validated for name, validated in rows if name in TARGET_CHECKS)
        names = {name for name, _ in rows}
        assert TARGET_CHECKS <= names
        assert OLD_CHECKS.isdisjoint(names)
        assert not any(name.endswith("_0056") for name in names)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD

    # Keep strict format checks from 0051, and close its SQL UNKNOWN loopholes.
    for changes in (
        {"provider_key_fingerprint": None}, {"provider_key_fingerprint": "g" * 64},
        {"provider_key_fingerprint": "A" * 64}, {"provider_credential_version": "bad-uuid"},
        {"routing_release_sha256": "x" * 71}, {"provider_channel_id": None},
        {"provider_key_index": None}, {"route_id": 0}, {"provider_identity_status": "other"},
        {"schema_version": 3},
    ):
        with pytest.raises(DBAPIError, match="ck_channel_cost_"):
            _insert(engine, ChannelCostEntry, _clone(ChannelCostEntry, cost, changes))
    with pytest.raises(DBAPIError, match="ck_relay_task_stage_provider_identity_complete"):
        _insert(engine, RelayTaskStageEvent, _clone(RelayTaskStageEvent, stage, {"provider_key_fingerprint": None}))
    legacy = {name: None for name in IDENTITY if name not in ("schema_version", "provider_identity_status", "route_id")}
    legacy.update(schema_version=1, provider_identity_status="legacy_unknown", route_id=None)
    _insert(engine, ChannelCostEntry, _clone(ChannelCostEntry, cost, legacy))
    unassigned = {**legacy, "provider_identity_status": "unassigned", "route_id": 7}
    with pytest.raises(DBAPIError, match="ck_relay_task_stage_unassigned_route"):
        _insert(engine, RelayTaskStageEvent, _clone(RelayTaskStageEvent, stage, unassigned))

    for changes in (
        {"approved_route_identity": None}, {"approved_route_identity_sha256": None},
        {"approved_route_identity_sha256": "G" * 64},
    ):
        with pytest.raises(DBAPIError, match="ck_model_commercial_plan_route_identity_"):
            _insert(engine, ModelCommercialReleasePlan, _clone(ModelCommercialReleasePlan, plan, changes))
    for receipt_sha, route_sha in ((None, "a" * 64), ("G" * 64, "a" * 64), ("a" * 64, "G" * 64)):
        with pytest.raises(DBAPIError, match="ck_model_commercial_execution_"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE model_commercial_release_executions SET publication_receipt='{}'::json, "
                    "publication_receipt_sha256=:receipt, released_route_identity_sha256=:route WHERE id=:id"
                ), {"receipt": receipt_sha, "route": route_sha, "id": execution["id"]})
    task_id = _seed_task(engine)
    for digest in (None, "G" * 64):
        with pytest.raises(DBAPIError, match="ck_task_provider_route_evidence_"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE generation_tasks SET provider_route_evidence='{}'::json, "
                    "provider_route_evidence_sha256=:sha WHERE id=:id"
                ), {"id": task_id, "sha": digest})
    before_current = _snapshot(engine)
    _upgrade_current_and_check(config, engine)
    assert _snapshot(engine) == before_current


@pytest.mark.parametrize("invalid_history", ["task_missing_sha", "cost_missing_fingerprint"])
def test_postgres_0056_rejects_unknown_check_history_atomically(integrity_postgres_engine, invalid_history):
    engine = integrity_postgres_engine
    config = _config()
    command.upgrade(config, OLD_HEAD)
    cost, _, _, _, _ = _seed_facts(engine)
    if invalid_history == "task_missing_sha":
        task_id = _seed_task(engine)
        with engine.connect() as connection:
            task = dict(connection.execute(select(GenerationTask.__table__).where(GenerationTask.id == task_id)).mappings().one())
        # INSERT reaches the legacy CHECK directly, without its separately
        # broken JSON UPDATE trigger hiding the three-valued-logic defect.
        _insert(engine, GenerationTask, _clone(GenerationTask, task, {
            "provider_route_evidence": {}, "provider_route_evidence_sha256": None,
        }))
    else:
        _insert(engine, ChannelCostEntry, _clone(ChannelCostEntry, cost, {"provider_key_fingerprint": None}))
    before = _snapshot(engine)
    with pytest.raises(DBAPIError, match="_0056"):
        command.upgrade(config, HEAD)
    assert _snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == OLD_HEAD
        names = set(connection.execute(text(
            "SELECT conname FROM pg_constraint WHERE connamespace=current_schema()::regnamespace"
        )).scalars())
        assert OLD_CHECKS <= names
        assert not any(name.endswith("_0056") for name in names)
