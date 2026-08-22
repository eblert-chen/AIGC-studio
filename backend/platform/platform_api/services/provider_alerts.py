from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import re
import socket
import time
from collections.abc import Awaitable, Sequence
from typing import Callable, Literal
from uuid import UUID

import httpx
from pydantic import Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import RelayProviderAlertEvent, utcnow
from .errors import ConflictError, DomainError
from .relay_telemetry import StrictTelemetryModel


_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

AlertHostnameResolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve_alert_hostname(host: str, port: int) -> tuple[str, ...]:
    """Resolve every A/AAAA result before a production alert connection.

    The caller pins one validated address into the request URL, so httpx never
    performs a second, attacker-controlled lookup between validation and dial.
    """

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return (str(literal),)

    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise httpx.ConnectError("Provider alert downstream DNS resolution failed") from exc
    return tuple(sorted({item[4][0] for item in addresses}))


def _validated_global_alert_address(addresses: Sequence[str]) -> str:
    """Fail closed when any DNS answer is not globally routable.

    Rejecting the entire answer set (rather than choosing a public answer) is
    intentional: a mixed public/private response is a DNS-rebinding attempt.
    """

    if not addresses:
        raise httpx.ConnectError("Provider alert downstream DNS returned no addresses")
    parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise httpx.ConnectError(
                "Provider alert downstream DNS returned an invalid address"
            ) from exc
        if not parsed.is_global:
            raise httpx.ConnectError(
                "Provider alert downstream DNS resolved to a non-global address"
            )
        parsed_addresses.append(parsed)
    # A deterministic choice makes the resolution-to-dial binding auditable and
    # prevents a resolver answer order from changing the selected endpoint.
    selected = min(parsed_addresses, key=lambda value: (value.version, value.packed))
    return str(selected)


