from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from platform_api.database import Base, build_session_factory
from platform_api.config import Settings
from platform_api.models import (
    AuditLog,
    Company,
    CompanyModelGrant,
    ModelCapability,
    ModelDefinition,
    PersonalRetailModelGrant,
)
from platform_api.relay_catalog_sync_worker import (
    RELAY_CATALOG_SYNC_SYSTEM_ACTOR,
    RelayCatalogSyncWorker,
    run_loop,
)
from platform_api.relay_client import (
    RelayModelCatalog,
    RelayModelCatalogRead,
    RelayModelReleaseEvidence,
    RelayPermanentError,
)
from platform_api.services.errors import ConflictError
from platform_api.services.models import ModelCatalogService

from .test_model_capability_v1_contract import _mode, canonical_capability


CAPABILITY_REVISION = "sha256:" + ("a" * 64)
CATALOG_REVISION = "sha256:" + ("b" * 64)
NEXT_CAPABILITY_REVISION = "sha256:" + ("c" * 64)
NEXT_CATALOG_REVISION = "sha256:" + ("d" * 64)
PUBLISHED_ROUTE_REVISION = "sha256:" + ("e" * 64)


def _catalog(
    *,
    model_id: str = "seedream-periodic",
    capability: dict | None = None,
    capability_revision: str = CAPABILITY_REVISION,
    catalog_revision: str = CATALOG_REVISION,
    lifecycle: str = "published_route",
    managed_route: bool = True,
    customer_callable: bool = True,
    model_published_route_revision: str = PUBLISHED_ROUTE_REVISION,
) -> RelayModelCatalog:
    resolved = capability or canonical_capability(
        modes={"text_to_image": _mode(output_counts=[1, 2])}
    )
    return RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision_scope": "transport_snapshot",
            "published_route_revision": PUBLISHED_ROUTE_REVISION,
            "catalog_revision": catalog_revision,
            "data": [
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "id": model_id,
                    "object": "model",
                    "capability_revision": capability_revision,
                    "lifecycle": lifecycle,
                    "managed_route": managed_route,
                    "customer_callable": customer_callable,
                    "published_route_revision": (
                        model_published_route_revision
                    ),
                    "capabilities": resolved,
                }
            ],
        }
    )


def _empty_catalog(*, catalog_revision: str) -> RelayModelCatalog:
    return RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision_scope": "transport_snapshot",
            "published_route_revision": PUBLISHED_ROUTE_REVISION,
            "catalog_revision": catalog_revision,
            "data": [],
        }
    )


def _release_evidence(catalog: RelayModelCatalog) -> RelayModelReleaseEvidence:
    return RelayModelReleaseEvidence.model_validate(
        {
            "schema_version": 1,
            "object": "relay.model_release_evidence",
            "catalog_revision": catalog.catalog_revision,
            "catalog_revision_scope": catalog.catalog_revision_scope,
            "published_route_revision": catalog.published_route_revision,
            "generated_at": datetime.now(timezone.utc),
            "test_freshness_max_age_seconds": 900,
            "models": [
                {
                    "public_model_id": item.id,
                    "capability_revision": item.capability_revision,
                    "published_route_revision": (
                        item.published_route_revision or None
                    ),
                    "routing_release_sha256": "sha256:" + "1" * 64,
                    "provider_cost_readiness_sha256": (
                        "sha256:" + "2" * 64
                    ),
                    "provider_cost_ready": False,
                    "provider_cost_rectangle_count": 0,
                    "provider_cost_ready_rectangle_count": 0,
                    "route_count": 0,
                    "enabled_route_count": 0,
                    "accepted_route_count": 0,
                    "fresh_test_count": 0,
                    "latest_successful_test_at": None,
                    "status": "blocked",
                    "routes": [],
                }
                for item in catalog.data
            ],
        }
    )


