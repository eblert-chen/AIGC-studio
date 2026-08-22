from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from platform_api.models import ModelDefinition, RelaySubmissionOutbox

from .test_generation_capability_v1 import (
    _provision_model,
    _recharge,
    _side_effect_snapshot,
)
from .test_model_capability_v1_contract import (
    _create_model,
    _mode,
    _publish,
    canonical_capability,
)


def _task_payload(*, metadata: dict | None = None) -> dict:
    payload = {
        "mode": "text_to_video",
        "prompt": "Adaptive capability security acceptance request",
        "assets": [],
        "duration_seconds": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "output_count": 1,
        "face_enabled": False,
    }
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


def _post_task(
    client,
    tenant,
    tenant_headers,
    *,
    model_id: str,
    suffix: str,
    request_payload: dict,
    expected_capability_version: int | None = None,
):
    body = {
        "model_id": model_id,
        "idempotency_key": f"adaptive-security-{suffix}",
        "request_payload": request_payload,
    }
    if expected_capability_version is not None:
        body["expected_capability_version"] = expected_capability_version
    return client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json=body,
    )


@pytest.mark.parametrize("unknown_field", ["provider_parameters", "seed"])
def test_unknown_request_payload_fields_fail_before_reserve_or_outbox(
    app, client, tenant, tenant_headers, unknown_field
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix=f"unknown-{unknown_field}",
        capability=capability,
    )
    _recharge(
        client,
        tenant,
        tenant_headers,
        suffix=f"unknown-{unknown_field}",
    )
    payload = _task_payload()
    payload[unknown_field] = {"provider_option": "must-not-pass"}
    before = _side_effect_snapshot(app, tenant["company_id"])

    response = _post_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix=f"unknown-{unknown_field}",
        request_payload=payload,
        expected_capability_version=1,
    )

    assert response.status_code == 409, response.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before


def test_client_metadata_is_nested_below_server_owned_relay_metadata(
    app, client, tenant, tenant_headers
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="metadata-boundary",
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix="metadata-boundary")
    client_metadata = {
        "campaign": "launch",
        "provider_parameters": {"temperature": 1},
        "platform_company_id": "attacker-company",
        "platform_task_id": "attacker-task",
        "_platform_input_assets": ["attacker-asset"],
    }

    response = _post_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="metadata-boundary",
        request_payload=_task_payload(metadata=client_metadata),
        expected_capability_version=1,
    )
    assert response.status_code == 201, response.text

    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == response.json()["id"]
            )
        )
        assert outbox is not None
        relay_metadata = outbox.relay_payload["metadata"]

    assert relay_metadata["client_metadata"] == client_metadata
    assert relay_metadata["platform_company_id"] == tenant["company_id"]
    assert relay_metadata["platform_task_id"] == response.json()["id"]
    assert relay_metadata["_platform_input_assets"] == []
    assert "provider_parameters" not in {
        key for key in relay_metadata if key != "client_metadata"
    }


def test_task_capability_version_guard_and_idempotent_replay_order(
    app, client, tenant, tenant_headers
):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                input_media_types=[],
                output_counts=[1],
            )
        }
    )
    model_id, admin_headers = _provision_model(
        client,
        tenant,
        suffix="version-guard",
        capability=capability,
    )
    _recharge(client, tenant, tenant_headers, suffix="version-guard")
    request_payload = _task_payload()

    original = _post_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="version-original",
        request_payload=request_payload,
        expected_capability_version=1,
    )
    assert original.status_code == 201, original.text
    assert original.json()["capability_snapshot"]["capability_version"] == 1

    disabled = client.post(
        f"/api/v1/platform-admin/models/{model_id}/disable",
        headers=admin_headers,
    )
    assert disabled.status_code == 200, disabled.text
    updated = client.put(
        f"/api/v1/platform-admin/models/{model_id}",
        headers=admin_headers,
        json={
            "display_name": "Capability version guard v2",
            "provider_key": "relay-capability-v1",
            "billing_mode": "per_item",
            "expected_capability_version": 1,
            "capabilities": [{"key": "generation", "config": capability}],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["capability_version"] == 2
    assert _publish(client, admin_headers, model_id).status_code == 200

    before_stale = _side_effect_snapshot(app, tenant["company_id"])
    stale = _post_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="version-stale",
        request_payload=request_payload,
        expected_capability_version=1,
    )
    assert stale.status_code == 409, stale.text
    assert _side_effect_snapshot(app, tenant["company_id"]) == before_stale

    current = _post_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="version-current",
        request_payload=request_payload,
        expected_capability_version=2,
    )
    assert current.status_code == 201, current.text
    assert current.json()["capability_snapshot"]["capability_version"] == 2

    replay = _post_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="version-original",
        request_payload=deepcopy(request_payload),
        expected_capability_version=2,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == original.json()["id"]
    assert replay.json()["capability_snapshot"]["capability_version"] == 1


@pytest.mark.parametrize(
    "empty_config",
    [
        {},
        {"text-to-video": {}},
    ],
    ids=["empty-generation", "empty-legacy-mode"],
)
def test_empty_generation_entries_are_not_saved_as_usable_capabilities(
    client, empty_config
):
    suffix = "empty-config" if not empty_config else "empty-legacy-mode"
    admin_headers = {
        "X-Platform-Admin-User-ID": client.post(
            "/api/v1/bootstrap/platform-admin",
            json={
                "email": f"adaptive-{suffix}@example.com",
                "display_name": f"Adaptive {suffix}",
            },
        ).json()["user_id"]
    }
    response = _create_model(
        client,
        admin_headers,
        suffix=suffix,
        capability=empty_config,
    )
    assert response.status_code == 409, response.text


def test_bootstrap_rejects_a_whitespace_only_capability_key_without_creating_model(
    app, client
):
    with app.state.session_factory() as session:
        before = session.scalar(select(func.count(ModelDefinition.id)))

    response = client.post(
        "/api/v1/bootstrap/models",
        json={
            "slug": "whitespace-capability-key",
            "display_name": "Whitespace capability key",
            "provider_key": "development",
            "billing_mode": "per_item",
            "capabilities": [
                {"key": "   ", "config": {"durations": [5]}}
            ],
        },
    )

    assert response.status_code == 409, response.text
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(ModelDefinition.id))) == before