class _ProductionAlertTransport(httpx.AsyncBaseTransport):
    """Pins each production request to validated DNS output.

    httpx normally resolves the hostname while dialing. This transport resolves
    first, rejects every non-global result, then rewrites the dial target to a
    validated IP while retaining the original HTTP Host and TLS SNI identity.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        resolver: AlertHostnameResolver,
    ) -> None:
        self._transport = transport
        self._resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if not host:
            raise httpx.ConnectError("Provider alert downstream host is missing")
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        address = _validated_global_alert_address(await self._resolver(host, port))
        authority = host if request.url.port is None else f"{host}:{request.url.port}"
        headers = request.headers.copy()
        headers["Host"] = authority
        extensions = dict(request.extensions)
        # httpcore honours this extension when doing the TLS handshake, so a
        # certificate is still verified for the configured hostname, not IP.
        extensions["sni_hostname"] = host
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=address),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self._transport.aclose()


class RelayProviderAlertIncident(StrictTelemetryModel):
    kind: Literal[
        "success_rate_drop",
        "widespread_route_failure",
        "batch_account_invalidation",
    ]
    state: Literal["triggered", "recovered"]
    provider_name: str = Field(min_length=1, max_length=64)
    generation: int = Field(strict=True, ge=1, le=2_147_483_647)
    reason_code: str = Field(min_length=1, max_length=64)
    sample_size: int = Field(strict=True, ge=0, le=2_147_483_647)
    success_count: int = Field(strict=True, ge=0, le=2_147_483_647)
    affected_routes: int = Field(strict=True, ge=0, le=2_147_483_647)
    total_routes: int = Field(strict=True, ge=0, le=2_147_483_647)
    success_rate_basis_points: int = Field(strict=True, ge=0, le=10_000)

    @field_validator("provider_name")
    @classmethod
    def validate_provider_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("provider_name must be normalized")
        return value

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if not _SAFE_CODE.fullmatch(value):
            raise ValueError("reason_code is invalid")
        return value

    @model_validator(mode="after")
    def validate_counts(self) -> "RelayProviderAlertIncident":
        if self.success_count > self.sample_size:
            raise ValueError("success_count cannot exceed sample_size")
        if self.affected_routes > self.total_routes:
            raise ValueError("affected_routes cannot exceed total_routes")
        return self


class RelayProviderAlertPayload(StrictTelemetryModel):
    schema_version: Literal[1]
    event_id: UUID
    type: str = Field(min_length=1, max_length=192)
    occurred_at: datetime
    incident: RelayProviderAlertIncident

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_identity(self) -> "RelayProviderAlertPayload":
        expected_type = (
            f"provider_monitor.{self.incident.kind}.{self.incident.state}"
        )
        if self.type != expected_type:
            raise ValueError("type does not match the incident transition")
        return self


class RelayProviderAlertPayloadError(DomainError):
    def __init__(self, message: str = "Relay provider alert payload is invalid") -> None:
        super().__init__(message, "relay_provider_alert_invalid", 422)


class ProviderAlertDownstreamError(DomainError):
    def __init__(
        self, message: str = "Provider alert downstream delivery failed"
    ) -> None:
        # A 5xx response is intentional: the Relay treats it as retryable and
        # retains the durable delivery until its configured retry limit.
        super().__init__(message, "provider_alert_downstream_unavailable", 503)


class ProviderAlertForwarder:
    """Async, signed bridge to the operator-owned alert receiver.

    Relay retries are the durable queue. This bridge only acknowledges the
    inbound event after the downstream accepted the exact body. A stable
    Idempotency-Key lets the downstream close the unavoidable crash window
    between its acknowledgement and the Platform database commit.
    """

    def __init__(
        self,
        webhook_url: str,
        signing_secret: str,
        *,
        timeout_seconds: float = 5.0,
        production: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: AlertHostnameResolver = _resolve_alert_hostname,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            not webhook_url
            or webhook_url != webhook_url.strip()
            or any(character.isspace() for character in webhook_url)
        ):
            raise ValueError("Provider alert downstream URL is invalid")
        try:
            parsed_url = httpx.URL(webhook_url)
        except httpx.InvalidURL as exc:
            raise ValueError("Provider alert downstream URL is invalid") from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.host
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Provider alert downstream URL is invalid")
        if len(signing_secret.encode("utf-8")) < 32:
            raise ValueError(
                "Provider alert downstream signing secret must contain at least 32 bytes"
            )
        if timeout_seconds < 1 or timeout_seconds > 30:
            raise ValueError("Provider alert downstream timeout is invalid")
        self._webhook_url = webhook_url
        self._secret = signing_secret.encode("utf-8")
        self._clock = clock
        downstream_transport = transport or httpx.AsyncHTTPTransport(
            # A pinned address must be resolved for each attempt. Do not reuse
            # a connection whose hostname could now resolve somewhere else.
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=0),
        )
        if production:
            downstream_transport = _ProductionAlertTransport(
                downstream_transport,
                resolver,
            )
        self._client = httpx.AsyncClient(
            transport=downstream_transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forward(
        self,
        raw_body: bytes,
        *,
        event_id: str,
        request_id: str,
    ) -> int:
        try:
            canonical_event_id = str(UUID(event_id))
        except ValueError as exc:
            raise ProviderAlertDownstreamError() from exc
        if canonical_event_id != event_id or not raw_body:
            raise ProviderAlertDownstreamError()
        timestamp = str(int(self._clock()))
        signing_input = (
            timestamp.encode("ascii")
            + b"."
            + event_id.encode("ascii")
            + b"."
            + raw_body
        )
        signature = hmac.new(
            self._secret, signing_input, hashlib.sha256
        ).hexdigest()
        try:
            response = await self._client.post(
                self._webhook_url,
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "ai-video-platform-alert-bridge/1.0",
                    "Idempotency-Key": event_id,
                    "X-Alert-Event-ID": event_id,
                    "X-Alert-Timestamp": timestamp,
                    "X-Alert-Signature": f"v1={signature}",
                    "X-Request-ID": request_id,
                },
            )
        except httpx.RequestError as exc:
            raise ProviderAlertDownstreamError() from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderAlertDownstreamError()
        return response.status_code


class ProviderAlertService:
    @staticmethod
    async def record_and_forward(
        session: Session,
        *,
        payload: RelayProviderAlertPayload,
        raw_body: bytes,
        event_id: str,
        payload_sha256: str,
        delivery_timestamp: datetime,
        request_id: str,
        forwarder: ProviderAlertForwarder,
    ) -> tuple[RelayProviderAlertEvent, bool]:
        if str(payload.event_id) != event_id:
            raise RelayProviderAlertPayloadError(
                "Relay provider alert body event_id does not match its signed header"
            )
        existing = session.get(RelayProviderAlertEvent, event_id)
        if existing is not None:
            if not hmac.compare_digest(existing.payload_sha256, payload_sha256):
                raise ConflictError(
                    "Relay provider alert event_id is already bound to different bytes"
                )
            return existing, True

        # ``Session.get`` starts a transaction on PostgreSQL. Do not retain its
        # connection or primary-key locks while awaiting an operator-controlled
        # network call. The downstream Idempotency-Key deliberately closes the
        # resulting crash/concurrent-delivery window; after a 2xx we race only
        # on the brief immutable receipt insert below.
        session.rollback()

        incident = payload.incident
        entry = RelayProviderAlertEvent(
            id=event_id,
            schema_version=payload.schema_version,
            event_type=payload.type,
            occurred_at=payload.occurred_at,
            incident_kind=incident.kind,
            incident_state=incident.state,
            provider_name=incident.provider_name,
            generation=incident.generation,
            reason_code=incident.reason_code,
            sample_size=incident.sample_size,
            success_count=incident.success_count,
            affected_routes=incident.affected_routes,
            total_routes=incident.total_routes,
            success_rate_basis_points=incident.success_rate_basis_points,
            delivery_timestamp=delivery_timestamp,
            payload_sha256=payload_sha256,
            request_id=request_id,
            received_at=utcnow(),
        )
        await forwarder.forward(
            raw_body,
            event_id=event_id,
            request_id=request_id,
        )
        try:
            # Only a short database operation remains after downstream 2xx.
            # If we crash or this insert loses its uniqueness race, Relay can
            # safely retry because the downstream received the stable event ID.
            session.add(entry)
            session.flush()
        except IntegrityError:
            session.rollback()
            repeated = session.get(RelayProviderAlertEvent, event_id)
            if repeated is None:
                raise
            if not hmac.compare_digest(repeated.payload_sha256, payload_sha256):
                raise ConflictError(
                    "Relay provider alert event_id is already bound to different bytes"
                )
            return repeated, True
        # The enclosing request transaction commits the immutable row after
        # downstream acceptance. A downstream or database failure therefore
        # leaves no receipt and remains deliverable by Relay retry.
        return entry, False
