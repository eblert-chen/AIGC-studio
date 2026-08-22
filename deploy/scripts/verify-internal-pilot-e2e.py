from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, "/app")


PLATFORM = "http://127.0.0.1:8000"
RELAY = os.environ["RELAY_BASE_URL"].rstrip("/")


def request_json(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read()
    except HTTPError as exc:
        safe_body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(
            f"{method} {url} failed with HTTP {exc.code}: {safe_body}"
        ) from exc
    return json.loads(payload) if payload else {}


def wait_for(url: str, headers: dict[str, str], predicate, label: str) -> dict:
    deadline = time.monotonic() + 180
    last: dict = {}
    while time.monotonic() < deadline:
        last = request_json("GET", url, headers=headers)
        if predicate(last):
            return last
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {label}; last status={last.get('status')}")


def main() -> None:
    run_id = uuid4().hex[:12]
    bootstrap_headers = {"X-Bootstrap-Token": os.environ["BOOTSTRAP_TOKEN"]}

    from platform_api.models import User

    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        admin_id = session.scalar(
            select(User.id).where(User.email == "pilot-admin@example.com")
        )
    engine.dispose()
    if admin_id is None:
        admin = request_json(
            "POST",
            f"{PLATFORM}/api/v1/bootstrap/platform-admin",
            headers=bootstrap_headers,
            body={
                "email": "pilot-admin@example.com",
                "display_name": "Internal Pilot Admin",
            },
        )
        admin_id = admin["user_id"]
    admin_headers = {"X-Platform-Admin-User-ID": admin_id}
    tenant = request_json(
        "POST",
        f"{PLATFORM}/api/v1/bootstrap",
        headers=bootstrap_headers,
        body={
            "company_name": f"Internal Pilot {run_id}",
            "owner_email": f"pilot-owner-{run_id}@example.com",
            "owner_display_name": "Internal Pilot Owner",
        },
    )
    company_id = tenant["company_id"]
    user_id = tenant["user_id"]
    tenant_headers = {"X-Company-ID": company_id, "X-User-ID": user_id}

    model = request_json(
        "POST",
        f"{PLATFORM}/api/v1/bootstrap/models",
        headers=bootstrap_headers,
        body={
            "slug": "mock.video.v1",
            "display_name": "Internal Pilot Mock Video",
            "provider_key": "mock-video",
            "billing_mode": "per_second",
            "capability_version": 1,
            "capabilities": [
                {
                    "key": "text-to-video",
                    "config": {
                        "durations": [5, 10],
                        "ratios": ["16:9", "9:16", "1:1"],
                        "resolutions": ["720p", "1080p"],
                        "output_counts": [1],
                        "max_prompt_length": 10000,
                    },
                }
            ],
        },
    )
    model_detail = request_json(
        "GET",
        f"{PLATFORM}/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
    )
    approved = request_json(
        "POST",
        f"{PLATFORM}/api/v1/platform-admin/models/{model['id']}/relay-capability",
        headers=admin_headers,
        body={"expected_capability_version": model_detail["capability_version"]},
    )
    if approved["model"]["status"] == "draft":
        request_json(
            "POST",
            f"{PLATFORM}/api/v1/platform-admin/models/{model['id']}/publish",
            headers=admin_headers,
        )
    request_json(
        "PUT",
        f"{PLATFORM}/api/v1/platform-admin/companies/{company_id}/model-grants",
        headers=admin_headers,
        body={
            "model_id": model["id"],
            "enabled": True,
            "price_per_second_cents": 2,
            "config_override": {},
        },
    )
    request_json(
        "POST",
        f"{PLATFORM}/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        body={
            "amount_cents": 100,
            "idempotency_key": f"pilot-recharge-{run_id}",
            "note": "internal deployment verification",
        },
    )
    task = request_json(
        "POST",
        f"{PLATFORM}/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        body={
            "model_id": model["id"],
            "expected_capability_version": approved["model"]["capability_version"],
            "idempotency_key": f"pilot-task-{run_id}",
            "request_payload": {
                "prompt": "Internal deployment verification frame",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
            },
        },
    )
    task_url = f"{PLATFORM}/api/v1/companies/{company_id}/tasks/{task['id']}"
    dispatched = wait_for(
        task_url,
        tenant_headers,
        lambda item: bool(item.get("relay_job_id")),
        "Platform dispatch",
    )
    relay_job_id = dispatched["relay_job_id"]
    relay_headers = {
        "X-Client-ID": os.environ["RELAY_CLIENT_ID"],
        "X-API-Key": os.environ["RELAY_API_KEY"],
    }
    wait_for(
        f"{RELAY}/v1/generations/{relay_job_id}",
        relay_headers,
        lambda item: item.get("status") in {"processing", "submitting"},
        "Relay provider submission",
    )

    # A tiny MP4-shaped payload is enough for the transfer/integrity path; the
    # mock provider never claims that it is a playable production video.
    source_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + run_id.encode()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    from platform_api.asset_storage import HuaweiObsInputAssetStore
    from platform_api.config import get_settings

    source_store = HuaweiObsInputAssetStore.from_settings(get_settings())
    source_key = f"inputs/{company_id}/{uuid4()}"
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "mock-result.mp4"
        source_path.write_bytes(source_bytes)
        source_store.put_file(
            source_key,
            source_path,
            content_type="video/mp4",
            size_bytes=len(source_bytes),
            sha256=source_sha,
        )
    source_url = source_store.signed_url(
        source_key,
        expires_seconds=900,
        original_filename="mock-result.mp4",
        disposition="inline",
    )
    request_json(
        "POST",
        f"{RELAY}/v1/providers/mock-video/webhooks",
        headers={"X-Mock-Webhook-Secret": "development-only-secret"},
        body={
            "event_id": f"pilot-event-{run_id}",
            "provider_task_id": f"mock-{relay_job_id}",
            "status": "succeeded",
            "progress": 100,
            "outputs": [
                {
                    "url": source_url,
                    "media_type": "video",
                    "content_type": "video/mp4",
                }
            ],
        },
    )
    completed = wait_for(
        task_url,
        tenant_headers,
        lambda item: item.get("status") in {"succeeded", "failed", "cancelled"},
        "artifact transfer, callback, and settlement",
    )
    if completed["status"] != "succeeded":
        raise RuntimeError(
            f"Pilot task ended as {completed['status']}: {completed.get('failure_reason')}"
        )
    if completed.get("actual_cost_cents") != 10:
        raise RuntimeError("Pilot task did not settle the expected 10 cents")
    artifacts = completed.get("output_artifacts") or []
    if len(artifacts) != 1 or artifacts[0].get("sha256") != source_sha:
        raise RuntimeError("Pilot artifact metadata did not reach Platform intact")

    print(
        "INTERNAL_PILOT_E2E_PASS "
        f"company_id={company_id} task_id={task['id']} relay_job_id={relay_job_id} "
        f"status={completed['status']} settled_cents={completed['actual_cost_cents']} "
        f"artifact_sha256={source_sha}"
    )


if __name__ == "__main__":
    main()