class ConditionalCatalogClient:
    def __init__(self, catalog: RelayModelCatalog) -> None:
        self._catalog = catalog
        self.evidence = _release_evidence(catalog)
        self.calls: list[str | None] = []
        self.evidence_calls = 0

    @property
    def catalog(self) -> RelayModelCatalog:
        return self._catalog

    @catalog.setter
    def catalog(self, value: RelayModelCatalog) -> None:
        self._catalog = value
        self.evidence = _release_evidence(value)

    def get_model_catalog(
        self, *, if_none_match: str | None = None, **_
    ) -> RelayModelCatalogRead:
        self.calls.append(if_none_match)
        etag = f'"{self.catalog.catalog_revision}"'
        if if_none_match == etag:
            return RelayModelCatalogRead(
                catalog=None,
                etag=etag,
                not_modified=True,
            )
        return RelayModelCatalogRead(
            catalog=self.catalog,
            etag=etag,
            not_modified=False,
        )

    def get_model_release_evidence(self, **_) -> RelayModelReleaseEvidence:
        self.evidence_calls += 1
        return self.evidence


@pytest.fixture
def worker_db():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    try:
        yield engine, factory
    finally:
        engine.dispose()


def test_periodic_worker_creates_one_system_audited_unpublished_draft(
    worker_db,
) -> None:
    _, factory = worker_db
    capability = canonical_capability(
        modes={"text_to_image": _mode(output_counts=[1, 2])}
    )
    client = ConditionalCatalogClient(_catalog(capability=capability))
    worker = RelayCatalogSyncWorker(factory, client)

    first = worker.run_once()
    assert first.leader is True
    assert first.not_modified is False
    assert first.catalog_revision == CATALOG_REVISION
    assert first.reconciliation is not None
    assert first.reconciliation.created_count == 1
    assert first.reconciliation.synced_count == 1

    second = worker.run_once()
    assert second.leader is True
    assert second.not_modified is True
    assert client.calls == [None, f'"{CATALOG_REVISION}"']

    with factory() as session:
        models = session.scalars(select(ModelDefinition)).all()
        assert len(models) == 1
        model = models[0]
        assert model.slug == "seedream-periodic"
        assert model.provider_key == "relay"
        assert model.billing_mode == "per_item"
        assert model.active is False
        assert model.published_at is None
        assert model.relay_capability_revision is None
        assert model.relay_capability_approved_ceiling is None
        assert model.relay_capability_candidate_revision == CAPABILITY_REVISION
        assert session.scalars(
            select(ModelCapability).where(ModelCapability.model_id == model.id)
        ).one().config == capability
        assert session.scalars(select(CompanyModelGrant)).all() == []
        assert session.scalars(select(PersonalRetailModelGrant)).all() == []
        audits = session.scalars(
            select(AuditLog).order_by(AuditLog.created_at, AuditLog.id)
        ).all()
        assert [audit.action for audit in audits] == [
            "model.create",
            "model.relay_capability.candidate_sync",
            "model.relay_catalog.reconcile",
        ]
        assert all(audit.actor_user_id is None for audit in audits)
        assert all(audit.actor_kind == "system" for audit in audits)
        assert all(
            audit.actor_key == RELAY_CATALOG_SYNC_SYSTEM_ACTOR
            for audit in audits
        )
        assert audits[-1].after_summary["automatic_approval"] is False
        assert audits[-1].after_summary["automatic_publish"] is False
        assert audits[-1].after_summary["automatic_distribution"] is False
        assert audits[-1].after_summary["automatic_pricing"] is False
        assert audits[-1].after_summary["execution_actor"] == {
            "kind": "system",
            "key": RELAY_CATALOG_SYNC_SYSTEM_ACTOR,
            "trigger": "periodic_worker",
        }


