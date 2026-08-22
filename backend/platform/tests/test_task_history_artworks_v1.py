from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text

from platform_api.download_gateway import DownloadGatewayClient
from platform_api.models import (
    DownloadCompletion,
    DownloadCompletionSource,
    DownloadRecord,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    TaskArtifact,
    TaskStatus,
)
from platform_api.relay_client import HttpxRelayClient
from platform_api.services.download_completion_events import (
    DownloadCompletionEventVerifier,
)

from .conftest import (
    TEST_EDGE_DOWNLOAD_SIGNING_SECRET,
    TEST_EDGE_COMPLETION_SERVICE_TOKEN,
    TEST_OBS_DOWNLOAD_SIGNING_SECRET,
    bootstrap,
)
from .test_generation_capability_v1 import _provision_model, _recharge
from .test_model_capability_v1_contract import _mode, canonical_capability


def _post_signed_download_completion(
    client,
    payload: dict,
    *,
    source: str = "edge_gateway",
    event_id: str | None = None,
    timestamp: int | None = None,
    secret: str | None = None,
):
    raw_body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    event_id = event_id or str(uuid4())
    timestamp = int(time.time()) if timestamp is None else timestamp
    secret = secret or (
        TEST_EDGE_DOWNLOAD_SIGNING_SECRET
        if source == "edge_gateway"
        else TEST_OBS_DOWNLOAD_SIGNING_SECRET
    )
    signing_input = (
        b"download-completion.v1\n"
        + source.encode("ascii")
        + b"\n"
        + str(timestamp).encode("ascii")
        + b"\n"
        + event_id.encode("ascii")
        + b"\n"
        + raw_body
    )
    signature = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/internal/artifact-download-completions/" + source.replace("_", "-"),
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Service-Token": (
                TEST_EDGE_COMPLETION_SERVICE_TOKEN
                if source == "edge_gateway"
                else "test-internal-token"
            ),
            "X-Download-Event-ID": event_id,
            "X-Download-Timestamp": str(timestamp),
            "X-Download-Signature": f"v1={signature}",
        },
    )


def _add_member(client, tenant, tenant_headers, suffix: str) -> dict:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={
            "email": f"history-{suffix}@example.com",
            "display_name": f"History {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _member_headers(tenant, member) -> dict[str, str]:
    return {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": member["user_id"],
    }


def _create_task(
    client,
    tenant,
    headers,
    *,
    model_id: str,
    suffix: str,
    mode: str,
) -> dict:
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"history-artwork-{suffix}",
            "request_payload": {
                "mode": mode,
                "prompt": f"history and artwork {suffix}",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _finish_task(
    app,
    client,
    internal_headers,
    tenant,
    task: dict,
    *,
    media_type: str,
    asset_size: int,
) -> tuple[dict, dict]:
    relay_job_id = str(uuid4())
    asset_id = str(uuid4())
    content_type = "image/png" if media_type == "image" else "video/mp4"
    artifact = {
        "asset_id": asset_id,
        "object_key": (
            f"outputs/{tenant['company_id']}/{relay_job_id}/{asset_id}"
        ),
        "media_type": media_type,
        "content_type": content_type,
        "size_bytes": asset_size,
        "sha256": ("a" if media_type == "image" else "b") * 64,
    }
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, task["id"]).relay_job_id = relay_job_id
    response = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": task["id"],
            "relay_job_id": relay_job_id,
            "status": "succeeded",
            "outputs": [artifact],
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), artifact


