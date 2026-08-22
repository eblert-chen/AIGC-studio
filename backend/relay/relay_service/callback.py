from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import aiohttp
from aiohttp.abc import AbstractResolver

from .errors import RelayError
from .models import CallbackDelivery
from .repository import CallbackRepository


MINIMUM_CALLBACK_SECRET_BYTES = 32
_PRODUCTION_SECRET_PLACEHOLDERS = (
    "changeme",
    "replaceme",
    "placeholder",
    "developmentonly",
    "exampleonly",
)


class CallbackConfigurationError(RuntimeError):
    """A callback cannot be delivered under the current trusted policy."""


class CallbackTransportError(RuntimeError):
    """An outbound callback attempt did not obtain an HTTP response."""


@dataclass(frozen=True)
class CallbackRoute:
    url: str
    signing_secret: str = field(repr=False)


def callback_secret_is_placeholder(secret: str) -> bool:
    normalized = "".join(
        character for character in secret.casefold() if character.isalnum()
    )
    return any(marker in normalized for marker in _PRODUCTION_SECRET_PLACEHOLDERS)


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def normalize_callback_url(url: str, *, production: bool) -> str:
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 2_048
        or url != url.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in url
        )
    ):
        raise CallbackConfigurationError("Callback URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise CallbackConfigurationError("Callback URL is invalid") from exc
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise CallbackConfigurationError(
            "Callback URL must not contain credentials, query, or fragment"
        )
    scheme = parsed.scheme.casefold()
    if production:
        if scheme != "https" or (port is not None and port != 443):
            raise CallbackConfigurationError(
                "Production callbacks require HTTPS port 443"
            )
        hostname = parsed.hostname.casefold().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
            (".localhost", ".local", ".internal")
        ):
            raise CallbackConfigurationError(
                "Production callback hostname is not public"
            )
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise CallbackConfigurationError(
                "Production callback address is not public"
            )
    elif scheme not in {"http", "https"}:
        raise CallbackConfigurationError(
            "Development callbacks require HTTP or HTTPS"
        )

    hostname = parsed.hostname.casefold().rstrip(".")
    default_port = 443 if scheme == "https" else 80
    netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]" if port in {None, default_port} else f"[{hostname}]:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


def parse_callback_routes(
    serialized: str | None,
    *,
    production: bool,
) -> dict[UUID, CallbackRoute]:
    if serialized is None or not serialized.strip():
        return {}
    try:
        payload = json.loads(
            serialized,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "RELAY_CALLBACK_ROUTES_JSON must be valid JSON without duplicate keys"
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(
            "RELAY_CALLBACK_ROUTES_JSON must be a non-empty object"
        )

    routes: dict[UUID, CallbackRoute] = {}
    for tenant_value, raw_route in payload.items():
        try:
            tenant_id = UUID(tenant_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Each callback route key must be a tenant UUID"
            ) from exc
        if not isinstance(raw_route, dict) or set(raw_route) != {
            "url",
            "signing_secret",
        }:
            raise RuntimeError(
                f"Callback route for tenant '{tenant_id}' must contain exactly "
                "'url' and 'signing_secret'"
            )
        url = raw_route["url"]
        secret = raw_route["signing_secret"]
        if not isinstance(url, str) or not url:
            raise RuntimeError(
                f"Callback URL for tenant '{tenant_id}' must be a string"
            )
        if (
            not isinstance(secret, str)
            or len(secret.encode("utf-8")) < MINIMUM_CALLBACK_SECRET_BYTES
        ):
            raise RuntimeError(
                f"Callback signing secret for tenant '{tenant_id}' must contain "
                f"at least {MINIMUM_CALLBACK_SECRET_BYTES} bytes"
            )
        if production and callback_secret_is_placeholder(secret):
            raise RuntimeError(
                f"Callback signing secret for tenant '{tenant_id}' uses an "
                "obvious placeholder"
            )
        try:
            normalized = normalize_callback_url(url, production=production)
        except CallbackConfigurationError as exc:
            raise RuntimeError(
                f"Callback URL for tenant '{tenant_id}' is not allowed: {exc}"
            ) from exc
        routes[tenant_id] = CallbackRoute(
            url=normalized,
            signing_secret=secret,
        )
    return routes


class CallbackPolicy:
    """Matches caller input to an exact, trusted tenant route."""

    def __init__(
        self,
        routes: dict[UUID, CallbackRoute],
        *,
        production: bool,
    ) -> None:
        self.routes = routes.copy()
        self.production = production

    def authorize(self, tenant_id: UUID, requested_url: str) -> str:
        route = self.routes.get(tenant_id)
        if route is None:
            raise RelayError(
                "CALLBACK_NOT_CONFIGURED",
                "No callback route is configured for this tenant",
                status_code=422,
            )
        try:
            normalized = normalize_callback_url(
                requested_url,
                production=self.production,
            )
        except CallbackConfigurationError as exc:
            raise RelayError(
                "CALLBACK_URL_NOT_ALLOWED",
                "Callback URL is not allowed",
                status_code=422,
            ) from exc
        if normalized != route.url:
            raise RelayError(
                "CALLBACK_URL_NOT_ALLOWED",
                "Callback URL is not allowed",
                status_code=422,
            )
        return route.url

    def route_for(self, delivery: CallbackDelivery) -> CallbackRoute:
        route = self.routes.get(delivery.tenant_id)
        if route is None or route.url != delivery.callback_url:
            raise CallbackConfigurationError(
                "Trusted callback route is unavailable"
            )
        return route


def serialize_callback_event(delivery: CallbackDelivery) -> bytes:
    return json.dumps(
        delivery.event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_callback(
    secret: str,
    *,
    timestamp: str,
    event_id: UUID,
    body: bytes,
) -> str:
    signing_input = (
        timestamp.encode("ascii")
        + b"."
        + str(event_id).encode("ascii")
        + b"."
        + body
    )
    digest = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    return f"v1={digest}"


class CallbackTransport(Protocol):
    async def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        *,
        production: bool,
    ) -> int: ...


class _PinnedResolver(AbstractResolver):
    def __init__(self, host: str, addresses: list[str]) -> None:
        self.host = host
        self.addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict]:
        if host != self.host:
            raise CallbackConfigurationError(
                "Unexpected hostname during callback delivery"
            )
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
            for address in self.addresses
        ]

    async def close(self) -> None:
        return None


