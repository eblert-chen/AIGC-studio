from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Literal, Protocol
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..asset_storage import (
    FilesystemInputAssetSigner,
    InputAssetStorageError,
    InputAssetStore,
)
from ..models import (
    GenerationTask,
    InputAsset,
    InputAssetStatus,
    TaskInputAsset,
    TaskArtifact,
    TaskStatus,
    new_id,
)
from .errors import ConflictError, DomainError, NotFoundError

MediaType = Literal["image", "video", "audio"]


class ArtifactContentSource(Protocol):
    def copy_to(
        self,
        target: BinaryIO,
        *,
        max_bytes: int,
    ) -> tuple[int, str]: ...


_CONTENT_TYPE_TO_MEDIA_TYPE: dict[str, MediaType] = {
    "image/avif": "image",
    "image/gif": "image",
    "image/heic": "image",
    "image/heif": "image",
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "video/mpeg": "video",
    "video/quicktime": "video",
    "video/webm": "video",
    "audio/aac": "audio",
    "audio/flac": "audio",
    "audio/mp4": "audio",
    "audio/mpeg": "audio",
    "audio/ogg": "audio",
    "audio/wav": "audio",
    "audio/webm": "audio",
    "audio/x-m4a": "audio",
    "audio/x-wav": "audio",
}