def _fixture_tasks(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
) -> dict:
    capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                input_media_types=[],
                output_counts=[1],
            ),
            "text_to_video": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                input_media_types=[],
                output_counts=[1],
            ),
        }
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="history-artworks-v1",
        capability=capability,
        unit_price_cents=25,
    )
    _recharge(
        client,
        tenant,
        tenant_headers,
        suffix="history-artworks-v1",
    )
    member = _add_member(client, tenant, tenant_headers, "operator")
    member_headers = _member_headers(tenant, member)

    owner_video = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="owner-video",
        mode="text_to_video",
    )
    owner_image = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="owner-image",
        mode="text_to_image",
    )
    member_video = _create_task(
        client,
        tenant,
        member_headers,
        model_id=model_id,
        suffix="member-video",
        mode="text_to_video",
    )
    failed = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="owner-failed",
        mode="text_to_video",
    )

    owner_video, owner_video_artifact = _finish_task(
        app,
        client,
        internal_headers,
        tenant,
        owner_video,
        media_type="video",
        asset_size=1_200,
    )
    owner_image, owner_image_artifact = _finish_task(
        app,
        client,
        internal_headers,
        tenant,
        owner_image,
        media_type="image",
        asset_size=640,
    )
    member_video, member_video_artifact = _finish_task(
        app,
        client,
        internal_headers,
        tenant,
        member_video,
        media_type="video",
        asset_size=2_400,
    )
    failed_relay_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, failed["id"]).relay_job_id = failed_relay_id
    failed_response = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "task_id": failed["id"],
            "relay_job_id": failed_relay_id,
            "status": "failed",
            "failure_reason": "provider rejected request",
        },
    )
    assert failed_response.status_code == 200, failed_response.text

    return {
        "model_id": model_id,
        "member": member,
        "member_headers": member_headers,
        "owner_video": owner_video,
        "owner_video_artifact": owner_video_artifact,
        "owner_image": owner_image,
        "owner_image_artifact": owner_image_artifact,
        "member_video": member_video,
        "member_video_artifact": member_video_artifact,
        "failed": failed_response.json(),
    }


