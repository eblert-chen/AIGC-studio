from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from platform_api.models import (
    AuditLog, Company, ModelCommercialReleaseExecution, ModelCommercialReleasePlan, ModelDefinition, utcnow,
)
from platform_api.relay_catalog_sync_worker import RelayCatalogSyncWorker
from platform_api.relay_client import RelayModelReleaseEvidence, RelayTemporaryError
from platform_api.schemas import GenerationReadinessStateResponse, ModelCommercialReleasePlanRequest
from platform_api.services.model_commercial_release import _canonical_sha256
from platform_api.services.models import ModelCatalogService, ModelGrantService
from platform_api.services.personal import PersonalModelService

from .test_model_capability_v1_contract import _admin_headers
from .test_model_commercial_release import _commercial_body, _prepare_video_draft
from .test_relay_capability_sync import CatalogRelayClient, _approve_candidate
from .test_task_capability_v3_contract import _catalog_v3
from .test_commercial_task_admission import _counts, _task_fixture


def test_manual_publication_requires_commercial_authority_before_first_grant(app, client):
    headers = _admin_headers(client, "distribution-first-publication")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    approved = _approve_candidate(client, headers, model, reason="审阅模型能力")
    assert approved.status_code == 200, approved.text
    publish_url = f"/api/v1/platform-admin/models/{model['id']}/publish"
    rejected = client.post(publish_url, headers=headers)
    assert rejected.status_code == 409, rejected.text
    assert "商业价格计划" in rejected.text
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored.active is False and stored.published_at is None

    plan = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert plan.status_code == 200, plan.text
    # Approval alone is not a released commercial execution.
    rejected = client.post(publish_url, headers=headers)
    assert rejected.status_code == 409, rejected.text
    worker = RelayCatalogSyncWorker(app.state.session_factory, app.state.relay_client)
    result = worker.run_once()
    assert result.commercial_release.released_count == 1
    with app.state.session_factory() as session:
        assert session.get(ModelDefinition, model["id"]).active is True


def test_legacy_publication_recovers_only_after_stop_and_exact_commercial_release(app, client):
    headers = _admin_headers(client, "distribution-legacy-recovery")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    approved = _approve_candidate(client, headers, model, reason="审阅历史模型能力")
    assert approved.status_code == 200, approved.text
    # Reproduce the durable state left by the previous publish implementation.
    original_published_at = utcnow()
    with app.state.session_factory.begin() as session:
        stored = session.get(ModelDefinition, model["id"])
        stored.active = True
        stored.published_at = original_published_at

    plan_url = f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan"
    body = _commercial_body(model=model)
    rejected = client.put(plan_url, headers=headers, json=body)
    assert rejected.status_code == 409, rejected.text
    assert "先停用" in rejected.text
    stopped = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/disable", headers=headers
    )
    assert stopped.status_code == 200, stopped.text
    plan = client.put(plan_url, headers=headers, json=body)
    assert plan.status_code == 200, plan.text
    with app.state.session_factory() as session:
        audit = session.scalar(select(AuditLog).where(
            AuditLog.action == "model.commercial_release_recovery.approve",
            AuditLog.target_id == model["id"],
        ))
        assert audit is not None
        assert audit.actor_user_id == headers["X-Platform-Admin-User-ID"]
        assert audit.after_summary["commercial_plan_id"] == plan.json()["id"]
        assert audit.after_summary["requires_explicit_reactivation"] is True

    good_relay = app.state.relay_client
    bad_relay = CatalogRelayClient(
        good_relay.catalog,
        provider_cost_ready=False,
        provider_cost_billing_unit="per_second",
        provider_cost_source_sha256="1" * 64,
        provider_cost_rate_set=True,
    )
    worker = RelayCatalogSyncWorker(app.state.session_factory, bad_relay)
    blocked = worker.run_once()
    assert blocked.commercial_release.released_count == 0
    assert blocked.commercial_release.blocked_count == 1
    worker.relay_client = good_relay
    result = worker.run_once()
    assert result.commercial_release.released_count == 1
    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored.active is False
        assert ModelCatalogService._as_utc(stored.published_at) == original_published_at
    published = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/publish", headers=headers
    )
    assert published.status_code == 200, published.text
    assert published.json()["active"] is True


