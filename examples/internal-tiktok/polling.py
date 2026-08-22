"""Reference internal-tiktok client for Relay Generation API v1.

This example intentionally uses a direct service credential and never calls the
customer platform. It demonstrates capability ETag revalidation, revision
pinning, stable idempotency, uncertainty-safe POST retries, bounded polling, and
verified artifact download.

Required environment variables:
    RELAY_BASE_URL
    INTERNAL_TIKTOK_RELAY_API_KEY
    TIKTOK_CLIENT_REFERENCE_ID
    TIKTOK_IDEMPOTENCY_KEY

Optional environment variables are documented in docs/generation-api-v1.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import requests


CONNECT_TIMEOUT_SECONDS = 3.05
RESPONSE_TIMEOUT_SECONDS = 15.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
POST_ATTEMPTS = 4
POLL_MAX_DELAY_SECONDS = 30.0
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RESERVATION_ACTIONS = {
    "queued": "hold",
    "submitting": "hold",
    "reconciliation_required": "hold",
    "processing": "hold",
    "transferring": "hold",
    "succeeded": "settle",
    "failed": "release",
    "cancelled": "release",
}


class RelayResponseError(RuntimeError):
    def __init__(self, status: int, payload: dict[str, Any] | None) -> None:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        self.status = status
        self.code = str(error.get("code", "UNPARSEABLE_RELAY_ERROR"))
        self.retryable = bool(error.get("retryable", False))
        super().__init__(f"Relay HTTP {status}: {self.code}")


def require_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


def positive_float_environment(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def validate_relay_origin(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    local_names = {"127.0.0.1", "localhost", "::1"}
    allow_local = os.environ.get("TIKTOK_ALLOW_INSECURE_LOCALHOST") == "1"
    if parsed.scheme == "https" and parsed.hostname:
        return value.rstrip("/")
    if allow_local and parsed.scheme == "http" and parsed.hostname in local_names:
        return value.rstrip("/")
    raise RuntimeError(
        "RELAY_BASE_URL must use HTTPS; HTTP loopback requires "
        "TIKTOK_ALLOW_INSECURE_LOCALHOST=1"
    )


def safe_json(response: requests.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def retry_after_seconds(response: requests.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    return max(0.0, min(delay, POLL_MAX_DELAY_SECONDS))


def backoff(attempt: int, response: requests.Response | None = None) -> None:
    explicit = retry_after_seconds(response)
    base = explicit if explicit is not None else min(2 ** (attempt - 1), 30)
    time.sleep(base + random.uniform(0, min(base * 0.2, 1.0)))


def assert_versioned_resource(payload: dict[str, Any]) -> None:
    if payload.get("api_version") != "v1":
        raise RuntimeError("Relay response api_version is not v1")
    if payload.get("schema_version") != 1:
        raise RuntimeError("Relay response schema_version is not 1")


def assert_reservation_action(payload: dict[str, Any]) -> None:
    status = payload.get("status")
    expected = RESERVATION_ACTIONS.get(str(status))
    if expected is None or payload.get("reservation_action") != expected:
        raise RuntimeError("Relay returned an inconsistent reservation_action")


def service_headers(client_id: str, api_key: str, request_id: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Client-ID": client_id,
        "X-API-Key": api_key,
        "X-Request-ID": request_id,
    }


def read_catalog(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
) -> tuple[dict[str, Any], str]:
    response = session.get(
        f"{base_url}/v1/models",
        headers=headers,
        timeout=(CONNECT_TIMEOUT_SECONDS, RESPONSE_TIMEOUT_SECONDS),
    )
    if response.status_code != 200:
        raise RelayResponseError(response.status_code, safe_json(response))
    catalog = safe_json(response)
    etag = response.headers.get("ETag")
    if catalog is None or etag is None:
        raise RuntimeError("Relay model catalog or ETag is missing")
    assert_versioned_resource(catalog)
    for model in catalog.get("data", []):
        if not isinstance(model, dict):
            raise RuntimeError("Relay model catalog contains an invalid resource")
        assert_versioned_resource(model)

    # A real integration should persist both the catalog and ETag. This second
    # request demonstrates conditional revalidation without persisting secrets.
    conditional_headers = {**headers, "If-None-Match": etag}
    conditional = session.get(
        f"{base_url}/v1/models",
        headers=conditional_headers,
        timeout=(CONNECT_TIMEOUT_SECONDS, RESPONSE_TIMEOUT_SECONDS),
    )
    if conditional.status_code not in {200, 304}:
        raise RelayResponseError(conditional.status_code, safe_json(conditional))
    if conditional.status_code == 200:
        refreshed = safe_json(conditional)
        refreshed_etag = conditional.headers.get("ETag")
        if refreshed is None or refreshed_etag is None:
            raise RuntimeError("Conditional catalog response is incomplete")
        catalog, etag = refreshed, refreshed_etag
    return catalog, etag


def select_model(catalog: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    models = catalog.get("data")
    if not isinstance(models, list) or not models:
        raise RuntimeError("Relay published no generation models")
    selected_id = os.environ.get("TIKTOK_MODEL_ID")
    selected = next(
        (
            item
            for item in models
            if isinstance(item, dict)
            and (selected_id is None or item.get("id") == selected_id)
        ),
        None,
    )
    if selected is None:
        raise RuntimeError("TIKTOK_MODEL_ID is not present in the Relay catalog")
    modes = selected.get("capabilities", {}).get("modes", {})
    requested_mode = os.environ.get("TIKTOK_GENERATION_MODE")
    if requested_mode is None:
        requested_mode = next(iter(modes), None)
    capability = modes.get(requested_mode) if requested_mode else None
    if not isinstance(capability, dict):
        raise RuntimeError("The requested model/mode capability is unavailable")
    return selected, str(requested_mode), capability


def first_capability_value(capability: dict[str, Any], key: str) -> Any:
    values = capability.get("limits", {}).get(key)
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"Model capability has no {key}")
    return values[0]


def build_payload(
    model: dict[str, Any],
    mode: str,
    capability: dict[str, Any],
    client_reference_id: str,
) -> dict[str, Any]:
    assets: list[dict[str, str]] = []
    required_media = {
        "image_to_video": "image",
        "video_to_video": "video",
    }.get(mode)
    if required_media is not None:
        asset_url = require_environment("TIKTOK_INPUT_ASSET_URL")
        assets.append({"url": asset_url, "media_type": required_media})

    prompt = os.environ.get(
        "TIKTOK_GENERATION_PROMPT",
        "Create a concise product video suitable for an internal TikTok campaign.",
    )
    payload: dict[str, Any] = {
        "client_reference_id": client_reference_id,
        "model": model["id"],
        "expected_capability_revision": model["capability_revision"],
        "mode": mode,
        "inputs": {"prompt": prompt, "assets": assets},
        "output": {
            "duration_seconds": first_capability_value(
                capability, "duration_seconds"
            ),
            "aspect_ratio": first_capability_value(capability, "aspect_ratios"),
            "resolution": first_capability_value(capability, "resolutions"),
            "count": first_capability_value(capability, "output_counts"),
            "face_enabled": False,
        },
        "metadata": {"source": "internal-tiktok"},
    }
    callback_url = os.environ.get("INTERNAL_TIKTOK_RELAY_CALLBACK_URL")
    if callback_url:
        payload["callback"] = {"url": callback_url}
    return payload


def submit_with_uncertainty_safe_retry(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    idempotency_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    # Serialize once. Every retry reuses these exact bytes and the exact key.
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    submit_headers = {
        **headers,
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }
    last_problem = "no response"
    for attempt in range(1, POST_ATTEMPTS + 1):
        response: requests.Response | None = None
        try:
            response = session.post(
                f"{base_url}/v1/generations",
                data=body,
                headers=submit_headers,
                timeout=(CONNECT_TIMEOUT_SECONDS, RESPONSE_TIMEOUT_SECONDS),
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Delivery is uncertain. A new key could duplicate a paid task.
            last_problem = type(exc).__name__
        else:
            if 200 <= response.status_code < 300:
                accepted = safe_json(response)
                if accepted is None:
                    last_problem = "invalid successful response"
                else:
                    assert_versioned_resource(accepted)
                    assert_reservation_action(accepted)
                    if accepted.get("id") != accepted.get("job_id"):
                        raise RuntimeError("Accepted id and job_id do not match")
                    expected_revision = accepted.get(
                        "expected_capability_revision"
                    )
                    if (
                        expected_revision != payload["expected_capability_revision"]
                        or accepted.get("capability_revision") != expected_revision
                    ):
                        raise RuntimeError(
                            "Accepted capability revisions are inconsistent"
                        )
                    return accepted
            elif response.status_code == 429 or response.status_code >= 500:
                last_problem = f"HTTP {response.status_code}"
            else:
                raise RelayResponseError(response.status_code, safe_json(response))
        if attempt < POST_ATTEMPTS:
            backoff(attempt, response)
    raise RuntimeError(
        "Relay submission outcome remains uncertain after bounded retries "
        f"({last_problem}). Preserve the payload and Idempotency-Key; never "
        "create a replacement key."
    )


def get_job(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    job_id: str,
) -> dict[str, Any] | None:
    try:
        response = session.get(
            f"{base_url}/v1/generations/{job_id}",
            headers=headers,
            timeout=(CONNECT_TIMEOUT_SECONDS, RESPONSE_TIMEOUT_SECONDS),
        )
    except (requests.Timeout, requests.ConnectionError):
        return None
    if response.status_code == 429 or response.status_code >= 500:
        return None
    if response.status_code != 200:
        raise RelayResponseError(response.status_code, safe_json(response))
    job = safe_json(response)
    if job is None:
        raise RuntimeError("Relay returned an invalid job document")
    assert_versioned_resource(job)
    assert_reservation_action(job)
    return job


def issue_download(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    job_id: str,
    asset_id: str,
) -> str:
    response = session.get(
        f"{base_url}/v1/generations/{job_id}/artifacts/{asset_id}/download",
        headers=headers,
        timeout=(CONNECT_TIMEOUT_SECONDS, RESPONSE_TIMEOUT_SECONDS),
    )
    if response.status_code != 200:
        raise RelayResponseError(response.status_code, safe_json(response))
    payload = safe_json(response)
    if payload is None or not isinstance(payload.get("url"), str):
        raise RuntimeError("Relay returned an invalid signed download document")
    assert_versioned_resource(payload)
    return payload["url"]


def download_and_verify(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    job_id: str,
    asset: dict[str, Any],
    output_directory: Path,
) -> Path:
    asset_id = str(asset["asset_id"])
    suffix = ".mp4" if asset.get("media_type") == "video" else ".img"
    target = output_directory / f"{asset_id}{suffix}"
    temporary = target.with_suffix(target.suffix + ".part")
    for issuance in range(2):
        url = issue_download(session, base_url, headers, job_id, asset_id)
        response = session.get(
            url,
            stream=True,
            timeout=(CONNECT_TIMEOUT_SECONDS, DOWNLOAD_TIMEOUT_SECONDS),
        )
        if response.status_code in {401, 403} and issuance == 0:
            continue
        response.raise_for_status()
        digest = hashlib.sha256()
        size = 0
        with temporary.open("wb") as destination:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                destination.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != asset["sha256"] or size != asset["size_bytes"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Downloaded artifact failed size or SHA-256 verification")
        temporary.replace(target)
        return target
    raise RuntimeError("Signed download authorization could not be refreshed")


def run() -> int:
    base_url = validate_relay_origin(require_environment("RELAY_BASE_URL"))
    client_id = os.environ.get(
        "INTERNAL_TIKTOK_RELAY_CLIENT_ID", "internal-tiktok"
    )
    api_key = require_environment("INTERNAL_TIKTOK_RELAY_API_KEY")
    client_reference_id = require_environment("TIKTOK_CLIENT_REFERENCE_ID")
    idempotency_key = require_environment("TIKTOK_IDEMPOTENCY_KEY")
    if not 8 <= len(idempotency_key) <= 128:
        raise RuntimeError("TIKTOK_IDEMPOTENCY_KEY must contain 8-128 characters")

    request_id = str(uuid5(NAMESPACE_URL, f"internal-tiktok:{client_reference_id}"))
    headers = service_headers(client_id, api_key, request_id)
    session = requests.Session()
    catalog, etag = read_catalog(session, base_url, headers)
    model, mode, capability = select_model(catalog)
    print(f"Catalog {etag}; selected {model['id']} / {mode}")

    payload = build_payload(model, mode, capability, client_reference_id)
    accepted = submit_with_uncertainty_safe_retry(
        session, base_url, headers, idempotency_key, payload
    )
    if accepted.get("expected_capability_revision") != model["capability_revision"]:
        raise RuntimeError("Accepted job did not preserve the expected revision")
    job_id = str(accepted.get("id") or accepted["job_id"])
    print(f"Accepted job {job_id}; reservation={accepted['reservation_action']}")
    print(
        "Accepted reservation_action is informational only; settle is allowed "
        "only after a complete succeeded GET or trusted callback."
    )

    deadline = time.monotonic() + positive_float_environment(
        "TIKTOK_POLL_DEADLINE_SECONDS", 3600
    )
    poll_attempt = 0
    last_status: str | None = None
    job: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        poll_attempt += 1
        job = get_job(session, base_url, headers, job_id)
        if job is not None:
            status = str(job["status"])
            if status != last_status:
                print(
                    f"Job {job_id}: {status} "
                    f"({job['progress']}%), reservation={job['reservation_action']}"
                )
                last_status = status
            if status in TERMINAL_STATUSES:
                break
        backoff(min(poll_attempt, 5))
    else:
        print(
            "Local polling deadline reached. This is not a Relay failure; "
            "keep the job and reservation on hold and continue background polling.",
            file=sys.stderr,
        )
        return 2

    assert job is not None
    if (
        job.get("expected_capability_revision")
        != payload["expected_capability_revision"]
        or job.get("capability_revision")
        != job.get("expected_capability_revision")
    ):
        raise RuntimeError("Job capability revisions are inconsistent")
    if job["status"] != "succeeded":
        error = job.get("error") or {}
        print(
            f"Terminal job {job_id}: {job['status']} / "
            f"{error.get('code', 'NO_ERROR_CODE')}; "
            f"reservation={job['reservation_action']}"
        )
        return 1

    outputs = job.get("outputs")
    if (
        job.get("progress") != 100
        or job.get("error") is not None
        or not isinstance(outputs, list)
        or not outputs
        or len(outputs) != job.get("output", {}).get("count")
    ):
        raise RuntimeError("Succeeded job does not contain its complete artifact set")

    output_directory = Path(
        os.environ.get("TIKTOK_OUTPUT_DIRECTORY", "generated-artifacts")
    ).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    for asset in outputs:
        saved = download_and_verify(
            session, base_url, headers, job_id, asset, output_directory
        )
        print(f"Verified artifact: {saved}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (RelayResponseError, RuntimeError, requests.RequestException) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
