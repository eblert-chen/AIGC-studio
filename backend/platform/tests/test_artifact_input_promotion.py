from __future__ import annotations

import hashlib

import httpx
from sqlalchemy import func, select

from platform_api.artifact_copy import HttpArtifactContentSource
from platform_api.models import (
    AuditLog,
    GenerationTask,
    InputAsset,
    RelaySubmissionOutbox,
    TaskArtifact,
    TaskStatus,
)
from platform_api.models import WalletAccount
from platform_api.relay_client import RelaySignedDownload

from .conftest import bootstrap
from .test_relay_boundary import recharge_and_create


def _create_another_task(app, client, tenant, tenant_headers, *, id_suffix: str):
    with app.state.session_factory() as session:
        model_id = session.scalar(
            select(GenerationTask.model_id).where(
                GenerationTask.company_id == tenant["company_id"]
            )
        )
        assert model_id is not None
    recharge = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": f"promotion-recharge-{id_suffix}",
        },
    )
    assert recharge.status_code == 200
    created = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"promotion-task-{id_suffix}",
            "request_payload": {
                "prompt": "产物转素材幂等冲突测试",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


RELAY_JOB_ID = "99999999-9999-4999-8999-999999999998"
RELAY_ASSET_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab"
CONTENT = b"\x00\x00\x00\x18ftypmp42generated-video"


class _PromotionRelayClient:
    def __init__(self, download: RelaySignedDownload) -> None:
        self.download = download
        self.calls: list[tuple[str, str, str | None]] = []

    def get_artifact_download(
        self,
        relay_job_id: str,
        asset_id: str,
        *,
        request_id: str | None = None,
    ) -> RelaySignedDownload:
        self.calls.append((relay_job_id, asset_id, request_id))
        return self.download


def _make_succeeded_artifact(app, task_id: str) -> TaskArtifact:
    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        assert task is not None
        task.status = TaskStatus.SUCCEEDED
        task.actual_cost_cents = task.quote_cents
        task.reserved_cents = 0
        task.relay_job_id = task.id
        task.output_artifacts = [
            {
                "asset_id": RELAY_ASSET_ID,
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": len(CONTENT),
                "sha256": hashlib.sha256(CONTENT).hexdigest(),
            }
        ]
        artifact = TaskArtifact(
            company_id=task.company_id,
            task_id=task.id,
            asset_id=RELAY_ASSET_ID,
            position=0,
            media_type="video",
            content_type="video/mp4",
            size_bytes=len(CONTENT),
            sha256=hashlib.sha256(CONTENT).hexdigest(),
        )
        session.add(artifact)
        session.flush()
        session.expunge(artifact)
        return artifact


def _configure_artifact_source(app, monkeypatch, *, content: bytes = CONTENT):
    download = RelaySignedDownload.model_validate(
        {
            "api_version": "v1",
            "schema_version": 1,
            "url": "http://127.0.0.1:8100/private-artifact",
            "expires_seconds": 300,
        }
    )
    relay = _PromotionRelayClient(download)
    app.state.relay_client = relay

    def source_factory(url: str, **_):
        assert url == "http://127.0.0.1:8100/private-artifact"
        return HttpArtifactContentSource(
            url,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Length": str(len(content))},
                    content=content,
                    request=request,
                )
            ),
        )

    monkeypatch.setattr(
        "platform_api.main.HttpArtifactContentSource",
        source_factory,
    )
    return relay