def test_periodic_worker_syncs_candidate_drift_without_rewriting_draft(
    worker_db,
) -> None:
    _, factory = worker_db
    original = canonical_capability(
        modes={"text_to_video": _mode(output_counts=[1], max_images=1)}
    )
    client = ConditionalCatalogClient(_catalog(capability=original))
    first_worker = RelayCatalogSyncWorker(factory, client)
    first_worker.run_once()

    drifted = deepcopy(original)
    drifted["modes"]["text_to_video"]["limits"]["max_images"] = 2
    client.catalog = _catalog(
        capability=drifted,
        capability_revision=NEXT_CAPABILITY_REVISION,
        catalog_revision=NEXT_CATALOG_REVISION,
    )
    changed = first_worker.run_once()
    assert changed.reconciliation is not None
    assert changed.reconciliation.created_count == 0
    assert changed.reconciliation.synced_count == 1

    with factory() as session:
        model = session.scalar(select(ModelDefinition))
        assert model is not None
        assert model.billing_mode == "per_second"
        assert model.relay_capability_candidate == drifted
        assert (
            model.relay_capability_candidate_revision
            == NEXT_CAPABILITY_REVISION
        )
        # The administrator-owned draft and every release/distribution field
        # remain unchanged by discovery drift.
        capability = session.scalar(
            select(ModelCapability).where(ModelCapability.model_id == model.id)
        )
        assert capability is not None
        assert capability.config == original
        assert model.active is False
        assert model.published_at is None
        assert model.relay_capability_revision is None


def test_periodic_worker_rolls_back_the_complete_invalid_catalog_and_etag(
    worker_db,
) -> None:
    _, factory = worker_db
    invalid = _catalog(model_id="invalid_model_id")
    client = ConditionalCatalogClient(invalid)
    worker = RelayCatalogSyncWorker(factory, client)

    with pytest.raises(ConflictError, match="无法安全映射"):
        worker.run_once()
    assert worker._etag is None
    with factory() as session:
        assert session.scalars(select(ModelDefinition)).all() == []
        assert session.scalars(select(AuditLog)).all() == []


def test_separate_instances_remain_database_idempotent(worker_db) -> None:
    _, factory = worker_db
    catalog = _catalog()
    first = RelayCatalogSyncWorker(factory, ConditionalCatalogClient(catalog))
    second = RelayCatalogSyncWorker(factory, ConditionalCatalogClient(catalog))

    first.run_once()
    repeated = second.run_once()
    assert repeated.reconciliation is not None
    assert repeated.reconciliation.created_count == 0
    assert repeated.reconciliation.synced_count == 0
    assert repeated.reconciliation.unchanged_count == 1

    with factory() as session:
        assert len(session.scalars(select(ModelDefinition)).all()) == 1
        assert len(session.scalars(select(AuditLog)).all()) == 3


def test_candidate_downgrade_disables_an_inactive_models_enabled_company_grant(
    worker_db,
) -> None:
    _, factory = worker_db
    client = ConditionalCatalogClient(_catalog())
    worker = RelayCatalogSyncWorker(factory, client)
    worker.run_once()

    with factory.begin() as session:
        model = session.scalar(select(ModelDefinition))
        assert model is not None and model.active is False
        company = Company(
            id="00000000-0000-4000-8000-000000000701",
            name="Relay downgrade regression",
            billing_version=2,
        )
        session.add(company)
        session.flush()
        grant = CompanyModelGrant(
            company_id=company.id,
            model_id=model.id,
            enabled=True,
            price_per_item_points=10,
            config_override={},
        )
        session.add(grant)
        session.flush()
        model_id, grant_id = model.id, grant.id

    client.catalog = _catalog(
        catalog_revision=NEXT_CATALOG_REVISION,
        lifecycle="reviewed_candidate",
        managed_route=False,
        customer_callable=False,
        model_published_route_revision="",
    )
    outcome = worker.run_once()
    assert outcome.reconciliation is not None
    assert outcome.reconciliation.invalidated_model_ids == (model_id,)

    with factory() as session:
        assert session.get(ModelDefinition, model_id).active is False
        assert session.get(CompanyModelGrant, grant_id).enabled is False
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "model.relay_route.invalidate"
            )
        )
        assert audit is not None
        invalidation = audit.after_summary["relay_route_invalidation"]
        assert invalidation["reason"] == "reviewed_candidate"
        assert invalidation["disabled_company_grant_ids"] == [grant_id]