def test_task_records_are_complete_paginated_and_scoped_to_the_employee(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
) -> None:
    data = _fixture_tasks(
        app, client, tenant, tenant_headers, internal_headers
    )
    company_id = tenant["company_id"]
    member_headers = data["member_headers"]

    owner_default = client.get(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
    )
    assert owner_default.status_code == 200, owner_default.text
    assert {item["user_id"] for item in owner_default.json()} == {
        tenant["user_id"]
    }
    member_default = client.get(
        f"/api/v1/companies/{company_id}/tasks",
        headers=member_headers,
    )
    assert member_default.status_code == 200, member_default.text
    assert [item["id"] for item in member_default.json()] == [
        data["member_video"]["id"]
    ]
    assert client.get(
        f"/api/v1/companies/{company_id}/tasks",
        headers=member_headers,
        params={"scope": "company"},
    ).status_code == 403

    company_tasks = client.get(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        params={"scope": "company"},
    )
    assert company_tasks.status_code == 200, company_tasks.text
    assert {item["id"] for item in company_tasks.json()} == {
        data["owner_video"]["id"],
        data["owner_image"]["id"],
        data["member_video"]["id"],
        data["failed"]["id"],
    }

    assert client.get(
        f"/api/v1/companies/{company_id}/tasks/{data['member_video']['id']}",
        headers=tenant_headers,
    ).status_code == 404
    detail = client.get(
        f"/api/v1/companies/{company_id}/tasks/{data['member_video']['id']}",
        headers=tenant_headers,
        params={"scope": "company"},
    )
    assert detail.status_code == 200, detail.text
    task = detail.json()
    required_fields = {
        "id",
        "company_id",
        "user_id",
        "model_id",
        "status",
        "request_payload",
        "quote_cents",
        "pricing_snapshot",
        "capability_snapshot",
        "reserved_cents",
        "actual_cost_cents",
        "relay_job_id",
        "output_artifacts",
        "failure_reason",
        "created_at",
        "updated_at",
    }
    assert required_fields <= task.keys()
    assert task["company_id"] == company_id
    assert task["user_id"] == data["member"]["user_id"]
    assert task["model_id"] == data["model_id"]
    assert task["request_payload"]["prompt"] == "history and artwork member-video"
    assert task["status"] == "succeeded"
    assert task["actual_cost_cents"] == 25
    assert len(task["output_artifacts"]) == 1
    assert task["output_artifacts"][0]["artifact_id"]
    assert {
        key: value
        for key, value in task["output_artifacts"][0].items()
        if key != "artifact_id"
    } == {
        key: value
        for key, value in data["member_video_artifact"].items()
        if key != "object_key"
    }
    assert client.get(
        f"/api/v1/companies/{company_id}/tasks/{data['owner_video']['id']}",
        headers=member_headers,
    ).status_code == 404

    history_url = f"/api/v1/companies/{company_id}/task-history"
    member_history = client.get(history_url, headers=member_headers)
    assert member_history.status_code == 200, member_history.text
    assert member_history.json()["total"] == 1
    assert member_history.json()["items"][0]["user_id"] == data["member"][
        "user_id"
    ]
    assert client.get(
        history_url,
        headers=member_headers,
        params={"scope": "company"},
    ).status_code == 403
    assert client.get(
        history_url,
        headers=member_headers,
        params={"employee_user_id": tenant["user_id"]},
    ).status_code == 403

    first_page = client.get(
        history_url,
        headers=tenant_headers,
        params={"scope": "company", "page": 1, "page_size": 2},
    )
    second_page = client.get(
        history_url,
        headers=tenant_headers,
        params={"scope": "company", "page": 2, "page_size": 2},
    )
    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == second_page.json()["total"] == 4
    first_ids = {item["id"] for item in first_page.json()["items"]}
    second_ids = {item["id"] for item in second_page.json()["items"]}
    assert len(first_ids) == len(second_ids) == 2
    assert first_ids.isdisjoint(second_ids)

    succeeded = client.get(
        history_url,
        headers=tenant_headers,
        params={
            "scope": "company",
            "status": "succeeded",
            "model_id": data["model_id"],
            "start_time": "2020-01-01T00:00:00Z",
            "end_time": "2100-01-01T00:00:00Z",
        },
    )
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["total"] == 3
    assert all(item["artifact_count"] == 1 for item in succeeded.json()["items"])
    assert all(item["downloaded"] is False for item in succeeded.json()["items"])

    image_only = client.get(
        history_url,
        headers=tenant_headers,
        params={"scope": "company", "media_type": "image"},
    )
    assert image_only.status_code == 200, image_only.text
    assert image_only.json()["total"] == 1
    assert all(
        item["request_payload"]["mode"] == "text_to_image"
        for item in image_only.json()["items"]
    )

    prompt_search = client.get(
        history_url,
        headers=tenant_headers,
        params={"scope": "company", "query": "member-video"},
    )
    assert prompt_search.status_code == 200, prompt_search.text
    assert prompt_search.json()["total"] == 1
    assert prompt_search.json()["items"][0]["id"] == data["member_video"]["id"]

    other = bootstrap(client, "history-cross-tenant")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    assert client.get(
        f"/api/v1/companies/{other['company_id']}/tasks/{data['owner_video']['id']}",
        headers=other_headers,
    ).status_code == 404
    assert client.get(
        history_url,
        headers=other_headers,
    ).status_code == 403