def test_promotes_durable_artifact_idempotently_without_billing_or_relay_outbox(
    app, client, tenant, tenant_headers, monkeypatch
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="promote-artifact"
    )
    artifact = _make_succeeded_artifact(app, task["id"])
    relay = _configure_artifact_source(app, monkeypatch)
    with app.state.session_factory() as session:
        wallet_before = session.get(WalletAccount, tenant["company_id"])
        assert wallet_before is not None
        wallet_snapshot = (
            wallet_before.available_cents,
            wallet_before.reserved_cents,
        )
    path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{RELAY_ASSET_ID}/input-asset"
    )
    body = {"idempotency_key": "promote-artifact-0001"}
    first = client.post(
        path,
        headers={**tenant_headers, "X-Request-ID": "promote-request-001"},
        json=body,
    )
    assert first.status_code == 201, first.text
    promoted = first.json()
    assert promoted["source_task_artifact_id"] == artifact.id
    assert promoted["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert promoted["size_bytes"] == len(CONTENT)
    assert "object_key" not in promoted
    assert "url" not in promoted

    second = client.post(path, headers=tenant_headers, json=body)
    assert second.status_code == 201, second.text
    assert second.json()["id"] == promoted["id"]
    assert len(relay.calls) == 1

    detail = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}",
        headers=tenant_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["output_artifacts"][0]["artifact_id"] == artifact.id
    assert detail.json()["output_artifacts"][0]["asset_id"] == RELAY_ASSET_ID

    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(InputAsset.id))) == 1
        assert session.scalar(select(func.count(RelaySubmissionOutbox.id))) == 1
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "task.artifact.promote_to_input_asset"
            )
        )
        assert audit is not None
        assert audit.target_id == promoted["id"]
        assert audit.before_summary["task_artifact_id"] == artifact.id
        wallet_after = session.get(WalletAccount, tenant["company_id"])
        assert wallet_after is not None
        assert (
            wallet_after.available_cents,
            wallet_after.reserved_cents,
        ) == wallet_snapshot


def test_promotion_enforces_scope_both_permissions_and_idempotency_source(
    app, client, tenant, tenant_headers, monkeypatch
):
    owner_task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="promotion-owner"
    )
    owner_artifact = _make_succeeded_artifact(app, owner_task["id"])
    second_task = _create_another_task(
        app, client, tenant, tenant_headers, id_suffix="promotion-second"
    )
    _make_succeeded_artifact(app, second_task["id"])
    relay = _configure_artifact_source(app, monkeypatch)

    member = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={
            "email": "promotion-member@example.com",
            "display_name": "Promotion Member",
        },
    ).json()
    member_headers = {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": member["user_id"],
    }
    owner_path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{owner_task['id']}"
        f"/artifacts/{RELAY_ASSET_ID}/input-asset"
    )
    assert client.post(
        owner_path,
        headers=member_headers,
        json={"idempotency_key": "member-own-scope-01"},
    ).status_code == 404
    assert client.post(
        owner_path,
        headers=member_headers,
        params={"scope": "company"},
        json={"idempotency_key": "member-company-scope-01"},
    ).status_code == 403

    deny_manage = client.put(
        (
            f"/api/v1/companies/{tenant['company_id']}/members/"
            f"{member['membership_id']}/permission"
        ),
        headers=tenant_headers,
        json={"permission_code": "assets.manage", "effect": "deny"},
    )
    assert deny_manage.status_code == 200
    assert client.post(
        owner_path,
        headers=member_headers,
        json={"idempotency_key": "member-no-assets-01"},
    ).status_code == 403

    body = {"idempotency_key": "owner-promotion-conflict"}
    first = client.post(owner_path, headers=tenant_headers, json=body)
    assert first.status_code == 201, first.text
    second_path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{second_task['id']}"
        f"/artifacts/{RELAY_ASSET_ID}/input-asset"
    )
    conflict = client.post(second_path, headers=tenant_headers, json=body)
    assert conflict.status_code == 409
    assert len(relay.calls) == 1
    with app.state.session_factory() as session:
        promoted = session.scalar(
            select(InputAsset).where(InputAsset.id == first.json()["id"])
        )
        assert promoted is not None
        assert promoted.source_task_artifact_id == owner_artifact.id


def test_promotion_rejects_nonterminal_unknown_and_integrity_mismatch(
    app, client, tenant, tenant_headers, monkeypatch
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="promotion-integrity"
    )
    path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{RELAY_ASSET_ID}/input-asset"
    )
    assert client.post(
        path,
        headers=tenant_headers,
        json={"idempotency_key": "promotion-before-success"},
    ).status_code == 404

    _make_succeeded_artifact(app, task["id"])
    _configure_artifact_source(app, monkeypatch, content=CONTENT + b"tampered")
    mismatch = client.post(
        path,
        headers=tenant_headers,
        json={"idempotency_key": "promotion-bad-integrity"},
    )
    assert mismatch.status_code == 502
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(InputAsset.id))) == 0