def _assert_distribution_readiness(app, model, *, code: str | None):
    with app.state.session_factory() as session:
        company = ModelGrantService.list_available_models(
            session, company_id="audit-company", require_relay_approval=True,
            release_evidence=app.state.relay_client.evidence,
        )
        personal = PersonalModelService.list_available(
            session, workspace_id="audit-workspace", require_relay_approval=True,
            release_evidence=app.state.relay_client.evidence,
        )
        for catalog in (company, personal):
            row = next(item for item in catalog if item["id"] == model["id"])
            readiness = row["mode_readiness"]["text_to_video"]["default"]
            GenerationReadinessStateResponse.model_validate(readiness)
            assert readiness["ready"] is (code is None)
            if code is not None:
                blocker = next(item for item in readiness["blockers"] if item["code"] == code)
                assert set(blocker) == {"code", "message", "resource_key", "resource_name", "retryable"}
                assert "sha256:" not in blocker["message"]
            else:
                assert readiness["blockers"] == []


@pytest.mark.parametrize("state", ["acceptance_blocked", "successor_pending"])
def test_customer_readiness_includes_current_commercial_execution(app, client, state):
    headers = _admin_headers(client, f"distribution-readiness-{state}")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    with app.state.session_factory.begin() as session:
        session.add(Company(id="audit-company", name="Audit company", billing_version=2))
    plan = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers, json=_commercial_body(model=model),
    )
    assert plan.status_code == 200, plan.text
    good_relay = app.state.relay_client
    worker = RelayCatalogSyncWorker(app.state.session_factory, good_relay)
    assert worker.run_once().commercial_release.released_count == 1
    _assert_distribution_readiness(app, model, code=None)
    if state == "acceptance_blocked":
        worker.relay_client = CatalogRelayClient(
            good_relay.catalog, evidence_status="blocked",
            provider_cost_billing_unit="per_second",
            provider_cost_source_sha256="1" * 64, provider_cost_rate_set=True,
        )
        result = worker.run_once()
        assert result.commercial_release.items[0]["last_blocker_code"] == "route_acceptance_pending"
        code = "commercial_release_route_acceptance_pending"
    else:
        body = _commercial_body(model=model)
        body.update(supersedes_plan_id=plan.json()["id"], idempotency_key="readiness-successor")
        successor = client.put(
            f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
            headers=headers, json=body,
        )
        assert successor.status_code == 200, successor.text
        code = "commercial_release_reconciliation_pending"
    _assert_distribution_readiness(app, model, code=code)
    worker.relay_client = good_relay
    assert worker.run_once().commercial_release.released_count == 1
    _assert_distribution_readiness(app, model, code=None)


@pytest.mark.parametrize("scope", ["company", "personal"])
@pytest.mark.parametrize("failure", ["evidence_read", "catalog_read", "projection_expired", "route_expired", "snapshot_mismatch"])
def test_released_directory_requires_current_relay_evidence_even_without_worker(
    app, client, tenant, monkeypatch, scope, failure
):
    _, model, headers, task_path, body, wallet_type, wallet_id = _task_fixture(
        app, client, tenant, scope
    )
    models_path = task_path.rsplit("/", 1)[0] + "/models"

    def current_readiness():
        response = client.get(models_path, headers=headers)
        assert response.status_code == 200, response.text
        row = next(item for item in response.json() if item["id"] == model["id"])
        return row["mode_readiness"]["text_to_video"]["default"]

    assert current_readiness()["ready"] is True
    before = _counts(app, wallet_type, wallet_id)
    relay = app.state.relay_client
    good_evidence = relay.evidence
    if failure in {"evidence_read", "catalog_read"}:
        def unavailable(**_):
            raise RelayTemporaryError("local injected Relay outage")

        method = "get_model_release_evidence" if failure == "evidence_read" else "get_model_catalog"
        monkeypatch.setattr(relay, method, unavailable)
        # A failed worker cannot mark the durable released execution blocked.
        with pytest.raises(RelayTemporaryError):
            RelayCatalogSyncWorker(app.state.session_factory, relay).run_once()
        expected_code = "commercial_release_evidence_unavailable"
    else:
        payload = good_evidence.model_dump()
        now = utcnow()
        if failure == "projection_expired":
            generated = now - timedelta(minutes=3)
            payload["generated_at"] = generated
            for item in payload["models"]:
                item["latest_successful_test_at"] = generated - timedelta(seconds=10)
                for route in item["routes"]:
                    route["latest_successful_test_at"] = item["latest_successful_test_at"]
                    route["fresh_until"] = generated + timedelta(seconds=890)
        elif failure == "route_expired":
            # Valid when generated, but already expired when the HTTP read
            # reaches the directory projection. Do not trust fresh=True alone.
            payload["generated_at"] = now - timedelta(seconds=10)
            for item in payload["models"]:
                item["latest_successful_test_at"] = now - timedelta(seconds=20)
                for route in item["routes"]:
                    route["latest_successful_test_at"] = item["latest_successful_test_at"]
                    route["fresh_until"] = now - timedelta(seconds=1)
        else:
            payload["catalog_revision"] = "sha256:" + "9" * 64
        relay.evidence = RelayModelReleaseEvidence.model_validate(payload)
        expected_code = (
            "commercial_release_evidence_unavailable" if failure == "snapshot_mismatch"
            else "commercial_release_evidence_expired"
        )

    readiness = current_readiness()
    assert readiness["ready"] is False and readiness["status"] == "blocked"
    blocker = next(item for item in readiness["blockers"] if item["code"] == expected_code)
    assert blocker["retryable"] is True
    assert set(blocker) == {"code", "message", "resource_key", "resource_name", "retryable"}
    assert "sha256:" not in blocker["message"] and "injected" not in blocker["message"]
    with app.state.session_factory() as session:
        execution = session.scalar(select(ModelCommercialReleaseExecution).where(
            ModelCommercialReleaseExecution.plan_id.in_(select(ModelCommercialReleasePlan.id).where(
                ModelCommercialReleasePlan.model_id == model["id"]
            ))
        ))
        assert execution.state == "released"
    assert _counts(app, wallet_type, wallet_id) == before

    # Admission remains independently fail-closed, regardless of list reads.
    # The real transport rejects old envelopes itself; that is bypassed by this
    # typed evidence double, so exercise its established expiry test separately.
    if failure != "projection_expired":
        rejected = client.post(task_path, headers=headers, json=body)
        expected_status = 503 if failure in {"evidence_read", "catalog_read"} else 502 if failure == "snapshot_mismatch" else 409
        assert rejected.status_code == expected_status, rejected.text
        assert _counts(app, wallet_type, wallet_id) == before
    monkeypatch.undo()
    relay.evidence = good_evidence
    assert current_readiness()["ready"] is True