def test_artworks_include_only_indexed_success_outputs_with_filters(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
) -> None:
    data = _fixture_tasks(
        app, client, tenant, tenant_headers, internal_headers
    )
    company_id = tenant["company_id"]
    artworks_url = f"/api/v1/companies/{company_id}/artworks"

    owner = client.get(artworks_url, headers=tenant_headers)
    assert owner.status_code == 200, owner.text
    assert owner.json()["total"] == 2
    assert {item["media_type"] for item in owner.json()["items"]} == {
        "image",
        "video",
    }
    assert data["failed"]["id"] not in {
        item["task_id"] for item in owner.json()["items"]
    }

    member = client.get(artworks_url, headers=data["member_headers"])
    assert member.status_code == 200, member.text
    assert member.json()["total"] == 1
    assert member.json()["items"][0]["task_id"] == data["member_video"]["id"]
    assert client.get(
        artworks_url,
        headers=data["member_headers"],
        params={"scope": "company"},
    ).status_code == 403

    company = client.get(
        artworks_url,
        headers=tenant_headers,
        params={"scope": "company"},
    )
    assert company.status_code == 200, company.text
    assert company.json()["total"] == 3
    required_fields = {
        "artifact_id",
        "task_id",
        "company_id",
        "asset_id",
        "output_index",
        "media_type",
        "content_type",
        "size_bytes",
        "sha256",
        "created_by_user_id",
        "created_by_display_name",
        "created_by_email",
        "model_id",
        "model_display_name",
        "request_payload",
        "actual_cost_cents",
        "download_issue_count",
        "download_completed_count",
        "downloaded",
        "last_download_issued_at",
        "last_download_completed_at",
        "created_at",
    }
    assert all(required_fields <= item.keys() for item in company.json()["items"])
    assert all(item["actual_cost_cents"] == 25 for item in company.json()["items"])

    image = client.get(
        artworks_url,
        headers=tenant_headers,
        params={"scope": "company", "media_type": "image"},
    )
    assert image.status_code == 200, image.text
    assert image.json()["total"] == 1
    assert image.json()["items"][0]["content_type"] == "image/png"
    member_filter = client.get(
        artworks_url,
        headers=tenant_headers,
        params={
            "scope": "company",
            "employee_user_id": data["member"]["user_id"],
            "model_id": data["model_id"],
            "downloaded": False,
            "start_time": "2020-01-01T00:00:00Z",
            "end_time": "2100-01-01T00:00:00Z",
        },
    )
    assert member_filter.status_code == 200, member_filter.text
    assert member_filter.json()["total"] == 1

    page_one = client.get(
        artworks_url,
        headers=tenant_headers,
        params={"scope": "company", "page": 1, "page_size": 2},
    ).json()
    page_two = client.get(
        artworks_url,
        headers=tenant_headers,
        params={"scope": "company", "page": 2, "page_size": 2},
    ).json()
    assert page_one["total"] == page_two["total"] == 3
    assert len(page_one["items"]) == 2
    assert len(page_two["items"]) == 1
    assert {
        item["artifact_id"] for item in page_one["items"]
    }.isdisjoint({item["artifact_id"] for item in page_two["items"]})

    with app.state.session_factory.begin() as session:
        incomplete = session.get(GenerationTask, data["failed"]["id"])
        incomplete.status = TaskStatus.SUCCEEDED
        incomplete.actual_cost_cents = incomplete.quote_cents
        incomplete.reserved_cents = 0
        incomplete.output_artifacts = [
            {
                "asset_id": str(uuid4()),
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 12,
                "sha256": "c" * 64,
            }
        ]
    still_complete_only = client.get(
        artworks_url,
        headers=tenant_headers,
        params={"scope": "company"},
    )
    assert still_complete_only.status_code == 200, still_complete_only.text
    assert still_complete_only.json()["total"] == 3


