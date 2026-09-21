from __future__ import annotations

from copy import deepcopy
import pytest
from sqlalchemy import func, select, update

from platform_api.models import (
    AuditLog,
    CompanyModelGrant,
    ModelDefinition,
    PersonalModelGrantBatchJournal,
    PersonalRetailModelGrant,
)
from platform_api.services.admin_entitlements import AdminEntitlementService
from platform_api.services.models import ModelCatalogService, ModelGrantService
from platform_api.services.personal import PersonalRetailGrantService

from .conftest import bootstrap
from .test_model_capability_v1_contract import (
    _admin_headers,
    _create_model,
    _grant,
    _mode,
    _publish,
    canonical_capability,
)
from .test_relay_capability_sync import (
    CatalogRelayClient,
    _approve_candidate,
    _catalog,
    _sync_candidate,
)


DRIFTED_REVISION = "sha256:" + ("e" * 64)


class ForbiddenReleaseEvidenceClient:
    def get_model_release_evidence(self, **_):
        raise AssertionError("a completed replay must not read Relay evidence")


def _approved_model(app, client, headers, *, suffix: str) -> dict:
    capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[1],
                resolutions=["1024x1024"],
                output_counts=[1],
                input_media_types=[],
            )
        }
    )
    created = _create_model(
        client,
        headers,
        suffix=suffix,
        capability=capability,
        billing_mode="per_item",
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], capability)
    )
    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Capture the release-guard candidate",
    )
    assert synced.status_code == 200, synced.text
    approved = _approve_candidate(
        client,
        headers,
        model,
        reason="Approve the release-guard candidate",
    )
    assert approved.status_code == 200, approved.text
    return approved.json()["model"]


def _inject_unsynchronized_candidate_drift(session, *, model_id: str) -> None:
    """Change the row without updating the Session identity-map instance."""

    session.execute(
        update(ModelDefinition)
        .where(ModelDefinition.id == model_id)
        .values(relay_capability_candidate_revision=DRIFTED_REVISION),
        execution_options={"synchronize_session": False},
    )


def _audit_count(session, *, action: str, target_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == action,
                AuditLog.target_id == target_id,
            )
        )
        or 0
    )


def _action_count(session, *, action: str) -> int:
    return int(
        session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == action)
        )
        or 0
    )


def test_publish_refreshes_the_locked_row_and_rejects_release_drift(
    app, client, monkeypatch
) -> None:
    headers = _admin_headers(client, "release-guard-publish")
    model = _approved_model(
        app, client, headers, suffix="release-guard-publish"
    )
    original = ModelCatalogService.publish

    def racing_publish(cls, session, **kwargs):
        _inject_unsynchronized_candidate_drift(
            session, model_id=kwargs["model_id"]
        )
        return original(session, **kwargs)

    monkeypatch.setattr(
        ModelCatalogService,
        "publish",
        classmethod(racing_publish),
    )
    rejected = _publish(client, headers, model["id"])
    assert rejected.status_code == 409, rejected.text
    assert "已变化" in rejected.text

    with app.state.session_factory() as session:
        stored = session.get(ModelDefinition, model["id"])
        assert stored is not None
        assert stored.active is False
        assert _audit_count(
            session,
            action="model.publish",
            target_id=model["id"],
        ) == 0


def test_single_company_and_personal_grants_reject_locked_release_drift(
    app, client, tenant, monkeypatch
) -> None:
    headers = _admin_headers(client, "release-guard-single-grants")
    model = _approved_model(
        app, client, headers, suffix="release-guard-single-grants"
    )
    published = _publish(client, headers, model["id"])
    assert published.status_code == 200, published.text

    original_company_upsert = ModelGrantService.upsert_grant

    def racing_company_upsert(session, **kwargs):
        _inject_unsynchronized_candidate_drift(
            session, model_id=kwargs["model_id"]
        )
        return original_company_upsert(session, **kwargs)

    monkeypatch.setattr(
        ModelGrantService,
        "upsert_grant",
        staticmethod(racing_company_upsert),
    )
    rejected_company = _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    )
    assert rejected_company.status_code == 409, rejected_company.text
    assert "已变化" in rejected_company.text

    original_personal_upsert = PersonalRetailGrantService.upsert

    def racing_personal_upsert(cls, session, **kwargs):
        _inject_unsynchronized_candidate_drift(
            session, model_id=kwargs["model_id"]
        )
        return original_personal_upsert(session, **kwargs)

    monkeypatch.setattr(
        PersonalRetailGrantService,
        "upsert",
        classmethod(racing_personal_upsert),
    )
    rejected_personal = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": None,
            "enabled": True,
            "price_per_item_points": 9,
            "price_per_second_points": None,
            "config_override": {},
            "reason": "Exercise the personal locked release guard",
        },
    )
    assert rejected_personal.status_code == 409, rejected_personal.text
    assert "已变化" in rejected_personal.text

    with app.state.session_factory() as session:
        assert session.scalar(
            select(CompanyModelGrant.id).where(
                CompanyModelGrant.company_id == tenant["company_id"],
                CompanyModelGrant.model_id == model["id"],
            )
        ) is None
        assert session.scalar(
            select(PersonalRetailModelGrant.id).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        ) is None
        assert _action_count(
            session, action="company.model_grant.upsert"
        ) == 0
        assert _audit_count(
            session,
            action="personal_model_grant.upsert",
            target_id=model["id"],
        ) == 0