def _has_iso_bmff_brand(header: bytes, brands: set[bytes] | None = None) -> bool:
    """Validate the ISO-BMFF file-type box used by MP4/AVIF/HEIF containers."""

    if len(header) < 12 or header[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(header[:4], "big")
    if box_size < 16 or box_size > len(header):
        return False
    file_type_box = header[:box_size]
    if brands is None:
        return True
    declared_brands = {file_type_box[8:12]}
    declared_brands.update(
        file_type_box[offset : offset + 4]
        for offset in range(16, len(file_type_box) - 3, 4)
    )
    return not declared_brands.isdisjoint(brands)


def _content_signature_matches(path: Path, content_type: str) -> bool:
    """Reject MIME spoofing before an upload becomes an active input asset.

    This deliberately validates only stable container signatures. Full media
    decoding, malware scanning, and transcoding belong to the later quarantine
    pipeline and must not be inferred from this check.
    """

    try:
        with path.open("rb") as source:
            header = source.read(128)
    except OSError:
        return False

    if content_type == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if content_type == "image/avif":
        return _has_iso_bmff_brand(header, {b"avif", b"avis"})
    if content_type in {"image/heic", "image/heif"}:
        return _has_iso_bmff_brand(
            header,
            {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"},
        )
    if content_type in {"video/mp4", "audio/mp4", "audio/x-m4a"}:
        return _has_iso_bmff_brand(header)
    if content_type == "video/quicktime":
        return _has_iso_bmff_brand(header, {b"qt  "}) or (
            len(header) >= 8 and header[4:8] in {b"moov", b"mdat", b"wide", b"free"}
        )
    if content_type == "video/mpeg":
        return header.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
    if content_type in {"video/webm", "audio/webm"}:
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if content_type == "audio/aac":
        return len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0
    if content_type == "audio/flac":
        return header.startswith(b"fLaC")
    if content_type == "audio/mpeg":
        return header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        )
    if content_type == "audio/ogg":
        return header.startswith(b"OggS")
    if content_type in {"audio/wav", "audio/x-wav"}:
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    return False


def _require_matching_content_signature(path: Path, content_type: str) -> None:
    if not _content_signature_matches(path, content_type):
        raise DomainError(
            "Uploaded bytes do not match the declared content type",
            "input_asset_content_signature_mismatch",
            422,
        )


def _safe_filename(value: str | None) -> str:
    filename = (value or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(
        character
        for character in filename.strip()
        if ord(character) >= 32 and character not in {"\x7f"}
    )
    return (filename or "upload")[:255]


def _valid_uuid_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    normalized = str(parsed)
    return normalized if normalized == value.lower() else None


class InputAssetService:
    @staticmethod
    def _validate_artifact_promotion_replay(
        existing: InputAsset,
        *,
        source_task_artifact_id: str,
    ) -> InputAsset:
        if existing.source_task_artifact_id != source_task_artifact_id:
            raise ConflictError(
                "Idempotency key is already used for a different source artifact"
            )
        return existing

    @staticmethod
    def get_artifact_promotion_replay(
        session: Session,
        *,
        company_id: str,
        user_id: str,
        idempotency_key: str,
        source_task_artifact_id: str,
    ) -> InputAsset | None:
        existing = session.scalar(
            select(InputAsset).where(
                InputAsset.company_id == company_id,
                InputAsset.uploaded_by_user_id == user_id,
                InputAsset.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        return InputAssetService._validate_artifact_promotion_replay(
            existing,
            source_task_artifact_id=source_task_artifact_id,
        )

    @classmethod
    def promote_task_artifact(
        cls,
        session: Session,
        *,
        store: InputAssetStore,
        artifact: TaskArtifact,
        user_id: str,
        idempotency_key: str,
        content_source: ArtifactContentSource | None,
        max_bytes: int,
    ) -> tuple[InputAsset, bool]:
        if (
            not 8 <= len(idempotency_key) <= 120
            or idempotency_key != idempotency_key.strip()
            or any(character.isspace() for character in idempotency_key)
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in idempotency_key
            )
        ):
            raise DomainError(
                "idempotency_key must be 8-120 visible non-whitespace characters",
                "invalid_idempotency_key",
                422,
            )
        existing = cls.get_artifact_promotion_replay(
            session,
            company_id=artifact.company_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            source_task_artifact_id=artifact.id,
        )
        if existing is not None:
            return existing, False
        if artifact.size_bytes > max_bytes:
            raise DomainError(
                "Generated artifact exceeds the configured input asset limit",
                "input_asset_too_large",
                413,
            )

        suffix = {
            "image": ".bin",
            "video": ".mp4",
        }.get(artifact.media_type, ".bin")
        temporary_path: Path | None = None
        try:
            if content_source is None:
                raise RuntimeError(
                    "Artifact content source is required for a new promotion"
                )
            with tempfile.NamedTemporaryFile(
                prefix="platform-artifact-promotion-",
                suffix=suffix,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                size_bytes, sha256 = content_source.copy_to(
                    temporary,
                    # The immutable artifact index gives us a tighter bound
                    # than the global asset limit and prevents over-reading a
                    # mismatched Relay response.
                    max_bytes=artifact.size_bytes,
                )
                temporary.flush()
            if size_bytes != artifact.size_bytes or sha256 != artifact.sha256:
                raise DomainError(
                    "Stored artifact integrity does not match its immutable index",
                    "artifact_integrity_mismatch",
                    502,
                )
            _require_matching_content_signature(temporary_path, artifact.content_type)

            asset_id = new_id()
            extension = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "video/mp4": ".mp4",
                "video/webm": ".webm",
            }.get(artifact.content_type, suffix)
            asset = InputAsset(
                id=asset_id,
                company_id=artifact.company_id,
                uploaded_by_user_id=user_id,
                source_task_artifact_id=artifact.id,
                idempotency_key=idempotency_key,
                original_filename=f"generated-{artifact.asset_id}{extension}",
                media_type=artifact.media_type,
                content_type=artifact.content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                storage_backend=store.kind,
                object_key=f"inputs/{artifact.company_id}/{asset_id}",
                status=InputAssetStatus.ACTIVE,
            )
            try:
                with session.begin_nested():
                    session.add(asset)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(InputAsset).where(
                        InputAsset.company_id == artifact.company_id,
                        InputAsset.uploaded_by_user_id == user_id,
                        InputAsset.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return (
                    cls._validate_artifact_promotion_replay(
                        existing,
                        source_task_artifact_id=artifact.id,
                    ),
                    False,
                )

            stored = store.put_file(
                asset.object_key,
                temporary_path,
                content_type=artifact.content_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            if stored.size_bytes != size_bytes or stored.sha256 != sha256:
                raise InputAssetStorageError(
                    "Promoted input asset integrity check failed"
                )
            return asset, True
        except InputAssetStorageError as exc:
            raise DomainError(
                "Input asset storage is temporarily unavailable",
                "input_asset_storage_unavailable",
                503,
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _validate_upload_replay(
        existing: InputAsset,
        *,
        original_filename: str,
        media_type: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> InputAsset:
        if (
            existing.source_task_artifact_id is not None
            or existing.original_filename != original_filename
            or existing.media_type != media_type
            or existing.content_type != content_type
            or existing.size_bytes != size_bytes
            or existing.sha256 != sha256
        ):
            raise ConflictError(
                "Idempotency-Key was already used for a different input asset"
            )
        return existing

    @staticmethod
    def create_from_upload(
        session: Session,
        *,
        store: InputAssetStore,
        company_id: str,
        user_id: str,
        upload: UploadFile,
        requested_media_type: str | None,
        max_bytes: int,
        idempotency_key: str | None = None,
    ) -> InputAsset:
        if idempotency_key is not None and (
            not 8 <= len(idempotency_key) <= 120
            or idempotency_key != idempotency_key.strip()
            or any(character.isspace() for character in idempotency_key)
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in idempotency_key
            )
        ):
            raise DomainError(
                "Idempotency-Key must be 8-120 visible non-whitespace characters",
                "invalid_idempotency_key",
                422,
            )
        content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        inferred_media_type = _CONTENT_TYPE_TO_MEDIA_TYPE.get(content_type)
        if inferred_media_type is None:
            raise DomainError(
                "Unsupported input asset content type",
                "unsupported_input_asset_type",
                422,
            )
        if requested_media_type is not None and requested_media_type not in {
            "image",
            "video",
            "audio",
        }:
            raise DomainError(
                "media_type must be image, video, or audio",
                "invalid_input_asset_media_type",
                422,
            )
        if requested_media_type and requested_media_type != inferred_media_type:
            raise DomainError(
                "media_type does not match the uploaded content type",
                "input_asset_media_type_mismatch",
                422,
            )

        temporary_path: Path | None = None
        digest = hashlib.sha256()
        size_bytes = 0
        original_filename = _safe_filename(upload.filename)
        try:
            with tempfile.NamedTemporaryFile(
                prefix="platform-input-", suffix=".upload", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := upload.file.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise DomainError(
                            "Input asset exceeds the configured upload limit",
                            "input_asset_too_large",
                            413,
                        )
                    digest.update(chunk)
                    temporary.write(chunk)
                temporary.flush()
            if size_bytes <= 0:
                raise DomainError(
                    "Input asset must not be empty",
                    "empty_input_asset",
                    422,
                )
            _require_matching_content_signature(temporary_path, content_type)

            sha256 = digest.hexdigest()
            if idempotency_key is not None:
                existing = session.scalar(
                    select(InputAsset).where(
                        InputAsset.company_id == company_id,
                        InputAsset.uploaded_by_user_id == user_id,
                        InputAsset.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return InputAssetService._validate_upload_replay(
                        existing,
                        original_filename=original_filename,
                        media_type=inferred_media_type,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        sha256=sha256,
                    )

            asset_id = new_id()
            object_key = f"inputs/{company_id}/{asset_id}"
            asset = InputAsset(
                id=asset_id,
                company_id=company_id,
                uploaded_by_user_id=user_id,
                idempotency_key=idempotency_key,
                original_filename=original_filename,
                media_type=inferred_media_type,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                storage_backend=store.kind,
                object_key=object_key,
                status=InputAssetStatus.ACTIVE,
            )
            try:
                with session.begin_nested():
                    session.add(asset)
                    session.flush()
            except IntegrityError:
                if idempotency_key is None:
                    raise
                existing = session.scalar(
                    select(InputAsset).where(
                        InputAsset.company_id == company_id,
                        InputAsset.uploaded_by_user_id == user_id,
                        InputAsset.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return InputAssetService._validate_upload_replay(
                    existing,
                    original_filename=original_filename,
                    media_type=inferred_media_type,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    sha256=sha256,
                )

            stored = store.put_file(
                object_key,
                temporary_path,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            if stored.size_bytes != size_bytes or stored.sha256 != sha256:
                raise InputAssetStorageError("Input asset integrity check failed")
            return asset
        except InputAssetStorageError as exc:
            raise DomainError(
                "Input asset storage is temporarily unavailable",
                "input_asset_storage_unavailable",
                503,
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def list_company(
        session: Session,
        *,
        company_id: str,
        status: InputAssetStatus | None = InputAssetStatus.ACTIVE,
        media_type: str | None = None,
        limit: int = 200,
    ) -> list[InputAsset]:
        statement = select(InputAsset).where(InputAsset.company_id == company_id)
        if status is not None:
            statement = statement.where(InputAsset.status == status)
        if media_type is not None:
            statement = statement.where(InputAsset.media_type == media_type)
        return list(
            session.scalars(
                statement.order_by(InputAsset.created_at.desc()).limit(limit)
            ).all()
        )

    @staticmethod
    def get_company_asset(
        session: Session,
        *,
        company_id: str,
        asset_id: str,
        require_active: bool = True,
    ) -> InputAsset:
        statement = select(InputAsset).where(
            InputAsset.id == asset_id,
            InputAsset.company_id == company_id,
        )
        if require_active:
            statement = statement.where(InputAsset.status == InputAssetStatus.ACTIVE)
        asset = session.scalar(statement)
        if asset is None:
            raise NotFoundError("Input asset does not exist")
        return asset

    @staticmethod
    def get_signed_asset(session: Session, *, asset_id: str) -> InputAsset:
        asset = session.scalar(
            select(InputAsset).where(
                InputAsset.id == asset_id,
                InputAsset.status == InputAssetStatus.ACTIVE,
            )
        )
        if asset is None:
            raise NotFoundError("Input asset does not exist")
        return asset

    @staticmethod
    def disable(session: Session, *, company_id: str, asset_id: str) -> InputAsset:
        asset = InputAssetService.get_company_asset(
            session,
            company_id=company_id,
            asset_id=asset_id,
            require_active=False,
        )
        if asset.status == InputAssetStatus.DISABLED:
            return asset
        active_reference = session.scalar(
            select(TaskInputAsset.task_id)
            .join(GenerationTask, GenerationTask.id == TaskInputAsset.task_id)
            .where(
                TaskInputAsset.asset_id == asset_id,
                GenerationTask.company_id == company_id,
                GenerationTask.status.in_(
                    [TaskStatus.DRAFT, TaskStatus.QUEUED, TaskStatus.PROCESSING]
                ),
            )
            .limit(1)
        )
        if active_reference is not None:
            raise ConflictError(
                "Input asset is referenced by a non-terminal generation task"
            )
        asset.status = InputAssetStatus.DISABLED
        session.flush()
        return asset

    @staticmethod
    def normalize_task_payload(
        session: Session, *, company_id: str, request_payload: dict[str, Any]
    ) -> tuple[dict[str, Any], list[InputAsset]]:
        raw_assets = request_payload.get("assets", [])
        if raw_assets is None:
            raw_assets = []
        if not isinstance(raw_assets, list):
            raise ConflictError("request_payload.assets must be a list")
        if len(raw_assets) > 15:
            raise ConflictError("A generation task supports at most 15 input assets")

        requested: list[tuple[str, str]] = []
        seen: set[str] = set()
        for reference in raw_assets:
            if not isinstance(reference, dict) or set(reference) != {
                "asset_id",
                "media_type",
            }:
                raise ConflictError(
                    "Every input asset must contain only asset_id and media_type"
                )
            asset_id = _valid_uuid_string(reference.get("asset_id"))
            media_type = reference.get("media_type")
            if asset_id is None or media_type not in {"image", "video", "audio"}:
                raise ConflictError("Input asset reference is invalid")
            if asset_id in seen:
                raise ConflictError("Duplicate input asset references are not allowed")
            seen.add(asset_id)
            requested.append((asset_id, media_type))

        if not requested:
            normalized = dict(request_payload)
            normalized["assets"] = []
            return normalized, []

        assets = list(
            session.scalars(
                select(InputAsset).where(
                    InputAsset.company_id == company_id,
                    InputAsset.id.in_([asset_id for asset_id, _ in requested]),
                    InputAsset.status == InputAssetStatus.ACTIVE,
                )
            ).all()
        )
        by_id = {asset.id: asset for asset in assets}
        if len(by_id) != len(requested):
            # Do not reveal whether a missing identifier belongs to another company.
            raise NotFoundError(
                "One or more active input assets do not exist in this company"
            )
        ordered: list[InputAsset] = []
        canonical_references: list[dict[str, str]] = []
        for asset_id, requested_media_type in requested:
            asset = by_id[asset_id]
            if asset.media_type != requested_media_type:
                raise ConflictError(
                    "Input asset media_type does not match stored metadata"
                )
            ordered.append(asset)
            canonical_references.append(
                {"asset_id": asset.id, "media_type": asset.media_type}
            )
        normalized = dict(request_payload)
        normalized["assets"] = canonical_references
        return normalized, ordered

    @staticmethod
    def link_task(
        session: Session,
        *,
        task_id: str,
        assets: list[InputAsset],
    ) -> None:
        for position, asset in enumerate(assets):
            session.add(
                TaskInputAsset(
                    task_id=task_id,
                    asset_id=asset.id,
                    position=position,
                )
            )
        session.flush()

    @staticmethod
    def access_url(
        *,
        asset: InputAsset,
        store: InputAssetStore,
        signer: FilesystemInputAssetSigner | None,
        expires_seconds: int,
        disposition: str,
    ) -> str:
        if asset.storage_backend != store.kind:
            raise DomainError(
                "Input asset storage backend is unavailable",
                "input_asset_storage_backend_unavailable",
                503,
            )
        try:
            signed = store.signed_url(
                asset.object_key,
                expires_seconds=expires_seconds,
                original_filename=asset.original_filename,
                disposition=disposition,
            )
            if signed is not None:
                return signed
            if signer is None:
                raise InputAssetStorageError(
                    "Filesystem input asset signer is unavailable"
                )
            return signer.sign(
                asset.id,
                expires_seconds=expires_seconds,
                disposition=disposition,
            )
        except InputAssetStorageError as exc:
            raise DomainError(
                "Input asset storage is temporarily unavailable",
                "input_asset_storage_unavailable",
                503,
            ) from exc

    @staticmethod
    def relay_assets(
        *,
        assets: list[InputAsset],
        store: InputAssetStore,
        signer: FilesystemInputAssetSigner | None,
        expires_seconds: int,
    ) -> list[dict[str, str]]:
        return [
            {
                "url": InputAssetService.access_url(
                    asset=asset,
                    store=store,
                    signer=signer,
                    expires_seconds=expires_seconds,
                    disposition="inline",
                ),
                "media_type": asset.media_type,
            }
            for asset in assets
        ]


class InputAssetRelayResolver:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        store: InputAssetStore,
        signer: FilesystemInputAssetSigner | None,
        expires_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._signer = signer
        self._expires_seconds = expires_seconds

    def resolve(
        self, *, company_id: str, references: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        payload = {"assets": references}
        with self._session_factory() as session:
            _, assets = InputAssetService.normalize_task_payload(
                session, company_id=company_id, request_payload=payload
            )
            return InputAssetService.relay_assets(
                assets=assets,
                store=self._store,
                signer=self._signer,
                expires_seconds=self._expires_seconds,
            )
