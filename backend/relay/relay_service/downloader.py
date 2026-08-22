from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import tempfile
from dataclasses import dataclass
from typing import BinaryIO, Callable, Protocol
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver


class ArtifactDownloadError(RuntimeError):
    pass


class ArtifactSecurityError(ArtifactDownloadError):
    pass


@dataclass(frozen=True)
class DownloadPolicy:
    max_bytes: int = 512 * 1024 * 1024
    timeout_seconds: float = 60
    allowed_content_types: frozenset[str] = frozenset(
        {
            "video/mp4",
            "video/webm",
            "image/jpeg",
            "image/png",
            "image/webp",
        }
    )


@dataclass
class DownloadedArtifact:
    content: BinaryIO
    content_type: str
    size_bytes: int
    sha256: str

    def close(self) -> None:
        self.content.close()


class ArtifactDownloader(Protocol):
    async def download(self, url: str) -> DownloadedArtifact: ...


class _PinnedResolver(AbstractResolver):
    def __init__(self, host: str, addresses: list[str]) -> None:
        self.host = host
        self.addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict]:
        if host != self.host:
            raise ArtifactSecurityError("Unexpected hostname during download")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6
                if ":" in address
                else socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
            for address in self.addresses
        ]

    async def close(self) -> None:
        return None


class SafeHttpsDownloader:
    """Bounded downloader with pinned public DNS and no redirects."""

    def __init__(
        self,
        policy: DownloadPolicy | None = None,
        *,
        resolve_host: Callable[[str], object] | None = None,
    ) -> None:
        self.policy = policy or DownloadPolicy()
        self._resolve_host_override = resolve_host

    async def _resolve(self, host: str) -> list[str]:
        if self._resolve_host_override:
            result = self._resolve_host_override(host)
            if asyncio.iscoroutine(result):
                result = await result
            return list(result)  # type: ignore[arg-type]
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            host, 443, type=socket.SOCK_STREAM
        )
        return sorted({record[4][0] for record in records})

    @staticmethod
    def _assert_public(addresses: list[str]) -> None:
        if not addresses:
            raise ArtifactSecurityError("Download hostname did not resolve")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            mapped = getattr(ip, "ipv4_mapped", None)
            if not ip.is_global or (mapped is not None and not mapped.is_global):
                raise ArtifactSecurityError(
                    "Download resolved to a non-public address"
                )

    @staticmethod
    def _validate_url(url: str) -> tuple[str, int]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ArtifactSecurityError(
                "Only credential-free HTTPS artifact URLs are allowed"
            )
        port = parsed.port or 443
        if port != 443:
            raise ArtifactSecurityError("Artifact URL must use HTTPS port 443")
        return parsed.hostname, port

    @staticmethod
    def _validate_status(status: int) -> None:
        if 300 <= status < 400:
            raise ArtifactSecurityError("Artifact redirects are forbidden")
        if status != 200:
            raise ArtifactDownloadError(
                f"Artifact download failed with status {status}"
            )

    def _assert_size(self, size: int) -> None:
        if size > self.policy.max_bytes:
            raise ArtifactDownloadError("Artifact exceeds maximum size")

    async def download(self, url: str) -> DownloadedArtifact:
        host, _ = self._validate_url(url)
        addresses = await self._resolve(host)
        self._assert_public(addresses)
        resolver = _PinnedResolver(host, addresses)
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            use_dns_cache=False,
            limit=1,
        )
        timeout = aiohttp.ClientTimeout(
            total=self.policy.timeout_seconds,
            connect=min(self.policy.timeout_seconds, 10),
            sock_read=min(self.policy.timeout_seconds, 30),
        )
        spool: BinaryIO = tempfile.SpooledTemporaryFile(
            max_size=1024 * 1024, mode="w+b"
        )
        try:
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout
            ) as session:
                async with session.get(url, allow_redirects=False) as response:
                    self._validate_status(response.status)
                    content_type = (
                        response.headers.get("Content-Type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type not in self.policy.allowed_content_types:
                        raise ArtifactDownloadError(
                            "Artifact MIME type is not allowed"
                        )
                    length = response.headers.get("Content-Length")
                    if length:
                        self._assert_size(int(length))
                    digest = hashlib.sha256()
                    size = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        size += len(chunk)
                        self._assert_size(size)
                        digest.update(chunk)
                        spool.write(chunk)
            spool.seek(0)
            return DownloadedArtifact(
                content=spool,
                content_type=content_type,
                size_bytes=size,
                sha256=digest.hexdigest(),
            )
        except Exception:
            spool.close()
            raise
