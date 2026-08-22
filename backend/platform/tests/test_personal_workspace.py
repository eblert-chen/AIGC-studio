from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import httpx
from sqlalchemy import func, select

from platform_api.models import (
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    PersonalDownloadRecord,
    PersonalLedgerEntry,
    PersonalRetailModelGrant,
    PersonalWalletAccount,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskTimeoutEvent,
    TaskStatus,
    User,
    ModelCapability,
    ModelDefinition,
    utcnow,
)
from platform_api.relay_client import (
    HttpxRelayClient,
    RelayArtifact,
    RelayPermanentError,
)
from platform_api.services.admin_analytics import AdminAnalyticsService
from platform_api.services.dashboard import DashboardService
from platform_api.services.relay_outbox import RelayOutboxDispatcher
from platform_api.services.relay_status import RelayStatusService
from platform_api.services.task_timeouts import TaskTimeoutService


RELAY_JOB_ID = "11111111-1111-4111-8111-111111111111"
ASSET_ID = "22222222-2222-4222-8222-222222222222"


def _personal_user(app, suffix: str) -> str:
    with app.state.session_factory.begin() as session:
        user = User(
            email=f"personal-{suffix}@example.com",
            display_name=f"Personal {suffix}",
        )
        session.add(user)
        session.flush()
        return user.id


def _retail_model(app) -> str:
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug="personal-video-v1",
            display_name="Personal Video",
            provider_key="test-provider",
            billing_mode="per_second",
            relay_capability_revision="sha256:" + ("1" * 64),
        )
        session.add(model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=model.id,
                capability_key="generation",
                config={
                    "schema_version": 1,
                    "modes": {
                        "text_to_video": {
                            "input_media_types": [],
                            "supports_face": False,
                            "required_resource_keys": [],
                            "limits": {
                                "max_prompt_length": 500,
                                "max_images": 0,
                                "max_videos": 0,
                                "max_audio": 0,
                                "duration_seconds": [5],
                                "aspect_ratios": ["16:9"],
                                "resolutions": ["720p"],
                                "output_counts": [1, 2],
                            },
                        }
                    },
                },
            )
        )
        session.add(
            PersonalRetailModelGrant(
                model_id=model.id,
                enabled=True,
                price_per_second_points=3,
                price_per_item_points=None,
                config_override={},
            )
        )
        return model.id


