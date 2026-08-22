from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..dependencies import get_db, require_internal_service
from ..services.relay_telemetry import (
    RelayOperationsSnapshotPayload,
    RelayTaskStagePayload,
    RelayTelemetryService,
)
from ..services.provider_alerts import (
    ProviderAlertService,
    RelayProviderAlertPayload,
)


router = APIRouter(prefix="/internal/relay", tags=["relay-telemetry"])

MAX_RELAY_TELEMETRY_BODY_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_ALERT_BODY_BYTES = 64 * 1024


async def _read_limited_body(
    request: Request,
    *,
    max_bytes: int = MAX_RELAY_TELEMETRY_BODY_BYTES,
) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Content-Length must be a non-negative integer",
            ) from None
        if declared_length < 0:
            raise HTTPException(
                status_code=400,
                detail="Content-Length must be a non-negative integer",
            )
        if declared_length > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Relay telemetry body exceeds the maximum size",
            )
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Relay telemetry body exceeds the maximum size",
            )
        body.extend(chunk)
    return bytes(body)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else "relay-telemetry"


def _response(
    response: Response,
    *,
    event_id: str,
    duplicate: bool,
    received_at,
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Relay-Telemetry-Duplicate"] = (
        "true" if duplicate else "false"
    )
    return {
        "event_id": event_id,
        "duplicate": duplicate,
        "received_at": received_at.isoformat(),
    }


@router.post("/provider-alerts", status_code=201)
async def ingest_provider_alert(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, object]:
    # This endpoint intentionally uses a dedicated HMAC secret instead of the
    # general internal-service token. The new-api Relay provider-alert worker
    # signs the exact persisted bytes on every durable delivery attempt.
    verifier = getattr(request.app.state, "provider_alert_verifier", None)
    forwarder = getattr(request.app.state, "provider_alert_forwarder", None)
    if verifier is None or forwarder is None:
        raise HTTPException(
            status_code=503,
            detail="Provider alert receiver or downstream is not configured",
        )
    raw_body = await _read_limited_body(
        request,
        max_bytes=MAX_PROVIDER_ALERT_BODY_BYTES,
    )
    payload, digest, delivered_at, event_id = verifier.verify(
        raw_body,
        event_id=request.headers.get("X-Relay-Event-ID"),
        timestamp=request.headers.get("X-Relay-Timestamp"),
        signature=request.headers.get("X-Relay-Signature"),
        payload_type=RelayProviderAlertPayload,
    )
    entry, duplicate = await ProviderAlertService.record_and_forward(
        session,
        payload=payload,
        raw_body=raw_body,
        event_id=event_id,
        payload_sha256=digest,
        delivery_timestamp=delivered_at,
        request_id=_request_id(request),
        forwarder=forwarder,
    )
    response.status_code = 200 if duplicate else 201
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Provider-Alert-Duplicate"] = (
        "true" if duplicate else "false"
    )
    return {
        "event_id": entry.id,
        "duplicate": duplicate,
        "received_at": entry.received_at.isoformat(),
    }


@router.post("/task-stages", status_code=201)
async def ingest_task_stage(
    request: Request,
    response: Response,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, object]:
    verifier = getattr(request.app.state, "relay_telemetry_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=503,
            detail="Relay telemetry verifier is not configured",
        )
    payload, digest, delivered_at, event_id = verifier.verify(
        await _read_limited_body(request),
        event_id=request.headers.get("X-Relay-Event-ID"),
        timestamp=request.headers.get("X-Relay-Timestamp"),
        signature=request.headers.get("X-Relay-Signature"),
        payload_type=RelayTaskStagePayload,
    )
    entry, duplicate = RelayTelemetryService.record_task_stage(
        session,
        payload=payload,
        event_id=event_id,
        payload_sha256=digest,
        delivery_timestamp=delivered_at,
        request_id=_request_id(request),
    )
    return _response(
        response,
        event_id=entry.id,
        duplicate=duplicate,
        received_at=entry.received_at,
    )


@router.post("/operations-snapshots", status_code=201)
async def ingest_operations_snapshot(
    request: Request,
    response: Response,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, object]:
    verifier = getattr(request.app.state, "relay_telemetry_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=503,
            detail="Relay telemetry verifier is not configured",
        )
    payload, digest, delivered_at, event_id = verifier.verify(
        await _read_limited_body(request),
        event_id=request.headers.get("X-Relay-Event-ID"),
        timestamp=request.headers.get("X-Relay-Timestamp"),
        signature=request.headers.get("X-Relay-Signature"),
        payload_type=RelayOperationsSnapshotPayload,
    )
    snapshot, duplicate = RelayTelemetryService.record_operations_snapshot(
        session,
        payload=payload,
        event_id=event_id,
        payload_sha256=digest,
        delivery_timestamp=delivered_at,
        request_id=_request_id(request),
    )
    return _response(
        response,
        event_id=snapshot.id,
        duplicate=duplicate,
        received_at=snapshot.received_at,
    )