def test_schema_downgrade_requires_explicit_audited_reason(app, client):
    headers = _admin_headers(client, "distribution-schema-downgrade")
    v3 = _catalog_v3()["generation"]
    with app.state.session_factory.begin() as session:
        model, _ = ModelCatalogService.create_draft(
            session, slug="audit-v3-model", display_name="Audit v3", provider_key="relay",
            billing_mode="per_second", capabilities=[("generation", v3)],
        )
        model.relay_capability_approved_ceiling = v3
        model_id, version = model.id, model.capability_version
    downgraded = deepcopy(v3)
    downgraded["schema_version"] = 2
    for mode in downgraded["modes"].values():
        for key in ("input_roles", "temporal_controls", "structured_inputs"):
            mode.pop(key)
    body = {
        "display_name": "Audit renamed", "provider_key": "relay",
        "billing_mode": "per_second", "expected_capability_version": version,
        "capabilities": [{"key": "generation", "config": downgraded}],
    }
    url = f"/api/v1/platform-admin/models/{model_id}"
    rejected = client.put(url, headers=headers, json=body)
    assert rejected.status_code == 409, rejected.text
    assert "降级原因" in rejected.text
    with app.state.session_factory() as session:
        stored = ModelCatalogService.capabilities(session, model_id=model_id)["generation"]
        assert stored["schema_version"] == 3
        assert stored["modes"]["image_to_video"]["structured_inputs"] == ["director_shot_v1"]
    body["capability_schema_downgrade_reason"] = "明确移除全部结构化输入并兼容旧版工具"
    changed = client.put(url, headers=headers, json=body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["capabilities"]["generation"]["schema_version"] == 2
    with app.state.session_factory() as session:
        audit = session.scalar(select(AuditLog).where(
            AuditLog.action == "model.update", AuditLog.target_id == model_id
        ))
        assert audit.after_summary["capability_schema_downgrade_reason"] == body["capability_schema_downgrade_reason"]


def test_v3_explicit_capability_restriction_preserves_schema_without_downgrade_reason(app):
    v3 = _catalog_v3()["generation"]
    restricted = deepcopy(v3)
    for mode in restricted["modes"].values():
        mode["structured_inputs"] = []
        mode["temporal_controls"] = []
        mode["input_roles"] = ["reference_image"]
    with app.state.session_factory.begin() as session:
        model, _ = ModelCatalogService.create_draft(
            session, slug="audit-v3-restriction", display_name="Audit v3", provider_key="relay",
            billing_mode="per_second", capabilities=[("generation", v3)],
        )
        model.relay_capability_approved_ceiling = v3
        _, model, changed = ModelCatalogService.update_model(
            session, model_id=model.id, display_name=model.display_name, provider_key="relay",
            billing_mode="per_second", expected_capability_version=model.capability_version,
            capabilities=[("generation", restricted)],
        )
        assert changed is True
        saved = ModelCatalogService.capabilities(session, model_id=model.id)["generation"]
        assert saved["schema_version"] == 3
        assert saved["modes"]["image_to_video"]["structured_inputs"] == []


@pytest.mark.parametrize("changed_field", ["provider_account_id", "provider_credential_set_version", "route_binding_sha256"])
def test_commercial_approval_fences_reviewed_route_when_candidate_and_catalog_stay_equal(app, client, changed_field):
    headers = _admin_headers(client, f"distribution-reviewed-route-{changed_field}")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    body = _commercial_body(model=model)
    original_catalog = app.state.relay_client.catalog.model_dump()
    evidence = app.state.relay_client.evidence.model_dump()
    item = evidence["models"][0]
    item["routing_release_sha256"] = "sha256:" + "4" * 64
    item["routes"][0][changed_field] = (
        "sha256:" + "3" * 64 if changed_field == "route_binding_sha256"
        else "22222222-2222-4222-8222-222222222222"
    )
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)
    assert app.state.relay_client.catalog.model_dump() == original_catalog
    url = f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan"
    stale = client.put(url, headers=headers, json=body)
    assert stale.status_code == 409, stale.text
    assert "重新审阅账号和路由" in stale.text
    with app.state.session_factory() as session:
        assert session.scalar(select(ModelCommercialReleasePlan.id)) is None
        assert session.scalar(select(AuditLog.id).where(
            AuditLog.action == "model.commercial_release_plan.approve"
        )) is None
    # A refreshed review explicitly opts into that exact replacement route.
    body["expected_routing_release_sha256"] = item["routing_release_sha256"]
    approved = client.put(url, headers=headers, json=body)
    assert approved.status_code == 200, approved.text
    identity = approved.json()["approved_route_identity"]
    assert identity["routing_release_sha256"] == body["expected_routing_release_sha256"]
    assert identity["routes"][0][changed_field] == item["routes"][0][changed_field]
    with app.state.session_factory() as session:
        audit = session.scalar(select(AuditLog).where(
            AuditLog.action == "model.commercial_release_plan.approve"
        ))
        assert audit.after_summary["expected_routing_release_sha256"] == body["expected_routing_release_sha256"]
    # Routing can change after approval; an exact replay returns only its old
    # immutable receipt, while changing the fence cannot reuse its key.
    evidence["models"][0]["routing_release_sha256"] = "sha256:" + "5" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)
    replay = client.put(url, headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["content_sha256"] == approved.json()["content_sha256"]
    altered_intent = dict(body, expected_routing_release_sha256="sha256:" + "5" * 64)
    assert client.put(url, headers=headers, json=altered_intent).status_code == 409


@pytest.mark.parametrize("routing_fence", [None, "", "sha256:" + "d" * 63, "sha256:" + "D" * 64])
def test_commercial_approval_requires_valid_routing_fence(app, client, routing_fence):
    headers = _admin_headers(client, "distribution-route-fence-format")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    body = _commercial_body(model=model)
    if routing_fence is None:
        body.pop("expected_routing_release_sha256")
    else:
        body["expected_routing_release_sha256"] = routing_fence
    rejected = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers, json=body,
    )
    assert rejected.status_code == 422, rejected.text
    with app.state.session_factory() as session:
        assert session.scalar(select(ModelCommercialReleasePlan.id)) is None