def test_signed_url_is_only_issued_until_a_trusted_completion_is_recorded(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
    edge_completion_headers,
) -> None:
    data = _fixture_tasks(
        app, client, tenant, tenant_headers, internal_headers
    )
    company_id = tenant["company_id"]
    task = data["member_video"]
    artifact = data["member_video_artifact"]
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    endpoint_host = "obs.cn-north-4.myhuaweicloud.com"
    bucket = "history-output-private"
    object_key = f"tasks/{task['id']}/{artifact['asset_id']}.mp4"
    source_url = (
        f"https://{endpoint_host}/{bucket}/{object_key}"
        "?AccessKeyId=masked&Expires=300&Signature=masked"
    )

    def relay_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": source_url,
                "expires_seconds": 600,
                "storage_binding": {
                    "provider": "huawei_obs",
                    "endpoint_host": endpoint_host,
                    "bucket": bucket,
                    "object_key": object_key,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": (
                        issued_at + timedelta(seconds=600)
                    ).isoformat(),
                    "url_sha256": hashlib.sha256(
                        source_url.encode("utf-8")
                    ).hexdigest(),
                },
            },
        )

    app.state.relay_client = HttpxRelayClient(
        base_url="http://relay.internal",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(relay_handler),
        allow_local_http=True,
    )
    gateway_registration: dict[str, object] = {}

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        gateway_registration.update(payload)
        ticket_id = str(uuid4())
        ticket_token = base64.urlsafe_b64encode(
            hashlib.sha256(ticket_id.encode("utf-8")).digest()
        ).rstrip(b"=").decode("ascii")
        ticket_url = f"https://downloads.example.com/downloads/{ticket_token}"
        return httpx.Response(
            201,
            headers={"Location": ticket_url},
            json={
                "api_version": "v1",
                "schema_version": 1,
                "download_record_id": payload["download_record_id"],
                "company_id": payload["company_id"],
                "task_id": payload["task_id"],
                "asset_id": payload["asset_id"],
                "issuance_request_id": payload["issuance_request_id"],
                "transfer_reference": payload["transfer_reference"],
                "gateway_ticket_id": ticket_id,
                "one_time": True,
                "ticket_url": ticket_url,
                "issued_at": issued_at.isoformat(),
                "expires_at": (
                    issued_at + timedelta(seconds=120)
                ).isoformat(),
                "expires_seconds": 120,
            },
        )

    app.state.download_gateway_client = DownloadGatewayClient(
        registration_url=(
            "https://download-gateway.example.com/internal/v1/download-tickets"
        ),
        public_base_url="https://downloads.example.com",
        service_token="history-gateway-service-token",
        signing_secret="history-gateway-registration-signing-secret",
        transport=httpx.MockTransport(gateway_handler),
        clock=lambda: issued_at.timestamp(),
    )
    download_url = (
        f"/api/v1/companies/{company_id}/tasks/{task['id']}"
        f"/artifacts/{artifact['asset_id']}/download"
    )
    issued = client.get(
        download_url,
        headers={
            **data["member_headers"],
            "X-Request-ID": "edge-request-member-video-0001",
        },
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["download_status"] == "issued"
    record_id = issued.json()["download_record_id"]

    records_url = f"/api/v1/companies/{company_id}/download-records"
    member_records = client.get(records_url, headers=data["member_headers"])
    assert member_records.status_code == 200, member_records.text
    assert member_records.json()["total"] == 1
    record = member_records.json()["items"][0]
    assert record["id"] == record_id
    assert record["status"] == "issued"
    assert record["downloaded"] is False
    assert record["completed_at"] is None
    assert record["bytes_sent"] is None
    assert record["completion_source"] is None

    owner_mine = client.get(records_url, headers=tenant_headers)
    assert owner_mine.status_code == 200
    assert owner_mine.json()["total"] == 0
    owner_company = client.get(
        records_url,
        headers=tenant_headers,
        params={"scope": "company", "employee_user_id": data["member"]["user_id"]},
    )
    assert owner_company.status_code == 200, owner_company.text
    assert owner_company.json()["total"] == 1
    assert client.get(
        records_url,
        headers=data["member_headers"],
        params={"scope": "company"},
    ).status_code == 403

    completion = {
        "download_record_id": record_id,
        "company_id": company_id,
        "task_id": task["id"],
        "asset_id": artifact["asset_id"],
        "external_event_id": "edge-transfer-event-member-video-0001",
        "bytes_sent": artifact["size_bytes"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": artifact["sha256"],
        "expected_size_bytes": artifact["size_bytes"],
        "http_status": 200,
        "transfer_scope": "full_body",
        "gateway_request_id": "edge-request-member-video-0001",
        "gateway_transfer_reference": gateway_registration[
            "transfer_reference"
        ],
    }
    completion_url = "/internal/artifact-download-completions/edge-gateway"
    assert client.post(
        completion_url,
        headers=edge_completion_headers,
        json=completion,
    ).status_code == 401
    assert client.post(
        completion_url,
        headers={
            **edge_completion_headers,
            "Content-Type": "application/json",
        },
        content=b"x" * (64 * 1024 + 1),
    ).status_code == 413
    assert client.post(
        "/internal/artifact-download-completions",
        headers=internal_headers,
        json=completion,
    ).status_code == 404
    wrong_size = _post_signed_download_completion(
        client,
        {
            **completion,
            "external_event_id": "edge-transfer-wrong-size-0001",
            "bytes_sent": 1,
            "expected_size_bytes": 1,
        },
    )
    assert wrong_size.status_code == 409, wrong_size.text
    assert _post_signed_download_completion(
        client,
        {**completion, "completed_at": "2030-01-02T03:04:05"},
    ).status_code == 422

    event_id = str(uuid4())
    event_timestamp = int(time.time())
    verifier_clock = [float(event_timestamp)]
    app.state.download_completion_event_verifier = (
        DownloadCompletionEventVerifier(
            edge_gateway_signing_secret=TEST_EDGE_DOWNLOAD_SIGNING_SECRET,
            obs_access_log_signing_secret=TEST_OBS_DOWNLOAD_SIGNING_SECRET,
            clock=lambda: verifier_clock[0],
        )
    )
    confirmed = _post_signed_download_completion(
        client,
        completion,
        event_id=event_id,
        timestamp=event_timestamp,
    )
    replayed = _post_signed_download_completion(
        client,
        completion,
        event_id=event_id,
        timestamp=event_timestamp,
    )
    assert confirmed.status_code == replayed.status_code == 201
    assert replayed.json()["id"] == confirmed.json()["id"]
    assert confirmed.json()["verification_version"] == 1
    assert confirmed.json()["signed_event_id"] == event_id
    assert confirmed.json()["artifact_sha256"] == artifact["sha256"]
    first_signed_timestamp = confirmed.json()["signed_event_timestamp"]
    verifier_clock[0] += 301
    expired_replay = _post_signed_download_completion(
        client,
        completion,
        event_id=event_id,
        timestamp=event_timestamp,
    )
    assert expired_replay.status_code == 401, expired_replay.text
    refreshed_replay = _post_signed_download_completion(
        client,
        completion,
        event_id=event_id,
        timestamp=int(verifier_clock[0]),
    )
    assert refreshed_replay.status_code == 201, refreshed_replay.text
    assert refreshed_replay.json()["id"] == confirmed.json()["id"]
    assert (
        refreshed_replay.json()["signed_event_timestamp"]
        == first_signed_timestamp
    )
    changed_body_same_event = _post_signed_download_completion(
        client,
        {
            **completion,
            "gateway_transfer_reference": "edge-transfer-conflict",
        },
        event_id=event_id,
        timestamp=int(verifier_clock[0]),
    )
    assert changed_body_same_event.status_code == 409
    second_event = _post_signed_download_completion(
        client,
        {
            **completion,
            "external_event_id": "edge-transfer-event-member-video-0002",
        },
        timestamp=int(verifier_clock[0]),
    )
    assert second_event.status_code == 409, second_event.text

    completed_records = client.get(
        records_url,
        headers=data["member_headers"],
        params={"page": 1, "page_size": 1, "task_id": task["id"]},
    )
    assert completed_records.status_code == 200, completed_records.text
    completed = completed_records.json()["items"][0]
    assert completed["status"] == "completed"
    assert completed["downloaded"] is True
    assert completed["bytes_sent"] == artifact["size_bytes"]
    assert completed["completion_source"] == "edge_gateway"

    downloaded_artworks = client.get(
        f"/api/v1/companies/{company_id}/artworks",
        headers=data["member_headers"],
        params={"downloaded": True},
    )
    assert downloaded_artworks.status_code == 200, downloaded_artworks.text
    assert downloaded_artworks.json()["total"] == 1
    assert downloaded_artworks.json()["items"][0]["downloaded"] is True
    assert downloaded_artworks.json()["items"][0]["download_issue_count"] == 1
    assert downloaded_artworks.json()["items"][0]["download_completed_count"] == 1

    historical_issuance = client.get(
        download_url,
        headers=data["member_headers"],
    )
    assert historical_issuance.status_code == 200
    historical_record_id = historical_issuance.json()["download_record_id"]
    with app.state.session_factory.begin() as session:
        now = datetime.now(timezone.utc)
        session.execute(
            text(
                "INSERT INTO download_completions "
                "(id, download_record_id, external_event_id, source, "
                "bytes_sent, completed_at, created_at) VALUES "
                "(:id, :download_record_id, :external_event_id, "
                ":source, :bytes_sent, :completed_at, :created_at)"
            ),
            {
                "id": str(uuid4()),
                "download_record_id": historical_record_id,
                "external_event_id": "historical-unsigned-download-event",
                "source": DownloadCompletionSource.OBS_ACCESS_LOG.name,
                "bytes_sent": artifact["size_bytes"],
                "completed_at": now,
                "created_at": now,
            },
        )
    historical_records = client.get(
        records_url,
        headers=data["member_headers"],
    )
    historical_row = next(
        item
        for item in historical_records.json()["items"]
        if item["id"] == historical_record_id
    )
    assert historical_row["downloaded"] is False
    assert historical_row["status"] == "issued"
    trusted_only_artwork = client.get(
        f"/api/v1/companies/{company_id}/artworks",
        headers=data["member_headers"],
        params={"downloaded": True},
    ).json()["items"][0]
    assert trusted_only_artwork["download_issue_count"] == 2
    assert trusted_only_artwork["download_completed_count"] == 1

    other = bootstrap(client, "download-cross-tenant-v1")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    assert client.get(
        (
            f"/api/v1/companies/{other['company_id']}/tasks/{task['id']}"
            f"/artifacts/{artifact['asset_id']}/download"
        ),
        headers=other_headers,
    ).status_code == 404

    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(DownloadRecord.id)).where(
                DownloadRecord.id == record_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(DownloadCompletion.id)).where(
                DownloadCompletion.download_record_id == record_id
            )
        ) == 1