def _create_and_settle(
    app,
    client,
    headers: dict[str, str],
    model_id: str,
    *,
    bind_relay_job: bool = True,
    settle: bool = True,
) -> dict:
    workspace_id = client.get("/api/v1/personal/me", headers=headers).json()[
        "workspace_id"
    ]
    credited = client.post(
        f"/internal/personal/wallets/{workspace_id}/credit",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json={
            "amount_points": 100,
            "idempotency_key": "personal-provision-0001",
            "note": "payment-confirmed test provision",
        },
    )
    assert credited.status_code == 200, credited.text
    replay = client.post(
        f"/internal/personal/wallets/{workspace_id}/credit",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json={
            "amount_points": 100,
            "idempotency_key": "personal-provision-0001",
            "note": "payment-confirmed test provision",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False

    models = client.get("/api/v1/personal/models", headers=headers).json()
    assert models[0]["effective_capabilities"]["modes"]["text_to_video"][
        "limits"
    ]["output_counts"] == [1]
    created = client.post(
        "/api/v1/personal/tasks",
        headers=headers,
        json={
            "model_id": model_id,
            "expected_capability_version": models[0]["capability_version"],
            "expected_quote_revision": models[0]["quote_revision"],
            "idempotency_key": "personal-task-0001",
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "a quiet lake",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
            },
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["quote_points"] == 15
    assert "quote_cents" not in task
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        assert stored.company_id is None
        if bind_relay_job:
            stored.relay_job_id = RELAY_JOB_ID
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == stored.id
            )
        )
        assert outbox.company_id is None
        assert outbox.personal_workspace_id == workspace_id
        assert outbox.relay_payload["metadata"]["platform_billing_scope"] == "personal"
        assert "platform_company_id" not in outbox.relay_payload["metadata"]

    if not settle:
        return {**task, "workspace_id": workspace_id}

    assert bind_relay_job
    with app.state.session_factory.begin() as session:
        locked = RelayStatusService.lock_wallet_and_task_for_scope(
            session,
            company_id=None,
            personal_workspace_id=workspace_id,
            task_id=task["id"],
        )
        RelayStatusService.apply_to_locked_task(
            session,
            task=locked,
            company_id=None,
            personal_workspace_id=workspace_id,
            task_id=task["id"],
            relay_job_id=RELAY_JOB_ID,
            target_status=TaskStatus.SUCCEEDED,
            outputs=[
                RelayArtifact(
                    asset_id=ASSET_ID,
                    object_key=f"outputs/{RELAY_JOB_ID}/{ASSET_ID}",
                    media_type="video",
                    content_type="video/mp4",
                    size_bytes=1234,
                    sha256="a" * 64,
                )
            ],
        )
    return {**task, "workspace_id": workspace_id}


def _assert_personal_reservation_released_once(app, task: dict) -> None:
    with app.state.session_factory() as session:
        stored = session.get(GenerationTask, task["id"])
        wallet = session.get(PersonalWalletAccount, task["workspace_id"])
        releases = session.scalar(
            select(func.count(PersonalLedgerEntry.id)).where(
                PersonalLedgerEntry.task_id == task["id"],
                PersonalLedgerEntry.kind == LedgerKind.RELEASE,
            )
        )
        assert stored.status == TaskStatus.FAILED
        assert stored.reserved_points == 0
        assert wallet.available_points == 100
        assert wallet.reserved_points == 0
        assert releases == 1


def test_personal_workspace_reuses_relay_and_points_settlement_without_company(
    app, client
):
    user_id = _personal_user(app, "vertical")
    headers = {"X-User-ID": user_id}
    surfaces = client.get("/api/v1/session/surfaces", headers=headers)
    assert surfaces.status_code == 200, surfaces.text
    assert surfaces.json()["companies"] == []
    assert surfaces.json()["personal"]["capabilities"] == {
        "generation": True,
        "models": True,
        "tasks": True,
        "artworks": True,
        "task_cancel": False,
        "assets": False,
        "artifact_access": True,
        "publishing": False,
    }
    task = _create_and_settle(app, client, headers, _retail_model(app))
    wallet = client.get("/api/v1/personal/wallet", headers=headers).json()
    assert wallet["available_points"] == 85
    assert wallet["reserved_points"] == 0
    artworks = client.get("/api/v1/personal/artworks", headers=headers).json()
    assert artworks["total"] == 1
    assert artworks["items"][0]["asset_id"] == ASSET_ID
    assert artworks["items"][0]["download_evidence_available"] is True
    assert artworks["items"][0]["download_status"] == "not_downloaded"
    assert artworks["items"][0]["download_issue_count"] == 0
    assert artworks["items"][0]["download_completed_count"] == 0
    assert artworks["items"][0]["downloaded"] is False
    assert artworks["items"][0]["last_download_issued_at"] is None
    assert artworks["items"][0]["last_download_completed_at"] is None
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(PersonalLedgerEntry.id))) == 3
        assert session.scalar(select(func.count(LedgerEntry.id))) == 0
        assert session.get(PersonalWalletAccount, task["workspace_id"]) is not None
        dashboard = DashboardService.build(session, page=1, page_size=100)
        assert dashboard["total_task_count"] == 0
        now = datetime.now(timezone.utc)
        operating = AdminAnalyticsService.operating_series(
            session,
            start=now - timedelta(days=1),
            end=now + timedelta(days=1),
            granularity="day",
        )
        assert operating["totals"]["settled_revenue_cents"] == 0
        assert operating["totals"]["cost_missing_task_count"] == 0
        operations = AdminAnalyticsService.task_operations(
            session, start=now - timedelta(days=1), end=now + timedelta(days=1)
        )
        assert operations["total_task_count"] == 0


