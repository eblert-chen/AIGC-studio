from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time
from typing import Callable, Protocol
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from .config import Settings


class InputAssetStorageError(RuntimeError):
    """Storage failure whose message is safe to expose to application logs."""


class InputAssetIntegrityError(InputAssetStorageError):
    """A stored object's bytes or immutable metadata no longer match its index."""


class InputAssetSignatureError(ValueError):
    pass


@dataclass(frozen=True)
class StoredInputAsset:
    size_bytes: int
    sha256: str


class InputAssetStore(Protocol):
    kind: str

    def put_file(
        self,
        object_key: str,
        source_path: Path,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredInputAsset: ...

    def signed_url(
        self,
        object_key: str,
        *,
        expires_seconds: int,
        original_filename: str,
        disposition: str,
    ) -> str | None: ...

    def local_path(self, object_key: str) -> Path | None: ...

    def verify_object(
        self,
        object_key: str,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredInputAsset: ...


_OBJECT_KEY_PATTERN = re.compile(
    r"inputs/[0-9a-f-]{36}/[0-9a-f-]{36}"
)


def validate_input_object_key(object_key: str) -> None:
    if not _OBJECT_KEY_PATTERN.fullmatch(object_key):
        raise InputAssetStorageError("Input asset object key is invalid")


_SHOWCASE_OBJECT_KEY_PATTERN = re.compile(r"showcase/media/[0-9a-f]{64}")


def validate_showcase_object_key(object_key: str) -> None:
    if not _SHOWCASE_OBJECT_KEY_PATTERN.fullmatch(object_key):
        raise InputAssetStorageError("Showcase media object key is invalid")


class FilesystemInputAssetStore:
    kind = "filesystem"

    def __init__(
        self,
        root: str,
        *,
        object_key_validator: Callable[[str], None] = validate_input_object_key,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self._validate_object_key = object_key_validator
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def with_object_key_validator(
        self,
        object_key_validator: Callable[[str], None],
    ) -> "FilesystemInputAssetStore":
        """Return an independently scoped view over the same storage root."""

        return type(self)(
            str(self.root),
            object_key_validator=object_key_validator,
        )

    def _path(self, object_key: str) -> Path:
        self._validate_object_key(object_key)
        relative = PurePosixPath(object_key)
        path = (self.root / Path(*relative.parts)).resolve()
        if self.root != path and self.root not in path.parents:
            raise InputAssetStorageError("Input asset path escaped storage root")
        return path

    @staticmethod
    def _file_integrity(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def put_file(
        self,
        object_key: str,
        source_path: Path,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredInputAsset:
        del content_type
        actual_size, actual_sha256 = self._file_integrity(source_path)
        if actual_size != size_bytes or actual_sha256 != sha256:
            raise InputAssetStorageError("Input asset integrity check failed")
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.upload")
        try:
            with source_path.open("rb") as source, temporary.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            raise InputAssetStorageError("Filesystem input asset upload failed") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return StoredInputAsset(size_bytes=actual_size, sha256=actual_sha256)

    def signed_url(self, *_, **__) -> None:
        return None

    def local_path(self, object_key: str) -> Path | None:
        path = self._path(object_key)
        if not path.is_file():
            raise InputAssetStorageError("Input asset object does not exist")
        return path

    def verify_object(
        self,
        object_key: str,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredInputAsset:
        del content_type
        path = self.local_path(object_key)
        assert path is not None
        try:
            actual_size, actual_sha256 = self._file_integrity(path)
        except OSError as exc:
            raise InputAssetStorageError(
                "Filesystem input asset verification failed"
            ) from exc
        if actual_size != size_bytes or actual_sha256 != sha256:
            raise InputAssetIntegrityError(
                "Filesystem input asset integrity does not match its index"
            )
        return StoredInputAsset(size_bytes=actual_size, sha256=actual_sha256)


class HuaweiObsInputAssetStore:
    kind = "huawei_obs"

    def __init__(
        self,
        client,
        bucket: str,
        *,
        endpoint_host: str | None = None,
        object_key_validator: Callable[[str], None] = validate_input_object_key,
    ) -> None:
        self._client = client
        self.bucket = bucket
        self._endpoint_host = endpoint_host
        self._validate_object_key = object_key_validator

    def with_object_key_validator(
        self,
        object_key_validator: Callable[[str], None],
    ) -> "HuaweiObsInputAssetStore":
        """Return an independently scoped view over the same private OBS client."""

        return type(self)(
            self._client,
            self.bucket,
            endpoint_host=self._endpoint_host,
            object_key_validator=object_key_validator,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        object_key_validator: Callable[[str], None] = validate_input_object_key,
    ) -> "HuaweiObsInputAssetStore":
        values = {
            "access_key_id": settings.huawei_obs_access_key_id,
            "secret_access_key": settings.huawei_obs_secret_access_key,
            "security_token": settings.huawei_obs_security_token,
            "server": settings.huawei_obs_endpoint,
            "bucket": settings.huawei_obs_bucket,
        }
        required_names = {
            "access_key_id",
            "secret_access_key",
            "server",
            "bucket",
        }
        missing = [
            key
            for key, value in values.items()
            if key in required_names and not value
        ]
        if missing:
            raise InputAssetStorageError(
                "Huawei OBS input asset configuration is incomplete: "
                + ", ".join(sorted(missing))
            )
        endpoint = urlsplit(values["server"] or "")
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or endpoint.port
        ):
            raise InputAssetStorageError(
                "Huawei OBS endpoint must be credential-free HTTPS"
            )
        bucket = values["bucket"] or ""
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise InputAssetStorageError("Huawei OBS bucket name is invalid")
        try:
            from obs import ObsClient
        except ImportError as exc:
            raise InputAssetStorageError(
                "Huawei OBS input assets require the optional 'obs' dependency"
            ) from exc
        client_kwargs = {
            "access_key_id": values["access_key_id"],
            "secret_access_key": values["secret_access_key"],
            "server": values["server"],
        }
        if values["security_token"]:
            client_kwargs["security_token"] = values["security_token"]
        client = ObsClient(
            **client_kwargs,
        )
        return cls(
            client,
            bucket,
            endpoint_host=(endpoint.hostname or "").lower().rstrip("."),
            object_key_validator=object_key_validator,
        )

    @staticmethod
    def _file_integrity(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise InputAssetStorageError(
                "Input asset could not be read before OBS upload"
            ) from exc
        return size, digest.hexdigest()

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
            raise InputAssetStorageError(
                "Huawei OBS returned an unsafe input asset signed URL"
            )
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
                raise InputAssetStorageError(
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
        except InputAssetStorageError:
            raise
        except Exception as exc:
            raise InputAssetStorageError(
                "Huawei OBS input asset metadata verification failed"
            ) from exc

    def put_file(
        self,
        object_key: str,
        source_path: Path,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredInputAsset:
        self._validate_object_key(object_key)
        actual_size, actual_sha256 = self._file_integrity(source_path)
        if actual_size != size_bytes or actual_sha256 != sha256:
            raise InputAssetStorageError("Input asset integrity check failed")
        try:
            from obs import PutObjectHeader
        except ImportError as exc:
            raise InputAssetStorageError(
                "Huawei OBS SDK is not installed"
            ) from exc
        headers = PutObjectHeader(
            contentType=content_type,
            acl="private",
            cacheControl="private, no-store",
        )
        metadata = {
            "sha256": sha256,
            "size-bytes": str(size_bytes),
        }
        try:
            response = self._client.putFile(
                self.bucket,
                object_key,
                str(source_path),
                metadata,
                headers,
            )
        except Exception as exc:
            raise InputAssetStorageError("Huawei OBS input asset upload failed") from exc
        if getattr(response, "status", 500) >= 300:
            raise InputAssetStorageError(
                "Huawei OBS input asset upload was rejected"
            )
        self.verify_object(
            object_key,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        return StoredInputAsset(
            size_bytes=actual_size,
            sha256=actual_sha256,
        )

    def verify_object(
        self,
        object_key: str,
        *,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> StoredInputAsset:
        self._validate_object_key(object_key)
        try:
            remote = self._client.getObjectMetadata(self.bucket, object_key)
        except Exception as exc:
            raise InputAssetStorageError(
                "Huawei OBS input asset verification failed"
            ) from exc
        if getattr(remote, "status", 500) >= 300:
            raise InputAssetStorageError(
                "Huawei OBS input asset verification was rejected"
            )
        body = getattr(remote, "body", None)
        remote_size = getattr(body, "contentLength", None)
        remote_content_type = getattr(body, "contentType", None)
        remote_metadata = getattr(body, "metadata", None)
        remote_sha256 = self._metadata_value(remote_metadata, "sha256")
        remote_size_metadata = self._metadata_value(remote_metadata, "size-bytes")
        # esdk-obs-python 3.26.x discards custom x-obs-meta-* headers from
        # getObjectMetadata(). Use a short-lived, host/path-bound signed HEAD
        # only when the SDK response omits them; this verifies the remote
        # object without downloading a potentially large input asset.
        if remote_sha256 is None or remote_size_metadata is None:
            signed_head = self._signed_head_metadata(object_key)
            remote_sha256 = self._metadata_value(signed_head, "sha256")
            remote_size_metadata = self._metadata_value(signed_head, "size-bytes")
        if (
            remote_size != size_bytes
            or remote_content_type != content_type
            or remote_sha256 != sha256
            or remote_size_metadata != str(size_bytes)
        ):
            raise InputAssetIntegrityError(
                "Huawei OBS input asset verification did not match its immutable index"
            )
        return StoredInputAsset(size_bytes=size_bytes, sha256=sha256)

    def signed_url(
        self,
        object_key: str,
        *,
        expires_seconds: int,
        original_filename: str,
        disposition: str,
    ) -> str:
        self._validate_object_key(object_key)
        if disposition not in {"inline", "attachment"}:
            raise InputAssetStorageError("Input asset disposition is invalid")
        if not 1 <= expires_seconds <= 3600:
            raise InputAssetStorageError("Signed URL expiry is outside allowed range")
        content_disposition = (
            f"{disposition}; filename*=UTF-8''{quote(original_filename)}"
        )
        try:
            result = self._client.createSignedUrl(
                method="GET",
                bucketName=self.bucket,
                objectKey=object_key,
                expires=expires_seconds,
                queryParams={
                    "response-content-disposition": content_disposition,
                },
            )
        except Exception as exc:
            raise InputAssetStorageError(
                "Huawei OBS could not create an input asset signed URL"
            ) from exc
        return self._validated_signed_object_url(
            getattr(result, "signedUrl", "") or "",
            object_key,
        )

    def local_path(self, object_key: str) -> None:
        self._validate_object_key(object_key)
        return None

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            close()


class FilesystemInputAssetSigner:
    def __init__(self, *, public_base_url: str, signing_secret: str) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        if not self.public_base_url:
            raise InputAssetStorageError(
                "Input asset public base URL must not be empty"
            )
        if not signing_secret:
            raise InputAssetStorageError(
                "Input asset signing secret must not be empty"
            )
        self._secret = signing_secret.encode("utf-8")

    def _signature(
        self, asset_id: str, expires: int, disposition: str
    ) -> str:
        message = f"v1\n{asset_id}\n{expires}\n{disposition}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def sign(
        self,
        asset_id: str,
        *,
        expires_seconds: int,
        disposition: str,
    ) -> str:
        if disposition not in {"inline", "attachment"}:
            raise InputAssetStorageError("Input asset disposition is invalid")
        if not 1 <= expires_seconds <= 3600:
            raise InputAssetStorageError("Signed URL expiry is outside allowed range")
        expires = int(time.time()) + expires_seconds
        query = urlencode(
            {
                "expires": expires,
                "disposition": disposition,
                "signature": self._signature(asset_id, expires, disposition),
            }
        )
        return (
            f"{self.public_base_url}/api/v1/input-assets/{asset_id}/content?{query}"
        )

    def verify(
        self,
        asset_id: str,
        *,
        expires: int,
        disposition: str,
        signature: str,
    ) -> None:
        if disposition not in {"inline", "attachment"}:
            raise InputAssetSignatureError("Input asset disposition is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise InputAssetSignatureError("Input asset signature is invalid")
        now = int(time.time())
        if expires <= now:
            raise InputAssetSignatureError("Input asset signature has expired")
        if expires - now > 3600:
            raise InputAssetSignatureError("Input asset expiry is invalid")
        expected = self._signature(asset_id, expires, disposition)
        if not hmac.compare_digest(expected, signature):
            raise InputAssetSignatureError("Input asset signature is invalid")


def build_input_asset_store(settings: Settings) -> InputAssetStore:
    if settings.input_asset_store == "filesystem":
        return FilesystemInputAssetStore(settings.input_asset_filesystem_root)
    if settings.input_asset_store == "huawei_obs":
        return HuaweiObsInputAssetStore.from_settings(settings)
    raise InputAssetStorageError("Unsupported input asset store")


def build_showcase_media_store(
    settings: Settings,
    *,
    base_store: InputAssetStore | None = None,
) -> InputAssetStore:
    """Use the configured Platform-controlled store with a public-media keyspace."""

    if isinstance(base_store, FilesystemInputAssetStore):
        return base_store.with_object_key_validator(validate_showcase_object_key)
    if isinstance(base_store, HuaweiObsInputAssetStore):
        return base_store.with_object_key_validator(validate_showcase_object_key)

    if settings.input_asset_store == "filesystem":
        return FilesystemInputAssetStore(
            settings.input_asset_filesystem_root,
            object_key_validator=validate_showcase_object_key,
        )
    if settings.input_asset_store == "huawei_obs":
        return HuaweiObsInputAssetStore.from_settings(
            settings,
            object_key_validator=validate_showcase_object_key,
        )
    raise InputAssetStorageError("Unsupported showcase media store")