def test_personal_batch_passes_and_enforces_the_release_snapshot(
    app, client, monkeypatch
) -> None:
    headers = _admin_headers(client, "release-guard-personal-batch")
    model = _approved_model(
        app, client, headers, suffix="release-guard-personal-batch"
    )
    published = _publish(client, headers, model["id"])
    assert published.status_code == 200, published.text
    changes = [
        {
            "model_id": model["id"],
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": None,
            "enabled": True,
            "price_per_item_points": 9,
            "price_per_second_points": None,
            "config_override": {},
        }
    ]
    preview = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/preview",
        headers=headers,
        json={"changes": changes},
    )
    assert preview.status_code == 200, preview.text
    original = PersonalRetailGrantService.execute_batch

    def racing_execute(cls, session, **kwargs):
        snapshots = kwargs["expected_release_snapshots"]
        assert model["id"] in snapshots
        assert snapshots[model["id"]]["route_evidence_identity"][
            "routing_release_sha256"
        ]
        _inject_unsynchronized_candidate_drift(session, model_id=model["id"])
        return original(session, **kwargs)

    monkeypatch.setattr(
        PersonalRetailGrantService,
        "execute_batch",
        classmethod(racing_execute),
    )
    rejected = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
        headers=headers,
        json={
            "changes": changes,
            "expected_snapshot": preview.json()["snapshot"],
            "reason": "Exercise the personal batch release guard",
            "idempotency_key": "release-guard-personal-batch-001",
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "已变化" in rejected.text

    with app.state.session_factory() as session:
        assert session.scalar(
            select(PersonalModelGrantBatchJournal.id).where(
                PersonalModelGrantBatchJournal.idempotency_key
                == "release-guard-personal-batch-001"
            )
        ) is None
        assert session.scalar(
            select(PersonalRetailModelGrant.id).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        ) is None


def test_company_entitlement_batch_passes_and_enforces_the_release_snapshot(
    app, client, tenant, monkeypatch
) -> None:
    headers = _admin_headers(client, "release-guard-company-batch")
    model = _approved_model(
        app, client, headers, suffix="release-guard-company-batch"
    )
    published = _publish(client, headers, model["id"])
    assert published.status_code == 200, published.text
    changes = [
        {
            "company_id": tenant["company_id"],
            "item_kind": "model",
            "item_id": model["id"],
            "enabled": True,
            "price_per_item_cents": 100,
            "config_override": {},
        }
    ]
    preview = client.post(
        "/api/v1/platform-admin/entitlements/batch/preview",
        headers=headers,
        json={"changes": changes},
    )
    assert preview.status_code == 200, preview.text
    original = AdminEntitlementService.execute_changes

    def racing_execute(cls, session, **kwargs):
        snapshots = kwargs["expected_release_snapshots"]
        assert model["id"] in snapshots
        assert snapshots[model["id"]]["route_evidence_identity"][
            "model_release_revision"
        ]
        _inject_unsynchronized_candidate_drift(session, model_id=model["id"])
        return original(session, **kwargs)

    monkeypatch.setattr(
        AdminEntitlementService,
        "execute_changes",
        classmethod(racing_execute),
    )
    rejected = client.post(
        "/api/v1/platform-admin/entitlements/batch/execute",
        headers=headers,
        json={
            "changes": changes,
            "expected_snapshot": preview.json()["snapshot"],
            "reason": "Exercise the company batch release guard",
            "idempotency_key": "release-guard-company-batch-001",
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert "已变化" in rejected.text

    with app.state.session_factory() as session:
        assert session.scalar(
            select(CompanyModelGrant.id).where(
                CompanyModelGrant.company_id == tenant["company_id"],
                CompanyModelGrant.model_id == model["id"],
            )
        ) is None
        assert _audit_count(
            session,
            action="company.entitlements.batch",
            target_id="release-guard-company-batch-001",
        ) == 0


@pytest.mark.parametrize("flow", ["batch", "copy", "template"])
def test_entitlement_execute_replays_before_live_relay_evidence_and_binds_payload(
    flow, app, client, tenant, monkeypatch
) -> None:
    headers = _admin_headers(client, f"release-replay-{flow}")
    model = _approved_model(
        app, client, headers, suffix=f"release-replay-{flow}"
    )
    published = _publish(client, headers, model["id"])
    assert published.status_code == 200, published.text

    source_company_id = None
    if flow == "batch":
        route = "/api/v1/platform-admin/entitlements/batch"
        preview_body = {
            "changes": [
                {
                    "company_id": tenant["company_id"],
                    "item_kind": "model",
                    "item_id": model["id"],
                    "enabled": True,
                    "price_per_item_cents": 100,
                    "config_override": {},
                }
            ]
        }
    elif flow == "copy":
        source = bootstrap(client, "release-replay-copy-source")
        source_company_id = source["company_id"]
        source_grant = _grant(
            client,
            headers,
            company_id=source["company_id"],
            model_id=model["id"],
            price_per_item_cents=100,
        )
        assert source_grant.status_code == 200, source_grant.text
        route = "/api/v1/platform-admin/entitlements/copy"
        preview_body = {
            "source_company_id": source["company_id"],
            "target_company_ids": [tenant["company_id"]],
            "mode": "merge",
            "include_models": True,
            "include_resources": False,
        }
    else:
        route = "/api/v1/platform-admin/entitlements/templates"
        preview_body = {
            "template_name": "Reviewed model release",
            "template_version": 1,
            "target_company_ids": [tenant["company_id"]],
            "mode": "merge",
            "cells": [
                {
                    "item_kind": "model",
                    "item_id": model["id"],
                    "enabled": True,
                    "price_per_item_cents": 100,
                    "config_override": {},
                }
            ],
        }

    preview = client.post(
        f"{route}/preview", headers=headers, json=preview_body
    )
    assert preview.status_code == 200, preview.text
    idempotency_key = f"release-replay-{flow}-001"
    operation = {
        **preview_body,
        "expected_snapshot": preview.json()["snapshot"],
        "reason": f"Apply the reviewed {flow} entitlement release",
        "idempotency_key": idempotency_key,
    }
    executed = client.post(
        f"{route}/execute", headers=headers, json=operation
    )
    assert executed.status_code == 200, executed.text
    first = executed.json()
    assert first["idempotent_replay"] is False

    with app.state.session_factory() as session:
        audit_count = _audit_count(
            session,
            action="company.entitlements.batch",
            target_id=idempotency_key,
        )
        grant_count = int(
            session.scalar(
                select(func.count(CompanyModelGrant.id)).where(
                    CompanyModelGrant.model_id == model["id"]
                )
            )
            or 0
        )
    assert audit_count == 1

    original_relay_client = app.state.relay_client
    with app.state.session_factory.begin() as session:
        if flow == "copy":
            source_grant = session.scalar(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.company_id == source_company_id,
                    CompanyModelGrant.model_id == model["id"],
                )
            )
            assert source_grant is not None
            source_grant.enabled = False
            source_grant.price_per_item_cents = 777
        elif flow == "template":
            stored_model = session.get(ModelDefinition, model["id"])
            assert stored_model is not None
            stored_model.active = False

    if flow == "copy":
        def forbid_copy_derivation(cls, *args, **kwargs):
            raise AssertionError(
                "a completed copy replay must not read mutable source grants"
            )

        monkeypatch.setattr(
            AdminEntitlementService,
            "changes_from_company",
            classmethod(forbid_copy_derivation),
        )
    elif flow == "template":
        def forbid_template_derivation(cls, *args, **kwargs):
            raise AssertionError(
                "a completed template replay must not rederive catalog cells"
            )

        monkeypatch.setattr(
            AdminEntitlementService,
            "changes_from_template",
            classmethod(forbid_template_derivation),
        )

    app.state.relay_client = ForbiddenReleaseEvidenceClient()
    replay_unavailable = client.post(
        f"{route}/execute", headers=headers, json=operation
    )
    assert replay_unavailable.status_code == 200, replay_unavailable.text
    assert replay_unavailable.json() == {**first, "idempotent_replay": True}

    app.state.relay_client = CatalogRelayClient(
        original_relay_client.catalog,
        evidence_revision=DRIFTED_REVISION,
    )
    replay_drifted = client.post(
        f"{route}/execute", headers=headers, json=operation
    )
    assert replay_drifted.status_code == 200, replay_drifted.text
    assert replay_drifted.json() == {**first, "idempotent_replay": True}

    conflicting_operation = deepcopy(operation)
    if flow == "batch":
        conflicting_operation["changes"][0]["price_per_item_cents"] = 101
    elif flow == "copy":
        conflicting_operation["include_resources"] = True
    else:
        conflicting_operation["template_version"] = 2
    conflicting = client.post(
        f"{route}/execute",
        headers=headers,
        json=conflicting_operation,
    )
    assert conflicting.status_code == 409, conflicting.text
    assert "idempotency_key" in conflicting.text

    with app.state.session_factory() as session:
        assert _audit_count(
            session,
            action="company.entitlements.batch",
            target_id=idempotency_key,
        ) == audit_count
        assert int(
            session.scalar(
                select(func.count(CompanyModelGrant.id)).where(
                    CompanyModelGrant.model_id == model["id"]
                )
            )
            or 0
        ) == grant_count