def test_repeated_success_keeps_one_artifact_index_and_one_settlement(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
) -> None:
    capability = canonical_capability(
        modes={"text_to_video": _mode(output_counts=[1])}
    )
    model_id, _ = _provision_model(
        client,
        tenant,
        suffix="artifact-terminal-replay",
        capability=capability,
        unit_price_cents=37,
    )
    _recharge(
        client,
        tenant,
        tenant_headers,
        suffix="artifact-terminal-replay",
    )
    task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="terminal-replay",
        mode="text_to_video",
    )
    relay_job_id = str(uuid4())
    asset_id = str(uuid4())
    with app.state.session_factory.begin() as session:
        session.get(GenerationTask, task["id"]).relay_job_id = relay_job_id
    body = {
        "company_id": tenant["company_id"],
        "task_id": task["id"],
        "relay_job_id": relay_job_id,
        "status": "succeeded",
        "outputs": [
            {
                "asset_id": asset_id,
                "object_key": (
                    f"outputs/{tenant['company_id']}/{relay_job_id}/{asset_id}"
                ),
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 777,
                "sha256": "d" * 64,
            }
        ],
    }
    first = client.post(
        "/internal/relay/status", headers=internal_headers, json=body
    )
    replay = client.post(
        "/internal/relay/status", headers=internal_headers, json=body
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json()["output_artifacts"] == first.json()["output_artifacts"]

    changed = client.post(
        "/internal/relay/status",
        headers=internal_headers,
        json={
            **body,
            "outputs": [{**body["outputs"][0], "sha256": "e" * 64}],
        },
    )
    assert changed.status_code == 409, changed.text
    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(TaskArtifact.id)).where(
                TaskArtifact.task_id == task["id"]
            )
        ) == 1
        assert session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == task["id"],
                LedgerEntry.kind == LedgerKind.SETTLE,
            )
        ) == 1