def test_pre_route_fence_fingerprint_replay_requires_original_immutable_route(app, client):
    headers = _admin_headers(client, "distribution-historical-route-fingerprint")
    model = _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    body = _commercial_body(model=model)
    url = f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan"
    historical_intent = ModelCommercialReleasePlanRequest.model_validate(body).model_dump()
    historical_intent.pop("idempotency_key")
    historical_intent.pop("expected_routing_release_sha256")
    historical_intent.update(model_id=model["id"], approved_by_user_id=headers["X-Platform-Admin-User-ID"])
    # Reproduce an old writer at insertion, without modifying an immutable
    # persisted approval or weakening its ORM/database update protections.
    def store_historical_fingerprint(_mapper, _connection, plan):
        if plan.model_id == model["id"]:
            plan.request_fingerprint = _canonical_sha256(historical_intent)

    event.listen(ModelCommercialReleasePlan, "before_insert", store_historical_fingerprint)
    try:
        approved = client.put(url, headers=headers, json=body)
    finally:
        event.remove(ModelCommercialReleasePlan, "before_insert", store_historical_fingerprint)
    assert approved.status_code == 200, approved.text
    evidence = app.state.relay_client.evidence.model_dump()
    evidence["models"][0]["routing_release_sha256"] = "sha256:" + "5" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(evidence)
    replay = client.put(url, headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["content_sha256"] == approved.json()["content_sha256"]
    body["expected_routing_release_sha256"] = "sha256:" + "5" * 64
    assert client.put(url, headers=headers, json=body).status_code == 409
