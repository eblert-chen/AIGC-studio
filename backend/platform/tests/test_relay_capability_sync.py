from __future__ import annotations

from copy import deepcopy

from sqlalchemy import select

from platform_api.models import RelaySubmissionOutbox
from platform_api.relay_client import RelayModelCatalog, RelayModelCatalogRead

from .test_model_capability_v1_contract import (
    _admin_headers,
    _create_model,
    _grant,
    _mode,
    _publish,
    canonical_capability,
)


REVISION = "sha256:" + ("a" * 64)
CATALOG_REVISION = "sha256:" + ("b" * 64)


def _recharge(client, tenant, tenant_headers, *, suffix: str) -> None:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 10_000,
            "idempotency_key": f"relay-sync-recharge-{suffix}",
            "note": "relay capability sync test",
        },
    )
    assert response.status_code == 200, response.text


class CatalogRelayClient:
    def __init__(self, catalog: RelayModelCatalog):
        self.catalog = catalog

    def get_model_catalog(self, **_) -> RelayModelCatalogRead:
        return RelayModelCatalogRead(
            catalog=self.catalog,
            etag=f'"{self.catalog.catalog_revision}"',
            not_modified=False,
        )


def _catalog(model_id: str, capability: dict) -> RelayModelCatalog:
    return RelayModelCatalog.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "object": "list",
            "catalog_revision": CATALOG_REVISION,
            "data": [
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "id": model_id,
                    "object": "model",
                    "capability_revision": REVISION,
                    "capabilities": capability,
                }
            ],
        }
    )


def test_relay_catalog_audit_approves_a_platform_restriction_and_stamps_tasks(
    app, client, tenant, tenant_headers
) -> None:
    headers = _admin_headers(client, "relay-sync")
    platform_capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=4,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1],
            )
        }
    )
    relay_capability = canonical_capability(
        modes={
            "image_to_video": _mode(
                max_images=9,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1, 2],
            ),
            "text_to_video": _mode(
                max_images=9,
                max_videos=0,
                max_audio=0,
                input_media_types=["image"],
                output_counts=[1, 2],
            ),
        }
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-sync",
        capability=platform_capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], relay_capability)
    )

    audit = client.get(
        "/api/v1/platform-admin/relay-models", headers=headers
    )
    assert audit.status_code == 200, audit.text
    row = audit.json()["items"][0]
    assert row["status"] == "compatible_restriction"
    assert row["approved_revision"] is None
    assert audit.headers["Cache-Control"] == "private, no-store"
    blocked_publish = _publish(client, headers, model["id"])
    assert blocked_publish.status_code == 409
    assert "中转站模型能力版本" in blocked_publish.text

    approved = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/relay-capability",
        headers=headers,
        json={"expected_capability_version": 1},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["compatibility"] == "compatible_restriction"
    assert approved.json()["capability_revision"] == REVISION
    assert approved.json()["model"]["relay_capability_revision"] == REVISION
    assert approved.json()["model"]["capability_version"] == 1

    assert _publish(client, headers, model["id"]).status_code == 200
    assert _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    ).status_code == 200
    _recharge(client, tenant, tenant_headers, suffix="relay-sync")
    created_task = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model["id"],
            "idempotency_key": "relay-revision-task",
            "expected_capability_version": 1,
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "Version-pinned generation",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
                "metadata": {},
            },
        },
    )
    assert created_task.status_code == 201, created_task.text
    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == created_task.json()["id"]
            )
        )
        assert outbox is not None
        assert outbox.relay_payload["expected_capability_revision"] == REVISION


def test_relay_sync_rejects_platform_capability_expansion(app, client) -> None:
    headers = _admin_headers(client, "relay-unsafe")
    platform_capability = canonical_capability(
        modes={"text_to_video": _mode(max_images=9)}
    )
    relay_capability = deepcopy(platform_capability)
    relay_capability["modes"]["text_to_video"]["limits"]["max_images"] = 4
    created = _create_model(
        client,
        headers,
        suffix="relay-unsafe",
        capability=platform_capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], relay_capability)
    )

    audit = client.get(
        "/api/v1/platform-admin/relay-models", headers=headers
    )
    assert audit.status_code == 200
    assert audit.json()["items"][0]["status"] == "unsafe_expansion"
    rejected = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/relay-capability",
        headers=headers,
        json={"expected_capability_version": 1},
    )
    assert rejected.status_code == 409
    detail = client.get(
        f"/api/v1/platform-admin/models/{model['id']}", headers=headers
    )
    assert detail.json()["relay_capability_revision"] is None


def test_configured_relay_rejects_tasks_for_an_unpinned_legacy_model(
    app, client, tenant, tenant_headers
) -> None:
    headers = _admin_headers(client, "relay-unpinned")
    capability = canonical_capability(
        modes={"text_to_video": _mode(max_images=1, output_counts=[1])}
    )
    created = _create_model(
        client,
        headers,
        suffix="relay-unpinned",
        capability=capability,
    )
    assert created.status_code == 201, created.text
    model = created.json()
    # Simulate a model published before the Relay capability-sync migration.
    assert _publish(client, headers, model["id"]).status_code == 200
    assert _grant(
        client,
        headers,
        company_id=tenant["company_id"],
        model_id=model["id"],
        price_per_item_cents=100,
    ).status_code == 200
    _recharge(client, tenant, tenant_headers, suffix="relay-unpinned")
    app.state.relay_client = CatalogRelayClient(
        _catalog(model["slug"], capability)
    )

    rejected = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model["id"],
            "idempotency_key": "relay-unpinned-task",
            "expected_capability_version": 1,
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "Must pin Relay capabilities first",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
                "metadata": {},
            },
        },
    )
    assert rejected.status_code == 409
    assert "尚未确认中转站能力版本" in rejected.text
