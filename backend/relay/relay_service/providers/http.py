from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json as json_module
from collections.abc import Mapping
from typing import Any, Protocol

import aiohttp


class JsonTransportError(RuntimeError):
    """Sanitized transport failure safe to surface through provider adapters."""

    def __init__(self, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class JsonHttpResponse:
    status: int
    body: dict[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


class JsonHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any] | None = None,
    ) -> JsonHttpResponse: ...

    async def probe(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> int: ...

    async def close(self) -> None: ...


class AioHttpJsonTransport:
    """Small JSON-only client that never includes response bodies in errors."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        max_response_bytes: int = 2 * 1024 * 1024,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._session = session
        self._owns_session = session is None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any] | None = None,
    ) -> JsonHttpResponse:
        session = self._session
        if session is None:
            session = aiohttp.ClientSession(timeout=self._timeout)
            self._session = session
        try:
            async with session.request(
                method,
                url,
                headers=dict(headers),
                json=dict(json) if json is not None else None,
                allow_redirects=False,
            ) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        declared_length = -1
                    if declared_length > self._max_response_bytes:
                        raise JsonTransportError(
                            "Provider JSON response exceeded the size limit",
                            outcome_unknown=method.upper() == "POST",
                        )
                collected = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    collected.extend(chunk)
                    if len(collected) > self._max_response_bytes:
                        raise JsonTransportError(
                            "Provider JSON response exceeded the size limit",
                            outcome_unknown=method.upper() == "POST",
                        )
                raw = bytes(collected)
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                try:
                    parsed = json_module_loads(raw)
                except (UnicodeDecodeError, json_module.JSONDecodeError) as exc:
                    raise JsonTransportError(
                        "Provider returned an invalid JSON response",
                        outcome_unknown=method.upper() == "POST",
                    ) from None
                if not isinstance(parsed, dict):
                    raise JsonTransportError(
                        "Provider returned a non-object JSON response",
                        outcome_unknown=method.upper() == "POST",
                    )
                return JsonHttpResponse(
                    status=response.status,
                    body=parsed,
                    headers=response_headers,
                )
        except JsonTransportError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise JsonTransportError(
                "Provider request could not be completed",
                outcome_unknown=method.upper() == "POST",
            ) from None

    async def probe(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> int:
        session = self._session
        if session is None:
            session = aiohttp.ClientSession(timeout=self._timeout)
            self._session = session
        try:
            async with session.request(
                method,
                url,
                headers=dict(headers),
                allow_redirects=False,
            ) as response:
                return response.status
        except (asyncio.TimeoutError, aiohttp.ClientError):
            raise JsonTransportError(
                "Provider connectivity probe could not be completed"
            ) from None

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None


def json_module_loads(raw: bytes) -> Any:
    """Keep the public ``json=`` request argument without shadowing loads."""

    return json_module.loads(raw.decode("utf-8"))