def test_reviewed_candidate_slug_collision_preserves_unowned_manual_model_and_grants(
    worker_db,
) -> None:
    _, factory = worker_db
    capability = canonical_capability(
        modes={"text_to_image": _mode(output_counts=[1, 2])}
    )
    with factory.begin() as session:
        model = ModelCatalogService.create_model(
            session,
            slug="seedream-periodic",
            display_name="Manual provider model",
            provider_key="manual-provider",
            capability_version=1,
            capabilities=[("generation", capability)],
            billing_mode="per_item",
            active=True,
        )
        company = Company(
            id="00000000-0000-4000-8000-000000000702",
            name="Manual provider regression",
            billing_version=2,
        )
        session.add(company)
        session.flush()
        company_grant = CompanyModelGrant(
            company_id=company.id,
            model_id=model.id,
            enabled=True,
            price_per_item_points=10,
            config_override={},
        )
        personal_grant = PersonalRetailModelGrant(
            model_id=model.id,
            enabled=True,
            price_per_item_points=10,
            config_override={},
        )
        session.add_all([company_grant, personal_grant])
        session.flush()
        model_id = model.id
        company_grant_id = company_grant.id
        personal_grant_id = personal_grant.id

    client = ConditionalCatalogClient(
        _catalog(
            lifecycle="reviewed_candidate",
            managed_route=False,
            customer_callable=False,
            model_published_route_revision="",
        )
    )
    outcome = RelayCatalogSyncWorker(factory, client).run_once()

    assert outcome.reconciliation is not None
    assert outcome.reconciliation.invalidated_model_ids == ()
    with factory() as session:
        persisted = session.get(ModelDefinition, model_id)
        assert persisted is not None
        assert persisted.active is True
        assert persisted.provider_key == "manual-provider"
        assert persisted.relay_capability_candidate_revision is None
        assert session.get(CompanyModelGrant, company_grant_id).enabled is True
        assert session.get(PersonalRetailModelGrant, personal_grant_id).enabled is True
        assert session.scalars(
            select(AuditLog).where(
                AuditLog.action == "model.relay_route.invalidate"
            )
        ).all() == []


def test_empty_catalog_snapshot_disables_missing_models_and_grants(
    worker_db,
) -> None:
    _, factory = worker_db
    capability = canonical_capability(
        modes={"text_to_image": _mode(output_counts=[1, 2])}
    )
    with factory.begin() as session:
        model = ModelCatalogService.create_model(
            session,
            slug="seedream-periodic",
            display_name="Previously released Relay model",
            provider_key="relay",
            capability_version=1,
            capabilities=[("generation", capability)],
            billing_mode="per_item",
            active=True,
        )
        model.relay_capability_revision = CAPABILITY_REVISION
        model.relay_capability_candidate_revision = CAPABILITY_REVISION
        model.relay_capability_candidate_catalog_revision = CATALOG_REVISION
        model.relay_capability_candidate = capability
        model.relay_capability_approved_ceiling = capability
        model.relay_capability_approved_catalog_revision = CATALOG_REVISION
        grant = PersonalRetailModelGrant(
            model_id=model.id,
            enabled=True,
            price_per_item_points=10,
            config_override={},
        )
        session.add(grant)
        session.flush()
        model_id, grant_id = model.id, grant.id

    client = ConditionalCatalogClient(
        _empty_catalog(catalog_revision=NEXT_CATALOG_REVISION)
    )
    worker = RelayCatalogSyncWorker(factory, client)
    outcome = worker.run_once()
    assert outcome.reconciliation is not None
    assert outcome.reconciliation.invalidated_model_ids == (model_id,)
    assert outcome.catalog_revision == NEXT_CATALOG_REVISION

    with factory() as session:
        assert session.get(ModelDefinition, model_id).active is False
        assert session.get(PersonalRetailModelGrant, grant_id).enabled is False
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "model.relay_route.invalidate"
            )
        )
        assert audit is not None
        assert (
            audit.after_summary["relay_route_invalidation"]["reason"]
            == "missing"
        )