def test_personal_artifact_access_is_owner_scoped_bound_and_audited(app, client):
    owner_id = _personal_user(app, "artifact-owner")
    owner_headers = {"X-User-ID": owner_id}
    task = _create_and_settle(app, client, owner_headers, _retail_model(app))
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    bucket = "relay-output-private"
    endpoint = "obs.cn-north-4.myhuaweicloud.com"
    object_key = f"outputs/{RELAY_JOB_ID}/{ASSET_ID}"
    url = (
        f"https://{endpoint}/{bucket}/{object_key}"
        "?AccessKeyId=masked&Expires=300&Signature=masked"
    )

    def relay_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": url,
                "expires_seconds": 300,
                "storage_binding": {
                    "provider": "huawei_obs",
                    "endpoint_host": endpoint,
                    "bucket": bucket,
                    "object_key": object_key,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(seconds=300)).isoformat(),
                    "url_sha256": hashlib.sha256(url.encode()).hexdigest(),
                },
            },
        )

    app.state.relay_client = HttpxRelayClient(
        base_url="https://relay.example.test",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(relay_handler),
    )
    base = f"/api/v1/personal/tasks/{task['id']}/artifacts/{ASSET_ID}"
    preview = client.get(base + "/preview", headers=owner_headers)
    assert preview.status_code == 200, preview.text
    download = client.get(base + "/download", headers=owner_headers)
    assert download.status_code == 200, download.text
    assert download.json()["download_status"] == "issued"
    refreshed_artworks = client.get(
        "/api/v1/personal/artworks", headers=owner_headers
    )
    assert refreshed_artworks.status_code == 200, refreshed_artworks.text
    evidence = refreshed_artworks.json()["items"][0]
    assert evidence["download_evidence_available"] is True
    assert evidence["download_status"] == "issued"
    assert evidence["download_issue_count"] == 1
    assert evidence["download_completed_count"] == 0
    assert evidence["downloaded"] is False
    assert evidence["last_download_issued_at"] is not None
    assert evidence["last_download_completed_at"] is None
    other_id = _personal_user(app, "artifact-other")
    assert client.get(base + "/preview", headers={"X-User-ID": other_id}).status_code == 404
    assert client.get(base + "/download", headers={"X-User-ID": other_id}).status_code == 404
    with app.state.session_factory() as session:
        record = session.get(
            PersonalDownloadRecord, download.json()["download_record_id"]
        )
        assert record.workspace_id == task["workspace_id"]
        assert record.source_url_sha256 == hashlib.sha256(url.encode()).hexdigest()
        assert not hasattr(record, "url")


def test_personal_permanent_relay_rejection_releases_points_once(app, client):
    user_id = _personal_user(app, "permanent-rejection")
    task = _create_and_settle(
        app,
        client,
        {"X-User-ID": user_id},
        _retail_model(app),
        bind_relay_job=False,
        settle=False,
    )

    class PermanentlyRejectingRelay:
        def submit(self, *_args, **_kwargs):
            raise RelayPermanentError("request rejected before provider creation")

    result = RelayOutboxDispatcher(
        app.state.session_factory,
        PermanentlyRejectingRelay(),
    ).dispatch_once()

    assert result.status == RelayOutboxStatus.PERMANENTLY_FAILED.value
    _assert_personal_reservation_released_once(app, task)


def test_personal_relay_failure_terminal_releases_points_once(app, client):
    user_id = _personal_user(app, "terminal-failure")
    task = _create_and_settle(
        app,
        client,
        {"X-User-ID": user_id},
        _retail_model(app),
        settle=False,
    )

    with app.state.session_factory.begin() as session:
        locked = RelayStatusService.lock_wallet_and_task_for_scope(
            session,
            company_id=None,
            personal_workspace_id=task["workspace_id"],
            task_id=task["id"],
        )
        RelayStatusService.apply_to_locked_task(
            session,
            task=locked,
            company_id=None,
            personal_workspace_id=task["workspace_id"],
            task_id=task["id"],
            relay_job_id=RELAY_JOB_ID,
            target_status=TaskStatus.FAILED,
            failure_reason="provider generation failed",
            reservation_action="release",
        )

    _assert_personal_reservation_released_once(app, task)


def test_personal_undispatched_timeout_releases_points_once(app, client):
    user_id = _personal_user(app, "undispatched-timeout")
    task = _create_and_settle(
        app,
        client,
        {"X-User-ID": user_id},
        _retail_model(app),
        bind_relay_job=False,
        settle=False,
    )
    with app.state.session_factory.begin() as session:
        stored = session.get(GenerationTask, task["id"])
        stored.created_at = utcnow() - timedelta(hours=8)

    first = TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()
    repeated = TaskTimeoutService(
        app.state.session_factory,
        relay_client=None,
        queued_timeout_seconds=60,
        processing_timeout_seconds=60,
    ).scan_once()

    assert first.scanned == 1
    assert first.compensated == 1
    assert first.items[0].outcome == "timeout_released"
    assert first.items[0].released_cents == 0
    assert first.items[0].released_points == 15
    assert repeated.scanned == 0
    _assert_personal_reservation_released_once(app, task)
    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        event = session.scalar(
            select(TaskTimeoutEvent).where(TaskTimeoutEvent.task_id == task["id"])
        )
        assert outbox.status == RelayOutboxStatus.PERMANENTLY_FAILED
        assert event.released_cents == 0
        assert event.released_points == 15
        assert event.ledger_entry_id is None
        assert event.personal_ledger_entry_id is not None
