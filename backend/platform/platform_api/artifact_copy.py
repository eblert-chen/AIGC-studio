from __future__ import annotations

import hashlib
from typing import BinaryIO

import httpx

from .relay_client import RelayPermanentError, RelayTemporaryError


class HttpArtifactContentSource:
    """Stream one already-validated platform-controlled artifact into a file."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        allowed_hosts: set[str] | None = None,
    ) -> None:
        if allowed_hosts is not None:
            source_host = (httpx.URL(url).host or "").casefold().rstrip(".")
            normalized_hosts = {
                host.casefold().rstrip(".") for host in allowed_hosts
            }
            if source_host not in normalized_hosts:
                raise RelayPermanentError(
                    "Artifact content host is not an approved storage endpoint"
                )
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def copy_to(
        self,
        target: BinaryIO,
        *,
        max_bytes: int,
    ) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream("GET", self._url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise RelayTemporaryError(
                            "Artifact content is temporarily unavailable"
                        )
                    if response.status_code < 200 or response.status_code >= 300:
                        raise RelayPermanentError(
                            "Artifact content could not be read"
                        )
                    declared_length = response.headers.get("content-length")
                    if declared_length is not None:
                        try:
                            length = int(declared_length)
                        except ValueError:
                            raise RelayPermanentError(
                                "Artifact content length is invalid"
                            ) from None
                        if length < 1 or length > max_bytes:
                            raise RelayPermanentError(
                                "Artifact content length is outside the allowed range"
                            )
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        size_bytes += len(chunk)
                        if size_bytes > max_bytes:
                            raise RelayPermanentError(
                                "Artifact content exceeds the configured input limit"
                            )
                        digest.update(chunk)
                        target.write(chunk)
        except RelayTemporaryError:
            raise
        except RelayPermanentError:
            raise
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Artifact content download failed"
            ) from exc
        if size_bytes <= 0:
            raise RelayPermanentError("Artifact content is empty")
        return size_bytes, digest.hexdigest()