def test_mismatched_catalog_and_evidence_cannot_mutate_database_or_etag(
    worker_db,
) -> None:
    _, factory = worker_db
    client = ConditionalCatalogClient(_catalog())
    client.evidence = client.evidence.model_copy(
        update={"catalog_revision": NEXT_CATALOG_REVISION}
    )
    worker = RelayCatalogSyncWorker(factory, client)

    with pytest.raises(RelayPermanentError, match="snapshots do not match"):
        worker.run_once()

    assert worker._etag is None
    assert worker._catalog is None
    with factory() as session:
        assert session.scalars(select(ModelDefinition)).all() == []
        assert session.scalars(select(AuditLog)).all() == []


def test_protected_worker_rejects_unmanaged_routes_before_any_mutation(
    worker_db,
) -> None:
    _, factory = worker_db
    client = ConditionalCatalogClient(_catalog(managed_route=False))
    worker = RelayCatalogSyncWorker(
        factory,
        client,
        require_managed_routes=True,
    )

    with pytest.raises(RelayPermanentError, match="unmanaged Relay route"):
        worker.run_once()

    assert worker._etag is None
    assert worker._catalog is None
    with factory() as session:
        assert session.scalars(select(ModelDefinition)).all() == []
        assert session.scalars(select(AuditLog)).all() == []


def test_non_leader_skips_relay_and_database_work(worker_db, monkeypatch) -> None:
    _, factory = worker_db
    client = ConditionalCatalogClient(_catalog())
    worker = RelayCatalogSyncWorker(factory, client)

    @contextmanager
    def not_leader():
        yield False

    monkeypatch.setattr(worker, "_leader", not_leader)
    outcome = worker.run_once()
    assert outcome.leader is False
    assert client.calls == []
    with factory() as session:
        assert session.scalars(select(ModelDefinition)).all() == []


def test_run_loop_retries_ordinary_failures_without_stopping_worker() -> None:
    calls = 0

    def run_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary Relay failure")
        return SimpleNamespace()

    waits: list[float] = []

    class StopAfterTwoWaits:
        def is_set(self) -> bool:
            return len(waits) >= 2

        def wait(self, delay: float) -> None:
            waits.append(delay)

    run_loop(
        SimpleNamespace(run_once=run_once),
        stop_event=StopAfterTwoWaits(),  # type: ignore[arg-type]
        interval_seconds=60,
        retry_base_seconds=5,
        retry_cap_seconds=300,
    )
    assert calls == 2
    assert waits == [5, 60]


def test_run_loop_keeps_database_attestation_fail_closed() -> None:
    calls: list[str] = []

    def fail_preflight() -> None:
        calls.append("preflight")
        raise RuntimeError("database drift")

    with pytest.raises(RuntimeError, match="database drift"):
        run_loop(
            SimpleNamespace(run_once=lambda: calls.append("mutation")),
            stop_event=SimpleNamespace(is_set=lambda: False),
            interval_seconds=60,
            retry_base_seconds=5,
            retry_cap_seconds=300,
            once=True,
            preflight=fail_preflight,
        )
    assert calls == ["preflight"]


def test_catalog_worker_schedule_configuration_is_strict_and_bounded() -> None:
    configured = Settings(
        relay_catalog_sync_enabled="false",  # type: ignore[arg-type]
        relay_catalog_sync_interval_seconds=120,
        relay_catalog_sync_retry_base_seconds=10,
        relay_catalog_sync_retry_cap_seconds=60,
    )
    assert configured.relay_catalog_sync_enabled is False
    assert configured.relay_catalog_sync_interval_seconds == 120

    with pytest.raises(ValueError, match="accepts only true or false"):
        Settings(relay_catalog_sync_enabled="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Settings(relay_catalog_sync_interval_seconds=4)
    with pytest.raises(ValueError, match="cannot exceed"):
        Settings(
            relay_catalog_sync_retry_base_seconds=200,
            relay_catalog_sync_retry_cap_seconds=100,
        )
