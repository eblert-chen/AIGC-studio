"""Reference HMAC receiver for internal-tiktok Relay callbacks.

Run this behind a production HTTPS reverse proxy that preserves the raw request
body and all X-Relay-* headers. The example stores event IDs and body hashes in
SQLite so duplicate delivery is durable across restarts. A production receiver
must apply the TikTok task transition and reservation instruction in the same
database transaction as the immutable event receipt.

Required environment variable:
    INTERNAL_TIKTOK_RELAY_CALLBACK_SECRET
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
from uuid import UUID


MAX_BODY_BYTES = 1024 * 1024
REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SIGNATURE_PATTERN = re.compile(r"^v1=([0-9a-f]{64})$")
ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_ASYNC_ERROR_CODES = frozenset(
    {
        "MODEL_CAPABILITY_UNAVAILABLE",
        "CAPABILITY_REVISION_MISMATCH",
        "REQUEST_NOT_SUPPORTED_BY_MODEL",
        "MODE_NOT_SUPPORTED_BY_MODEL",
        "NO_PROVIDER_AVAILABLE",
        "PROVIDER_ACCOUNT_POOL_BUSY",
        "PROVIDER_ACCOUNT_POOL_RATE_LIMITED",
        "PROVIDER_TASK_NOT_ASSIGNED",
        "PROVIDER_NOT_FOUND",
        "PROVIDER_CIRCUIT_OPEN",
        "PROVIDER_POLL_FAILED",
        "PROVIDER_TASK_MISMATCH",
        "PROVIDER_TASK_ID_INVALID",
        "UPSTREAM_FAILED",
        "CONTENT_POLICY_REJECTED",
        "INPUT_ASSET_UNAVAILABLE",
        "GENERATION_FAILED",
        "GENERATION_TASK_NOT_FOUND_UPSTREAM",
        "GENERATION_CHANNEL_RESPONSE_INVALID",
        "GENERATION_CHANNEL_UNAVAILABLE",
        "ARTIFACT_TRANSFER_RETRYING",
        "ARTIFACT_TRANSFER_FAILED",
        "SUBMISSION_RECONCILIATION_REQUIRED",
        "SUBMISSION_CONFIRMED_NOT_CREATED",
        "PROVIDER_RETRIES_EXHAUSTED",
        "WORKER_ATTEMPTS_EXHAUSTED",
        "PROVIDER_POLL_RECONCILIATION_REQUIRED",
    }
)
RESERVATION_ACTIONS = {
    "reconciliation_required": "hold",
    "processing": "hold",
    "succeeded": "settle",
    "failed": "release",
    "cancelled": "release",
}


class CallbackRejected(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def require_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise RuntimeError(f"{name} is required")
    return value


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CallbackRejected(400, "callback JSON contains a duplicate key")
        result[key] = value
    return result


def parse_aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CallbackRejected(400, "occurred_at must be a date-time string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CallbackRejected(400, "occurred_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CallbackRejected(400, "occurred_at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def validate_revision(value: Any, field: str) -> None:
    if not isinstance(value, str) or REVISION_PATTERN.fullmatch(value) is None:
        raise CallbackRejected(400, f"{field} is not a valid capability revision")


def validate_error(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "code",
        "message",
        "retryable",
        "details",
    }:
        raise CallbackRejected(400, "job.error does not match schema v1")
    if (
        not isinstance(value["code"], str)
        or ERROR_CODE_PATTERN.fullmatch(value["code"]) is None
        or value["code"] not in PUBLIC_ASYNC_ERROR_CODES
        or not isinstance(value["message"], str)
        or not isinstance(value["retryable"], bool)
        or not isinstance(value["details"], dict)
    ):
        raise CallbackRejected(400, "job.error fields are invalid")


def validate_artifact(value: Any) -> None:
    expected = {
        "asset_id",
        "object_key",
        "media_type",
        "content_type",
        "size_bytes",
        "sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CallbackRejected(400, "callback artifact does not match schema v1")
    try:
        UUID(str(value["asset_id"]))
    except ValueError as exc:
        raise CallbackRejected(400, "artifact.asset_id is not a UUID") from exc
    size = value["size_bytes"]
    if (
        not isinstance(value["object_key"], str)
        or not 1 <= len(value["object_key"]) <= 1024
        or value["media_type"] not in {"image", "video"}
        or not isinstance(value["content_type"], str)
        or not value["content_type"]
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(value["sha256"], str)
        or SHA256_PATTERN.fullmatch(value["sha256"]) is None
    ):
        raise CallbackRejected(400, "callback artifact fields are invalid")


def validate_payload(payload: Any, header_event_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CallbackRejected(400, "callback body must be a JSON object")
    expected_root = {
        "api_version",
        "schema_version",
        "event_id",
        "type",
        "occurred_at",
        "job",
    }
    if set(payload) != expected_root:
        raise CallbackRejected(400, "callback root fields do not match schema v1")
    if payload["api_version"] != "v1" or payload["schema_version"] != 1:
        raise CallbackRejected(400, "unsupported callback contract version")
    if payload["type"] != "generation.status_changed":
        raise CallbackRejected(400, "unsupported callback event type")
    try:
        UUID(str(payload["event_id"]))
    except ValueError as exc:
        raise CallbackRejected(400, "event_id is not a UUID") from exc
    if payload["event_id"] != header_event_id:
        raise CallbackRejected(400, "callback event header and body do not match")
    parse_aware_datetime(payload["occurred_at"])

    job = payload.get("job")
    expected_job = {
        "api_version",
        "id",
        "client_reference_id",
        "status",
        "progress",
        "outputs",
        "error",
        "expected_capability_revision",
        "capability_revision",
        "reservation_action",
    }
    if not isinstance(job, dict) or set(job) != expected_job:
        raise CallbackRejected(400, "callback job fields do not match schema v1")
    if job["api_version"] != "v1":
        raise CallbackRejected(400, "callback job api_version is not v1")
    try:
        UUID(str(job["id"]))
    except ValueError as exc:
        raise CallbackRejected(400, "job.id is not a UUID") from exc
    client_reference_id = job.get("client_reference_id")
    if client_reference_id is not None and (
        not isinstance(client_reference_id, str)
        or len(client_reference_id) > 128
    ):
        raise CallbackRejected(400, "job.client_reference_id is invalid")
    status = job.get("status")
    action = RESERVATION_ACTIONS.get(str(status))
    if action is None or job.get("reservation_action") != action:
        raise CallbackRejected(400, "status and reservation_action are inconsistent")
    if (
        not isinstance(job.get("progress"), int)
        or isinstance(job["progress"], bool)
        or not 0 <= job["progress"] <= 100
    ):
        raise CallbackRejected(400, "job.progress is invalid")
    validate_revision(
        job.get("expected_capability_revision"),
        "job.expected_capability_revision",
    )
    validate_revision(job.get("capability_revision"), "job.capability_revision")
    if job["expected_capability_revision"] != job["capability_revision"]:
        raise CallbackRejected(400, "callback capability revisions do not match")
    outputs = job.get("outputs")
    if not isinstance(outputs, list) or len(outputs) > 16:
        raise CallbackRejected(400, "job.outputs must contain at most 16 items")
    for output in outputs:
        validate_artifact(output)
    error = job.get("error")
    if error is not None:
        validate_error(error)
    if status == "succeeded":
        if job["progress"] != 100 or not outputs or error is not None:
            raise CallbackRejected(400, "succeeded job is incomplete")
    elif outputs:
        raise CallbackRejected(400, "non-succeeded job cannot expose outputs")
    if status == "failed" and error is None:
        raise CallbackRejected(400, "failed job must contain an error")
    return payload


def verify_signature(
    raw_body: bytes,
    event_id: str | None,
    timestamp_text: str | None,
    signature: str | None,
    signing_key: bytes,
    max_age_seconds: int,
) -> str:
    if not event_id or not timestamp_text or not signature:
        raise CallbackRejected(401, "callback signature headers are required")
    try:
        UUID(event_id)
        timestamp = int(timestamp_text)
    except (ValueError, TypeError) as exc:
        raise CallbackRejected(401, "callback signature headers are invalid") from exc
    if abs(int(time.time()) - timestamp) > max_age_seconds:
        raise CallbackRejected(401, "callback timestamp is outside the replay window")
    match = SIGNATURE_PATTERN.fullmatch(signature)
    if match is None:
        raise CallbackRejected(401, "callback signature format is invalid")
    signing_input = f"{timestamp}.{event_id}.".encode("ascii") + raw_body
    expected = hmac.new(signing_key, signing_input, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise CallbackRejected(401, "callback signature is invalid")
    return event_id


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_callback_events (
                    event_id TEXT PRIMARY KEY,
                    body_sha256 TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reservation_action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def record(self, event: dict[str, Any], raw_body: bytes) -> bool:
        event_id = str(event["event_id"])
        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        job = event["job"]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT body_sha256 FROM relay_callback_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                if existing[0] != body_sha256:
                    raise CallbackRejected(
                        409, "event_id was already used with a different body"
                    )
                return True
            # In production, update the TikTok task and apply reservation_action
            # in this same transaction before committing this immutable receipt.
            connection.execute(
                """
                INSERT INTO relay_callback_events (
                    event_id, body_sha256, job_id, status,
                    reservation_action, occurred_at, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    body_sha256,
                    str(job["id"]),
                    str(job["status"]),
                    str(job["reservation_action"]),
                    str(event["occurred_at"]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
            return False


class CallbackHandler(BaseHTTPRequestHandler):
    server_version = "InternalTikTokRelayCallback/1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/internal/relay-callbacks":
            self.send_error_json(404, "route not found")
            return
        try:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type.strip().casefold() != "application/json":
                raise CallbackRejected(415, "Content-Type must be application/json")
            length_text = self.headers.get("Content-Length")
            if length_text is None:
                raise CallbackRejected(411, "Content-Length is required")
            try:
                length = int(length_text)
            except ValueError as exc:
                raise CallbackRejected(400, "Content-Length is invalid") from exc
            if length < 0 or length > MAX_BODY_BYTES:
                raise CallbackRejected(413, "callback body is too large")
            raw_body = self.rfile.read(length)
            if len(raw_body) != length:
                raise CallbackRejected(400, "callback body is incomplete")
            event_id = verify_signature(
                raw_body,
                self.headers.get("X-Relay-Event-ID"),
                self.headers.get("X-Relay-Timestamp"),
                self.headers.get("X-Relay-Signature"),
                self.server.signing_key,
                self.server.max_age_seconds,
            )
            try:
                payload = json.loads(
                    raw_body.decode("utf-8"),
                    object_pairs_hook=reject_duplicate_keys,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CallbackRejected(400, "callback body is not valid JSON") from exc
            event = validate_payload(payload, event_id)
            duplicate = self.server.event_store.record(event, raw_body)
        except CallbackRejected as exc:
            self.send_error_json(exc.status, str(exc))
            return
        except (OSError, sqlite3.Error, ValueError):
            self.send_error_json(500, "callback receiver could not persist the event")
            return

        self.send_response(204)
        self.send_header(
            "X-Relay-Callback-Duplicate", "true" if duplicate else "false"
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def send_error_json(self, status: int, message: str) -> None:
        body = json.dumps(
            {"error": {"code": "CALLBACK_REJECTED", "message": message}},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the reference receiver quiet and avoid logging signed payloads.
        return


class CallbackServer(ThreadingHTTPServer):
    signing_key: bytes
    max_age_seconds: int
    event_store: EventStore


def run() -> None:
    signing_key = require_environment(
        "INTERNAL_TIKTOK_RELAY_CALLBACK_SECRET"
    ).encode("utf-8")
    if len(signing_key) < 32:
        raise RuntimeError(
            "INTERNAL_TIKTOK_RELAY_CALLBACK_SECRET must contain at least 32 bytes"
        )
    max_age_seconds = int(
        os.environ.get("INTERNAL_TIKTOK_CALLBACK_MAX_AGE_SECONDS", "300")
    )
    if max_age_seconds <= 0:
        raise RuntimeError("callback replay window must be positive")
    bind_host = os.environ.get("INTERNAL_TIKTOK_CALLBACK_BIND", "127.0.0.1")
    bind_port = int(os.environ.get("INTERNAL_TIKTOK_CALLBACK_PORT", "8088"))
    database_path = Path(
        os.environ.get(
            "INTERNAL_TIKTOK_CALLBACK_DB_PATH",
            "internal-tiktok-callback-events.sqlite3",
        )
    ).resolve()

    server = CallbackServer((bind_host, bind_port), CallbackHandler)
    server.signing_key = signing_key
    server.max_age_seconds = max_age_seconds
    server.event_store = EventStore(database_path)
    print(
        f"Listening on http://{bind_host}:{bind_port}/internal/relay-callbacks; "
        "publish it only through an HTTPS reverse proxy"
    )
    server.serve_forever()


if __name__ == "__main__":
    run()
