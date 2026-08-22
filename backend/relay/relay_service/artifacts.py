from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID


class ArtifactConfigurationError(RuntimeError):
    pass


class ArtifactStoreError(RuntimeError):
    pass


class ArtifactNotFoundError(ArtifactStoreError):
    pass


class ArtifactSignatureError(ArtifactStoreError):
    pass


@dataclass(frozen=True)
class StoredObject:
    size_bytes: int
    sha256: str


@dataclass
class OpenedArtifact:
    content: BinaryIO
    content_type: str
    size_bytes: int
    sha256: str

    def close(self) -> None:
        self.content.close()


@runtime_checkable
class ArtifactStore(Protocol):
    kind: str
    persistent: bool

    async def put_content(
        self,
        object_key: str,
        content: BinaryIO,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject: ...

    async def signed_download_url(
        self, object_key: str, *, expires_seconds: int = 300
    ) -> str: ...

    async def healthcheck(self) -> bool: ...


def validate_output_key(object_key: str) -> None:
    parts = object_key.split("/")
    if (
        len(parts) != 4
        or parts[0] != "outputs"
        or len(object_key) > 160
        or "\\" in object_key
    ):
        raise ArtifactStoreError("Artifact object key is outside outputs namespace")
    try:
        # Output keys are generated internally from canonical UUIDs. Requiring the
        # exact representation also excludes dot segments, encoded separators,
        # control characters, drive prefixes, and other filesystem metacharacters.
        if any(str(UUID(part)) != part for part in parts[1:]):
            raise ValueError
    except (ValueError, AttributeError):
        raise ArtifactStoreError(
            "Artifact object key is outside outputs namespace"
        ) from None


class FilesystemArtifactStore:
    """Development-only shared artifact store with signed relay downloads."""

    kind = "filesystem"
    persistent = True
    download_path = "/v1/artifacts/download"
    _content_type_pattern = re.compile(
        r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
    )

    def __init__(
        self,
        root: str | os.PathLike[str],
        public_base_url: str,
        signing_secret: str | bytes,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        configured_root = Path(root)
        if not configured_root.is_absolute():
            raise ArtifactConfigurationError(
                "Filesystem artifact root must be an absolute path"
            )
        self.root = configured_root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ArtifactConfigurationError(
                "Filesystem artifact root must be a directory"
            )

        parsed_base = urlsplit(public_base_url)
        if (
            parsed_base.scheme not in {"http", "https"}
            or not parsed_base.hostname
            or parsed_base.username
            or parsed_base.password
            or parsed_base.query
            or parsed_base.fragment
        ):
            raise ArtifactConfigurationError(
                "Artifact public base URL must be credential-free HTTP(S)"
            )
        self.public_base_url = public_base_url.rstrip("/")

        secret = (
            signing_secret.encode("utf-8")
            if isinstance(signing_secret, str)
            else signing_secret
        )
        if len(secret) < 32:
            raise ArtifactConfigurationError(
                "Artifact signing secret must contain at least 32 bytes"
            )
        self._signing_secret = secret
        self._clock = clock

    def _paths(
        self, object_key: str, *, create_parents: bool = False
    ) -> tuple[Path, Path]:
        validate_output_key(object_key)
        parts = object_key.split("/")
        current = self.root
        for part in parts[:-1]:
            candidate = current / part
            if candidate.exists():
                if candidate.is_symlink() or not candidate.is_dir():
                    raise ArtifactStoreError(
                        "Artifact storage path is invalid"
                    )
            elif create_parents:
                try:
                    candidate.mkdir(mode=0o700)
                except FileExistsError:
                    # Another process may have created the same directory.
                    if candidate.is_symlink() or not candidate.is_dir():
                        raise ArtifactStoreError(
                            "Artifact storage path is invalid"
                        ) from None
            else:
                raise ArtifactNotFoundError("Artifact does not exist")
            current = candidate
            if current.is_symlink():
                raise ArtifactStoreError(
                    "Symbolic links are forbidden in artifact storage"
                )
            try:
                current.resolve(strict=True).relative_to(self.root)
            except (FileNotFoundError, ValueError):
                raise ArtifactStoreError(
                    "Artifact object key is outside configured storage"
                ) from None
        parent = current
        artifact_path = parent / parts[-1]
        metadata_path = parent / f"{parts[-1]}.meta.json"
        for candidate in (artifact_path, metadata_path):
            if candidate.is_symlink():
                raise ArtifactStoreError(
                    "Symbolic links are forbidden in artifact storage"
                )
        return artifact_path, metadata_path

    @staticmethod
    def _stream_integrity(content: BinaryIO, output: BinaryIO) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        content.seek(0)
        while True:
            chunk = content.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            output.write(chunk)
        return size, digest.hexdigest()

    @staticmethod
    def _file_integrity(content: BinaryIO) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        content.seek(0)
        while True:
            chunk = content.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        content.seek(0)
        return size, digest.hexdigest()

    @staticmethod
    def _open_temp(parent: Path, suffix: str) -> tuple[Path, BinaryIO]:
        while True:
            path = parent / f".artifact-{secrets.token_hex(16)}{suffix}"
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                return path, os.fdopen(descriptor, "wb")
            except FileExistsError:
                continue

    def _validate_existing(
        self, artifact_path: Path, *, size_bytes: int, sha256: str
    ) -> None:
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ArtifactStoreError("Artifact storage target is invalid")
        with artifact_path.open("rb") as existing:
            actual_size, actual_hash = self._file_integrity(existing)
        if actual_size != size_bytes or actual_hash != sha256:
            raise ArtifactStoreError(
                "Existing artifact conflicts with requested content"
            )

    def _put_content_sync(
        self,
        object_key: str,
        content: BinaryIO,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject:
        if (
            not self._content_type_pattern.fullmatch(content_type)
            or len(content_type) > 127
        ):
            raise ArtifactStoreError("Artifact content type is invalid")
        if size_bytes < 0 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ArtifactStoreError("Artifact integrity metadata is invalid")
        artifact_path, metadata_path = self._paths(
            object_key, create_parents=True
        )
        temp_path, temp_content = self._open_temp(
            artifact_path.parent, ".content"
        )
        try:
            with temp_content:
                actual_size, actual_hash = self._stream_integrity(
                    content, temp_content
                )
                temp_content.flush()
                os.fsync(temp_content.fileno())
            if actual_size != size_bytes or actual_hash != sha256:
                raise ArtifactStoreError("Artifact integrity check failed")

            try:
                # Hard-linking publishes the fully flushed file atomically without
                # overwriting a conflicting object created by another process.
                os.link(temp_path, artifact_path)
            except FileExistsError:
                self._validate_existing(
                    artifact_path,
                    size_bytes=size_bytes,
                    sha256=sha256,
                )

            metadata = json.dumps(
                {
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            metadata_temp_path, metadata_temp = self._open_temp(
                metadata_path.parent, ".metadata"
            )
            try:
                with metadata_temp:
                    metadata_temp.write(metadata)
                    metadata_temp.flush()
                    os.fsync(metadata_temp.fileno())
                os.replace(metadata_temp_path, metadata_path)
            finally:
                metadata_temp_path.unlink(missing_ok=True)
        finally:
            temp_path.unlink(missing_ok=True)
        return StoredObject(size_bytes=size_bytes, sha256=sha256)

    async def put_content(
        self,
        object_key: str,
        content: BinaryIO,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject:
        return await asyncio.to_thread(
            self._put_content_sync,
            object_key,
            content,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )

    def _signature(self, object_key: str, expires: int) -> str:
        message = f"v1\n{object_key}\n{expires}".encode("utf-8")
        return hmac.new(
            self._signing_secret, message, hashlib.sha256
        ).hexdigest()

    async def signed_download_url(
        self, object_key: str, *, expires_seconds: int = 300
    ) -> str:
        artifact_path, metadata_path = self._paths(object_key)
        if not 1 <= expires_seconds <= 3600:
            raise ArtifactStoreError("Signed URL expiry is outside allowed range")
        if not artifact_path.is_file() or not metadata_path.is_file():
            raise ArtifactNotFoundError("Artifact does not exist")
        expires = int(self._clock()) + expires_seconds
        query = urlencode(
            {
                "key": object_key,
                "expires": expires,
                "signature": self._signature(object_key, expires),
            }
        )
        return f"{self.public_base_url}{self.download_path}?{query}"

    def _open_signed_sync(
        self, object_key: str, expires: int, signature: str
    ) -> OpenedArtifact:
        validate_output_key(object_key)
        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ArtifactSignatureError("Artifact download signature is invalid")
        now = int(self._clock())
        if expires <= now:
            raise ArtifactSignatureError("Artifact download signature has expired")
        # Refuse arbitrarily long-lived tokens even if a valid signer is misused.
        if expires - now > 3600:
            raise ArtifactSignatureError("Artifact download expiry is invalid")
        expected = self._signature(object_key, expires)
        if not hmac.compare_digest(expected, signature):
            raise ArtifactSignatureError("Artifact download signature is invalid")

        artifact_path, metadata_path = self._paths(object_key)
        if not artifact_path.is_file() or not metadata_path.is_file():
            raise ArtifactNotFoundError("Artifact does not exist")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            content_type = metadata["content_type"]
            size_bytes = metadata["size_bytes"]
            sha256 = metadata["sha256"]
            if (
                not isinstance(content_type, str)
                or not self._content_type_pattern.fullmatch(content_type)
                or not isinstance(size_bytes, int)
                or size_bytes < 0
                or not isinstance(sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            ):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ArtifactStoreError("Artifact metadata is invalid") from None

        content = artifact_path.open("rb")
        try:
            actual_size, actual_hash = self._file_integrity(content)
            if actual_size != size_bytes or actual_hash != sha256:
                raise ArtifactStoreError("Stored artifact integrity check failed")
            return OpenedArtifact(
                content=content,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except Exception:
            content.close()
            raise

    async def open_signed(
        self, object_key: str, expires: int, signature: str
    ) -> OpenedArtifact:
        return await asyncio.to_thread(
            self._open_signed_sync, object_key, expires, signature
        )

    async def healthcheck(self) -> bool:
        return await asyncio.to_thread(
            lambda: self.root.is_dir() and os.access(self.root, os.R_OK | os.W_OK)
        )


class InMemoryArtifactStore:
    """Deterministic test store. It never exposes a public HTTP URL."""

    kind = "memory"
    persistent = False

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.put_counts: dict[str, int] = {}

    async def put_content(
        self,
        object_key: str,
        content: BinaryIO,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject:
        validate_output_key(object_key)
        content.seek(0)
        data = content.read()
        actual_hash = hashlib.sha256(data).hexdigest()
        if len(data) != size_bytes or actual_hash != sha256:
            raise ArtifactStoreError("Artifact integrity check failed")
        self.objects[object_key] = data
        self.content_types[object_key] = content_type
        self.put_counts[object_key] = self.put_counts.get(object_key, 0) + 1
        return StoredObject(size_bytes=len(data), sha256=actual_hash)

    async def signed_download_url(
        self, object_key: str, *, expires_seconds: int = 300
    ) -> str:
        validate_output_key(object_key)
        if object_key not in self.objects:
            raise ArtifactStoreError("Artifact does not exist")
        return f"memory-signed://{object_key}?expires={expires_seconds}"

    async def healthcheck(self) -> bool:
        return True


class HuaweiObsArtifactStore:
    """Huawei OBS adapter using the official optional Python SDK."""

    kind = "huawei-obs"
    persistent = True

    def __init__(
        self,
        client,
        bucket: str,
        *,
        endpoint_host: str | None = None,
    ) -> None:
        self._client = client
        self.bucket = bucket
        self._endpoint_host = endpoint_host

    @classmethod
    def from_environment(cls) -> "HuaweiObsArtifactStore":
        names = {
            "access_key_id": "HUAWEI_OBS_ACCESS_KEY_ID",
            "secret_access_key": "HUAWEI_OBS_SECRET_ACCESS_KEY",
            "server": "HUAWEI_OBS_ENDPOINT",
            "bucket": "HUAWEI_OBS_BUCKET",
        }
        values = {key: os.getenv(name) for key, name in names.items()}
        missing = [
            names[key] for key, value in values.items() if not value
        ]
        if missing:
            raise ArtifactConfigurationError(
                "Huawei OBS configuration is incomplete: "
                + ", ".join(missing)
            )
        endpoint = urlsplit(values["server"])  # type: ignore[arg-type]
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
        ):
            raise ArtifactConfigurationError(
                "Huawei OBS endpoint must be credential-free HTTPS"
            )
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
            values["bucket"] or "",
        ):
            raise ArtifactConfigurationError("Huawei OBS bucket name is invalid")
        try:
            from obs import ObsClient
        except ImportError as exc:
            raise ArtifactConfigurationError(
                "Huawei OBS support requires the optional 'obs' dependency"
            ) from exc
        client = ObsClient(
            access_key_id=values["access_key_id"],
            secret_access_key=values["secret_access_key"],
            server=values["server"],
        )
        return cls(  # type: ignore[arg-type]
            client,
            values["bucket"],
            endpoint_host=(endpoint.hostname or "").lower().rstrip("."),
        )

    @staticmethod
    def _metadata_value(metadata: object, name: str) -> str | None:
        if not isinstance(metadata, dict):
            return None
        for key, value in metadata.items():
            normalized = str(key).strip().lower()
            for prefix in ("x-obs-meta-", "x-amz-meta-"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :]
            if normalized == name:
                return str(value)
        return None

    def _validated_signed_object_url(self, signed_url: str, object_key: str) -> str:
        parsed = urlsplit(signed_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.fragment
            or not parsed.query
        ):
            raise ArtifactStoreError("Huawei OBS returned an unsafe signed URL")
        if self._endpoint_host:
            signed_host = parsed.hostname.lower().rstrip(".")
            expected_virtual_host = f"{self.bucket}.{self._endpoint_host}"
            expected_object_path = f"/{object_key}"
            if signed_host == expected_virtual_host:
                path_matches = parsed.path == expected_object_path
            elif signed_host == self._endpoint_host:
                path_matches = parsed.path in {
                    expected_object_path,
                    f"/{self.bucket}{expected_object_path}",
                }
            else:
                path_matches = False
            if not path_matches:
                raise ArtifactStoreError(
                    "Huawei OBS signed URL does not match the configured object"
                )
        return signed_url

    def _signed_head_metadata(self, object_key: str) -> dict[str, str]:
        try:
            result = self._client.createSignedUrl(
                method="HEAD",
                bucketName=self.bucket,
                objectKey=object_key,
                expires=60,
            )
            signed_url = self._validated_signed_object_url(
                getattr(result, "signedUrl", "") or "",
                object_key,
            )
            with urlopen(Request(signed_url, method="HEAD"), timeout=15) as response:
                return {str(key): str(value) for key, value in response.headers.items()}
        except ArtifactStoreError:
            raise
        except Exception as exc:
            raise ArtifactStoreError(
                "Huawei OBS object metadata verification failed"
            ) from exc

    async def put_content(
        self,
        object_key: str,
        content: BinaryIO,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredObject:
        validate_output_key(object_key)
        content.seek(0)

        def upload():
            try:
                from obs import PutObjectHeader
            except ImportError as exc:
                raise ArtifactConfigurationError(
                    "Huawei OBS SDK is not installed"
                ) from exc
            headers = PutObjectHeader(
                contentType=content_type,
                # Do not rely only on a bucket's current default ACL. The
                # upload request itself must never make generated media public.
                acl="private",
            )
            return self._client.putContent(
                self.bucket,
                object_key,
                content=content,
                metadata={"sha256": sha256},
                headers=headers,
                autoClose=False,
            )

        try:
            response = await asyncio.to_thread(upload)
        except ArtifactConfigurationError:
            raise
        except Exception as exc:
            raise ArtifactStoreError("Huawei OBS upload request failed") from exc
        if not isinstance(getattr(response, "status", None), int) or response.status >= 300:
            # OBS error messages can include request details; expose only a code.
            raise ArtifactStoreError(
                "Huawei OBS upload did not return a successful status"
            )

        # A successful PUT response alone is not enough to mark a generation as
        # durable. Verify the object through OBS HEAD before the transfer worker
        # can publish it or settle customer billing.
        def head():
            return self._client.getObjectMetadata(self.bucket, object_key)

        try:
            metadata_response = await asyncio.to_thread(head)
        except Exception as exc:
            raise ArtifactStoreError(
                "Huawei OBS object verification request failed"
            ) from exc
        if (
            not isinstance(getattr(metadata_response, "status", None), int)
            or metadata_response.status >= 300
        ):
            raise ArtifactStoreError(
                "Huawei OBS object verification did not return a successful status"
            )
        body = getattr(metadata_response, "body", None)
        try:
            stored_size = int(getattr(body, "contentLength"))
        except (AttributeError, TypeError, ValueError):
            raise ArtifactStoreError(
                "Huawei OBS object verification metadata is invalid"
            ) from None
        stored_content_type = getattr(body, "contentType", None)
        stored_sha256 = None
        body_metadata = getattr(body, "metadata", None)
        stored_sha256 = self._metadata_value(body_metadata, "sha256")
        for item in getattr(metadata_response, "header", None) or []:
            if (
                isinstance(item, (tuple, list))
                and len(item) == 2
                and str(item[0]).lower() == "x-obs-meta-sha256"
            ):
                stored_sha256 = str(item[1])
                break
        # esdk-obs-python 3.26.x omits custom x-obs-meta-* headers from
        # getObjectMetadata(). Fall back to a short-lived, host/path-bound
        # signed HEAD so a large artifact is not downloaded just to verify it.
        if stored_sha256 is None:
            signed_head = await asyncio.to_thread(
                self._signed_head_metadata,
                object_key,
            )
            stored_sha256 = self._metadata_value(signed_head, "sha256")
        if (
            stored_size != size_bytes
            or stored_content_type != content_type
            or stored_sha256 != sha256
        ):
            raise ArtifactStoreError(
                "Huawei OBS object verification did not match uploaded content"
            )
        return StoredObject(size_bytes=size_bytes, sha256=sha256)

    async def signed_download_url(
        self, object_key: str, *, expires_seconds: int = 300
    ) -> str:
        validate_output_key(object_key)
        if not 1 <= expires_seconds <= 3600:
            raise ArtifactStoreError("Signed URL expiry is outside allowed range")

        def sign():
            return self._client.createSignedUrl(
                method="GET",
                bucketName=self.bucket,
                objectKey=object_key,
                expires=expires_seconds,
            )

        result = await asyncio.to_thread(sign)
        signed_url = getattr(result, "signedUrl", None)
        if not signed_url:
            raise ArtifactStoreError("Huawei OBS could not create a signed URL")
        return self._validated_signed_object_url(signed_url, object_key)

    async def healthcheck(self) -> bool:
        try:
            response = await asyncio.to_thread(
                self._client.getBucketMetadata, self.bucket
            )
            return response.status < 300
        except Exception:
            return False

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            await asyncio.to_thread(close)