class AioHttpCallbackTransport:
    """No-redirect HTTP transport with public-DNS pinning in production."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        resolve_host: Callable[[str], object] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._resolve_host_override = resolve_host

    async def _resolve(self, host: str, port: int) -> list[str]:
        if self._resolve_host_override is not None:
            result = self._resolve_host_override(host)
            if asyncio.iscoroutine(result):
                result = await result
            return list(result)  # type: ignore[arg-type]
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return sorted({record[4][0] for record in records})

    @staticmethod
    def _assert_public(addresses: list[str]) -> None:
        if not addresses:
            raise CallbackConfigurationError(
                "Callback hostname did not resolve"
            )
        for address in addresses:
            ip = ipaddress.ip_address(address)
            mapped = getattr(ip, "ipv4_mapped", None)
            if not ip.is_global or (mapped is not None and not mapped.is_global):
                raise CallbackConfigurationError(
                    "Callback resolved to a non-public address"
                )

    async def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        *,
        production: bool,
    ) -> int:
        try:
            # The outer deadline deliberately includes DNS resolution.  The
            # HTTP client's own timeout starts later and cannot bound a stuck
            # resolver by itself.
            async with asyncio.timeout(self.timeout_seconds):
                parsed = urlsplit(url)
                connector: aiohttp.BaseConnector | None = None
                if production:
                    host = parsed.hostname
                    if host is None:
                        raise CallbackConfigurationError(
                            "Callback URL is invalid"
                        )
                    addresses = await self._resolve(host, parsed.port or 443)
                    self._assert_public(addresses)
                    connector = aiohttp.TCPConnector(
                        resolver=_PinnedResolver(host, addresses),
                        use_dns_cache=False,
                        limit=1,
                    )
                timeout = aiohttp.ClientTimeout(
                    total=self.timeout_seconds,
                    connect=min(self.timeout_seconds, 5),
                    sock_read=min(self.timeout_seconds, 5),
                )
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout
                ) as session:
                    async with session.post(
                        url,
                        data=body,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        return response.status
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise CallbackTransportError(
                "Callback request failed"
            ) from exc


class CallbackDispatcher:
    def __init__(
        self,
        repository: CallbackRepository,
        policy: CallbackPolicy,
        *,
        transport: CallbackTransport | None = None,
        max_attempts: int = 8,
        base_delay_seconds: float = 5,
        max_delay_seconds: float = 3600,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("callback retry delays must be positive")
        self.repository = repository
        self.policy = policy
        self.transport = transport or AioHttpCallbackTransport()
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _retry_delay(self, attempts: int) -> timedelta:
        seconds = min(
            self.base_delay_seconds * (2 ** max(attempts - 1, 0)),
            self.max_delay_seconds,
        )
        return timedelta(seconds=seconds)

    async def dispatch_once(self, *, batch_size: int = 50) -> int:
        delivered = 0
        attempted_ids: set[UUID] = set()
        for _ in range(max(batch_size, 0)):
            # Claim only the item that is about to be sent. Claiming a whole
            # batch before serial HTTP calls lets a slow first endpoint consume
            # the visibility lease of every untouched item behind it.
            claims = await self.repository.claim_callback_deliveries(
                batch_size=1,
                exclude_ids=attempted_ids,
            )
            if not claims:
                break
            claim = claims[0]
            delivery = claim.delivery
            attempted_ids.add(delivery.id)
            response_status: int | None = None
            try:
                route = self.policy.route_for(delivery)
                body = serialize_callback_event(delivery)
                timestamp = str(int(self.now().timestamp()))
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "ai-video-relay-callback/1.0",
                    "X-Relay-Event-ID": str(delivery.id),
                    "X-Relay-Timestamp": timestamp,
                    "X-Request-ID": delivery.request_id,
                    "X-Relay-Signature": sign_callback(
                        route.signing_secret,
                        timestamp=timestamp,
                        event_id=delivery.id,
                        body=body,
                    ),
                }
                response_status = await self.transport.post(
                    route.url,
                    body,
                    headers,
                    production=self.policy.production,
                )
                if 200 <= response_status < 300:
                    completed = await self.repository.mark_callback_delivered(
                        delivery.id,
                        token=claim.token,
                        response_status=response_status,
                    )
                    if completed:
                        delivered += 1
                    continue
                error = f"HTTP {response_status}"
            except CallbackConfigurationError:
                error = "Callback configuration rejected delivery"
            except CallbackTransportError:
                error = "Callback transport failed"
            except Exception:
                # Error values can contain URLs, credentials, or response data.
                error = "Callback delivery failed"

            dead_letter = delivery.attempts >= self.max_attempts
            await self.repository.release_callback_delivery(
                delivery.id,
                token=claim.token,
                error=error,
                retry_delay=self._retry_delay(delivery.attempts),
                dead_letter=dead_letter,
                response_status=response_status,
            )
        return delivered
