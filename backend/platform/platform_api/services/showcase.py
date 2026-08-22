from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Protocol

from fastapi import UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..asset_storage import (
    InputAssetIntegrityError,
    InputAssetStorageError,
    InputAssetStore,
)
from ..models import (
    ShowcaseChannel,
    ShowcaseDraftItem,
    ShowcaseMedia,
    ShowcasePublicationEvent,
    ShowcaseRelease,
    ShowcaseReleaseItem,
    new_id,
    utcnow,
)
from .audit import AuditService
from .errors import ConflictError, DomainError, NotFoundError
from .input_assets import _require_matching_content_signature, _safe_filename
from .showcase_media import SanitizedShowcaseMedia, sanitize_showcase_media


SHOWCASE_CHANNEL_ID = "home"
_SHOWCASE_CONTENT_TYPES = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "video/webm": "video",
}


class ShowcaseArtifactContentSource(Protocol):
    def copy_to(
        self,
        target: BinaryIO,
        *,
        max_bytes: int,
    ) -> tuple[int, str]: ...


def validate_idempotency_key(value: str) -> str:
    if (
        not 8 <= len(value) <= 120
        or value != value.strip()
        or any(character.isspace() for character in value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DomainError(
            "Idempotency-Key must be 8-120 visible non-whitespace characters",
            "invalid_idempotency_key",
            422,
        )
    return value


def _sha256_json(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _item_manifest(row: ShowcaseDraftItem | ShowcaseReleaseItem, media: ShowcaseMedia) -> dict[str, Any]:
    return {
        "source_draft_item_id": (
            row.id if isinstance(row, ShowcaseDraftItem) else row.source_draft_item_id
        ),
        "media_id": media.id,
        "media_sha256": media.sha256,
        "media_type": media.media_type,
        "content_type": media.content_type,
        "title": row.title,
        "section": row.section,
        "category": row.category,
        "alt_text": row.alt_text,
        "public_prompt": row.public_prompt,
        "aspect_ratio": row.aspect_ratio,
        "is_hero": row.is_hero,
    }


class ShowcaseService:
    @staticmethod
    def _channel(session: Session, *, lock: bool = False) -> ShowcaseChannel:
        statement = select(ShowcaseChannel).where(
            ShowcaseChannel.id == SHOWCASE_CHANNEL_ID
        )
        if lock and session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        channel = session.scalar(statement)
        if channel is not None:
            return channel
        try:
            with session.begin_nested():
                channel = ShowcaseChannel(id=SHOWCASE_CHANNEL_ID, draft_version=0)
                session.add(channel)
                session.flush()
        except IntegrityError:
            channel = session.get(ShowcaseChannel, SHOWCASE_CHANNEL_ID)
            if channel is None:  # pragma: no cover - defensive database failure
                raise
        return channel

    @staticmethod
    def _require_expected(channel: ShowcaseChannel, expected: int) -> None:
        if channel.draft_version != expected:
            raise ConflictError("Showcase draft changed; refresh and retry")

    @staticmethod
    def _require_publication_expected(
        channel: ShowcaseChannel,
        expected: int,
    ) -> None:
        if channel.publication_version != expected:
            raise ConflictError("Showcase publication changed; refresh and retry")

    @staticmethod
    def _touch_draft(channel: ShowcaseChannel, *, user_id: str) -> int:
        channel.draft_version += 1
        channel.updated_by_user_id = user_id
        channel.updated_at = utcnow()
        return int(channel.draft_version)

    @staticmethod
    def _replace_active_hero(
        session: Session,
        *,
        selected_item_id: str,
        user_id: str,
    ) -> list[str]:
        replaced = list(
            session.scalars(
                select(ShowcaseDraftItem).where(
                    ShowcaseDraftItem.id != selected_item_id,
                    ShowcaseDraftItem.retired_at.is_(None),
                    ShowcaseDraftItem.is_hero.is_(True),
                )
            ).all()
        )
        for item in replaced:
            item.is_hero = False
            item.updated_by_user_id = user_id
        return [item.id for item in replaced]

    @staticmethod
    def _media_replay(
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
        source_task_artifact_id: str | None,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> ShowcaseMedia | None:
        existing = session.scalar(
            select(ShowcaseMedia).where(
                ShowcaseMedia.created_by_user_id == user_id,
                ShowcaseMedia.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        if (
            existing.source_task_artifact_id != source_task_artifact_id
            or existing.content_type != content_type
            or existing.size_bytes != size_bytes
            or existing.sha256 != sha256
        ):
            raise ConflictError(
                "Idempotency-Key was already used for different showcase media"
            )
        return existing

    @classmethod
    def _persist_media_file(
        cls,
        session: Session,
        *,
        store: InputAssetStore,
        user_id: str,
        idempotency_key: str,
        source_task_artifact_id: str | None,
        original_filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        temporary_path: Path,
    ) -> tuple[ShowcaseMedia, bool]:
        replay = cls._media_replay(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            source_task_artifact_id=source_task_artifact_id,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        if replay is not None:
            return replay, False
        existing_content = session.scalar(
            select(ShowcaseMedia).where(ShowcaseMedia.sha256 == sha256)
        )
        if existing_content is not None:
            if (
                existing_content.content_type != content_type
                or existing_content.size_bytes != size_bytes
            ):
                raise ConflictError("Showcase media digest metadata is inconsistent")
            # A successful response must permanently bind the supplied
            # idempotency key. This immutable row belongs to a different
            # successful request, so expose it through the admin media list
            # instead of pretending the new key was consumed.
            raise ConflictError(
                "Showcase media content already exists under a different "
                "Idempotency-Key"
            )

        _require_matching_content_signature(temporary_path, content_type)
        object_key = f"showcase/media/{sha256}"
        try:
            stored = store.put_file(
                object_key,
                temporary_path,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        except InputAssetStorageError as exc:
            raise DomainError(
                "Showcase media storage is temporarily unavailable",
                "showcase_storage_unavailable",
                503,
            ) from exc
        if stored.size_bytes != size_bytes or stored.sha256 != sha256:
            raise DomainError(
                "Showcase media storage integrity check failed",
                "showcase_storage_integrity_mismatch",
                502,
            )
        media = ShowcaseMedia(
            id=new_id(),
            created_by_user_id=user_id,
            source_task_artifact_id=source_task_artifact_id,
            idempotency_key=idempotency_key,
            original_filename=original_filename,
            media_type=_SHOWCASE_CONTENT_TYPES[content_type],
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_backend=store.kind,
            object_key=object_key,
            created_at=utcnow(),
        )
        try:
            with session.begin_nested():
                session.add(media)
                session.flush()
        except IntegrityError:
            concurrent = session.scalar(
                select(ShowcaseMedia).where(ShowcaseMedia.sha256 == sha256)
            )
            if concurrent is None:
                raise
            replay = cls._media_replay(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                source_task_artifact_id=source_task_artifact_id,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            if replay is not None:
                return replay, False
            raise ConflictError(
                "Showcase media content already exists under a different "
                "Idempotency-Key"
            )
        return media, True

    @classmethod
    def create_media_from_upload(
        cls,
        session: Session,
        *,
        store: InputAssetStore,
        user_id: str,
        idempotency_key: str,
        upload: UploadFile,
        max_bytes: int,
    ) -> tuple[ShowcaseMedia, bool]:
        validate_idempotency_key(idempotency_key)
        content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in _SHOWCASE_CONTENT_TYPES:
            raise DomainError(
                "Showcase accepts JPEG, PNG, WebP, MP4, or WebM media",
                "unsupported_showcase_media_type",
                422,
            )
        if _SHOWCASE_CONTENT_TYPES[content_type] == "video":
            raise DomainError(
                "Direct showcase video uploads are disabled; import a verified "
                "personal generated artifact instead",
                "direct_showcase_video_disabled",
                422,
            )
        size_bytes = 0
        temporary_path: Path | None = None
        sanitized: SanitizedShowcaseMedia | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="platform-showcase-", suffix=".upload", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := upload.file.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise DomainError(
                            "Showcase media exceeds the configured upload limit",
                            "showcase_media_too_large",
                            413,
                        )
                    temporary.write(chunk)
                temporary.flush()
            if size_bytes <= 0:
                raise DomainError(
                    "Showcase media must not be empty",
                    "empty_showcase_media",
                    422,
                )
            sanitized = sanitize_showcase_media(
                temporary_path,
                content_type=content_type,
                max_bytes=max_bytes,
                trusted_generated_artifact=False,
            )
            return cls._persist_media_file(
                session,
                store=store,
                user_id=user_id,
                idempotency_key=idempotency_key,
                source_task_artifact_id=None,
                original_filename=_safe_filename(upload.filename),
                content_type=sanitized.content_type,
                size_bytes=sanitized.size_bytes,
                sha256=sanitized.sha256,
                temporary_path=sanitized.path,
            )
        finally:
            if sanitized is not None:
                try:
                    sanitized.path.unlink(missing_ok=True)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def create_media_from_artifact(
        cls,
        session: Session,
        *,
        store: InputAssetStore,
        user_id: str,
        idempotency_key: str,
        source_task_artifact_id: str,
        asset_id: str,
        content_type: str,
        expected_size_bytes: int,
        expected_sha256: str,
        content_source: ShowcaseArtifactContentSource,
        max_bytes: int,
    ) -> tuple[ShowcaseMedia, bool]:
        validate_idempotency_key(idempotency_key)
        if content_type not in _SHOWCASE_CONTENT_TYPES:
            raise DomainError(
                "Generated artifact type is not supported by the showcase",
                "unsupported_showcase_media_type",
                422,
            )
        if expected_size_bytes > max_bytes:
            raise DomainError(
                "Generated artifact exceeds the configured showcase limit",
                "showcase_media_too_large",
                413,
            )
        idempotent_replay = session.scalar(
            select(ShowcaseMedia).where(
                ShowcaseMedia.created_by_user_id == user_id,
                ShowcaseMedia.idempotency_key == idempotency_key,
            )
        )
        if idempotent_replay is not None:
            if idempotent_replay.source_task_artifact_id != source_task_artifact_id:
                raise ConflictError(
                    "Idempotency-Key was already used for different showcase media"
                )
            return idempotent_replay, False
        source_replay = session.scalar(
            select(ShowcaseMedia).where(
                ShowcaseMedia.source_task_artifact_id == source_task_artifact_id
            )
        )
        if source_replay is not None:
            raise ConflictError(
                "Generated artifact was already imported under a different "
                "Idempotency-Key"
            )
        temporary_path: Path | None = None
        sanitized: SanitizedShowcaseMedia | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="platform-showcase-artifact-", suffix=".upload", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                size_bytes, sha256 = content_source.copy_to(
                    temporary,
                    max_bytes=expected_size_bytes,
                )
                temporary.flush()
            if size_bytes != expected_size_bytes or sha256 != expected_sha256:
                raise DomainError(
                    "Stored artifact does not match its immutable index",
                    "showcase_artifact_integrity_mismatch",
                    502,
                )
            sanitized = sanitize_showcase_media(
                temporary_path,
                content_type=content_type,
                max_bytes=max_bytes,
                trusted_generated_artifact=True,
            )
            suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "video/mp4": ".mp4",
            }[sanitized.content_type]
            return cls._persist_media_file(
                session,
                store=store,
                user_id=user_id,
                idempotency_key=idempotency_key,
                source_task_artifact_id=source_task_artifact_id,
                original_filename=f"generated-{asset_id}{suffix}",
                content_type=sanitized.content_type,
                size_bytes=sanitized.size_bytes,
                sha256=sanitized.sha256,
                temporary_path=sanitized.path,
            )
        finally:
            if sanitized is not None:
                try:
                    sanitized.path.unlink(missing_ok=True)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def create_item(
        cls,
        session: Session,
        *,
        user_id: str,
        expected_draft_version: int,
        values: dict[str, Any],
        request_id: str,
    ) -> tuple[ShowcaseDraftItem, int]:
        channel = cls._channel(session, lock=True)
        cls._require_expected(channel, expected_draft_version)
        if session.get(ShowcaseMedia, values["media_id"]) is None:
            raise NotFoundError("Showcase media does not exist")
        item = ShowcaseDraftItem(
            id=new_id(),
            **values,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        session.add(item)
        replaced_hero_item_ids = (
            cls._replace_active_hero(
                session,
                selected_item_id=item.id,
                user_id=user_id,
            )
            if item.is_hero
            else []
        )
        version = cls._touch_draft(channel, user_id=user_id)
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="showcase.draft_item.create",
            target_type="showcase_draft_item",
            target_id=item.id,
            before_summary={},
            after_summary={
                "media_id": item.media_id,
                "section": item.section,
                "is_hero": item.is_hero,
                "sort_order": item.sort_order,
                "replaced_hero_item_ids": replaced_hero_item_ids,
                "draft_version": version,
            },
            request_id=request_id,
        )
        return item, version

    @classmethod
    def update_item(
        cls,
        session: Session,
        *,
        item_id: str,
        user_id: str,
        expected_draft_version: int,
        values: dict[str, Any],
        request_id: str,
    ) -> tuple[ShowcaseDraftItem, int]:
        channel = cls._channel(session, lock=True)
        cls._require_expected(channel, expected_draft_version)
        item = session.get(ShowcaseDraftItem, item_id)
        if item is None or item.retired_at is not None:
            raise NotFoundError("Active showcase draft item does not exist")
        if session.get(ShowcaseMedia, values["media_id"]) is None:
            raise NotFoundError("Showcase media does not exist")
        before = {
            "media_id": item.media_id,
            "section": item.section,
            "is_hero": item.is_hero,
            "sort_order": item.sort_order,
        }
        for key, value in values.items():
            setattr(item, key, value)
        item.updated_by_user_id = user_id
        replaced_hero_item_ids = (
            cls._replace_active_hero(
                session,
                selected_item_id=item.id,
                user_id=user_id,
            )
            if item.is_hero
            else []
        )
        version = cls._touch_draft(channel, user_id=user_id)
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="showcase.draft_item.update",
            target_type="showcase_draft_item",
            target_id=item.id,
            before_summary=before,
            after_summary={
                "media_id": item.media_id,
                "section": item.section,
                "is_hero": item.is_hero,
                "sort_order": item.sort_order,
                "replaced_hero_item_ids": replaced_hero_item_ids,
                "draft_version": version,
            },
            request_id=request_id,
        )
        return item, version

    @classmethod
    def retire_item(
        cls,
        session: Session,
        *,
        item_id: str,
        user_id: str,
        expected_draft_version: int,
        request_id: str,
    ) -> tuple[ShowcaseDraftItem, int]:
        channel = cls._channel(session, lock=True)
        cls._require_expected(channel, expected_draft_version)
        item = session.get(ShowcaseDraftItem, item_id)
        if item is None or item.retired_at is not None:
            raise NotFoundError("Active showcase draft item does not exist")
        item.retired_at = utcnow()
        item.updated_by_user_id = user_id
        version = cls._touch_draft(channel, user_id=user_id)
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="showcase.draft_item.retire",
            target_type="showcase_draft_item",
            target_id=item.id,
            before_summary={"retired_at": None},
            after_summary={
                "retired_at": item.retired_at.isoformat(),
                "draft_version": version,
            },
            request_id=request_id,
        )
        return item, version

    @classmethod
    def reorder_items(
        cls,
        session: Session,
        *,
        user_id: str,
        expected_draft_version: int,
        item_ids: list[str],
        request_id: str,
    ) -> int:
        channel = cls._channel(session, lock=True)
        cls._require_expected(channel, expected_draft_version)
        active = list(
            session.scalars(
                select(ShowcaseDraftItem)
                .where(ShowcaseDraftItem.retired_at.is_(None))
                .order_by(ShowcaseDraftItem.sort_order, ShowcaseDraftItem.id)
            ).all()
        )
        by_id = {item.id: item for item in active}
        if set(item_ids) != set(by_id) or len(item_ids) != len(active):
            raise DomainError(
                "item_ids must be the complete active showcase item set",
                "invalid_showcase_order",
                422,
            )
        before = [item.id for item in active]
        for position, item_id in enumerate(item_ids):
            item = by_id[item_id]
            item.sort_order = position
            item.updated_by_user_id = user_id
        version = cls._touch_draft(channel, user_id=user_id)
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="showcase.draft_items.reorder",
            target_type="showcase_channel",
            target_id=SHOWCASE_CHANNEL_ID,
            before_summary={"item_ids": before},
            after_summary={"item_ids": item_ids, "draft_version": version},
            request_id=request_id,
        )
        return version

    @staticmethod
    def _active_draft_rows(
        session: Session,
    ) -> list[tuple[ShowcaseDraftItem, ShowcaseMedia]]:
        return list(
            session.execute(
                select(ShowcaseDraftItem, ShowcaseMedia)
                .join(ShowcaseMedia, ShowcaseMedia.id == ShowcaseDraftItem.media_id)
                .where(ShowcaseDraftItem.retired_at.is_(None))
                .order_by(ShowcaseDraftItem.sort_order, ShowcaseDraftItem.id)
            ).all()
        )

    @staticmethod
    def _release_rows(
        session: Session, release_id: str
    ) -> list[tuple[ShowcaseReleaseItem, ShowcaseMedia]]:
        return list(
            session.execute(
                select(ShowcaseReleaseItem, ShowcaseMedia)
                .join(ShowcaseMedia, ShowcaseMedia.id == ShowcaseReleaseItem.media_id)
                .where(ShowcaseReleaseItem.release_id == release_id)
                .order_by(ShowcaseReleaseItem.position)
            ).all()
        )

    @staticmethod
    def _validate_publishable(manifest: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = list(manifest)
        if not rows:
            raise DomainError(
                "At least one active showcase item is required",
                "empty_showcase_release",
                422,
            )
        if sum(1 for item in rows if item["is_hero"]) != 1:
            raise DomainError(
                "A showcase release must contain exactly one hero",
                "invalid_showcase_hero_count",
                422,
            )
        hero = next(item for item in rows if item["is_hero"])
        if hero["section"] != "video":
            raise DomainError(
                "The showcase hero must belong to the video section",
                "invalid_showcase_hero_section",
                422,
            )
        return rows

    @staticmethod
    def _verify_media_storage(
        store: InputAssetStore,
        media_rows: Iterable[ShowcaseMedia],
    ) -> None:
        """Fail before the release pointer changes if any object is unavailable."""

        verified_ids: set[str] = set()
        for media in media_rows:
            if media.id in verified_ids:
                continue
            try:
                store.verify_object(
                    media.object_key,
                    content_type=media.content_type,
                    size_bytes=int(media.size_bytes),
                    sha256=media.sha256,
                )
            except InputAssetIntegrityError as exc:
                raise DomainError(
                    "Showcase media no longer matches its immutable index",
                    "showcase_storage_integrity_mismatch",
                    502,
                ) from exc
            except InputAssetStorageError as exc:
                raise DomainError(
                    "Showcase media storage is temporarily unavailable",
                    "showcase_storage_unavailable",
                    503,
                ) from exc
            verified_ids.add(media.id)

    @classmethod
    def _release_replay(
        cls,
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> ShowcaseRelease | None:
        existing = session.scalar(
            select(ShowcaseRelease).where(
                ShowcaseRelease.published_by_user_id == user_id,
                ShowcaseRelease.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        if existing.request_fingerprint != fingerprint:
            raise ConflictError(
                "Idempotency-Key was already used for a different showcase release"
            )
        return existing

    @classmethod
    def _create_release(
        cls,
        session: Session,
        *,
        channel: ShowcaseChannel,
        user_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        release_note: str,
        source_release_id: str | None,
        manifest: list[dict[str, Any]],
        request_id: str,
        action: str,
    ) -> ShowcaseRelease:
        manifest = cls._validate_publishable(manifest)
        version = int(session.scalar(select(func.max(ShowcaseRelease.version))) or 0) + 1
        publication_version = int(channel.publication_version) + 1
        release = ShowcaseRelease(
            id=new_id(),
            version=version,
            draft_version=int(channel.draft_version),
            publication_version=publication_version,
            published_by_user_id=user_id,
            source_release_id=source_release_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            release_note=release_note,
            manifest_sha256=_sha256_json(manifest),
            published_at=utcnow(),
        )
        session.add(release)
        session.flush()
        for position, item in enumerate(manifest):
            session.add(
                ShowcaseReleaseItem(
                    id=new_id(),
                    release_id=release.id,
                    source_draft_item_id=item["source_draft_item_id"],
                    media_id=item["media_id"],
                    position=position,
                    title=item["title"],
                    section=item["section"],
                    category=item["category"],
                    alt_text=item["alt_text"],
                    public_prompt=item["public_prompt"],
                    aspect_ratio=item["aspect_ratio"],
                    is_hero=item["is_hero"],
                )
            )
        previous_release_id = channel.current_release_id
        channel.current_release_id = release.id
        channel.publication_version = publication_version
        channel.updated_by_user_id = user_id
        channel.updated_at = utcnow()
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action=action,
            target_type="showcase_release",
            target_id=release.id,
            before_summary={"current_release_id": previous_release_id},
            after_summary={
                "release_id": release.id,
                "version": release.version,
                "draft_version": release.draft_version,
                "publication_version": release.publication_version,
                "source_release_id": source_release_id,
                "manifest_sha256": release.manifest_sha256,
                "item_count": len(manifest),
            },
            request_id=request_id,
        )
        return release

    @classmethod
    def publish(
        cls,
        session: Session,
        *,
        store: InputAssetStore,
        user_id: str,
        idempotency_key: str,
        expected_draft_version: int,
        expected_publication_version: int,
        release_note: str,
        request_id: str,
    ) -> ShowcaseRelease:
        validate_idempotency_key(idempotency_key)
        fingerprint = _sha256_json(
            {
                "operation": "publish",
                "expected_draft_version": expected_draft_version,
                "expected_publication_version": expected_publication_version,
                "release_note": release_note,
            }
        )
        channel = cls._channel(session, lock=True)
        replay = cls._release_replay(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        cls._require_expected(channel, expected_draft_version)
        cls._require_publication_expected(channel, expected_publication_version)
        rows = cls._active_draft_rows(session)
        manifest = [_item_manifest(item, media) for item, media in rows]
        cls._validate_publishable(manifest)
        cls._verify_media_storage(store, (media for _, media in rows))
        return cls._create_release(
            session,
            channel=channel,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            release_note=release_note,
            source_release_id=None,
            manifest=manifest,
            request_id=request_id,
            action="showcase.release.publish",
        )

    @classmethod
    def rollback(
        cls,
        session: Session,
        *,
        store: InputAssetStore,
        target_release_id: str,
        user_id: str,
        idempotency_key: str,
        expected_draft_version: int,
        expected_publication_version: int,
        release_note: str,
        request_id: str,
    ) -> ShowcaseRelease:
        validate_idempotency_key(idempotency_key)
        fingerprint = _sha256_json(
            {
                "operation": "rollback",
                "target_release_id": target_release_id,
                "expected_draft_version": expected_draft_version,
                "expected_publication_version": expected_publication_version,
                "release_note": release_note,
            }
        )
        channel = cls._channel(session, lock=True)
        replay = cls._release_replay(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        target = session.get(ShowcaseRelease, target_release_id)
        if target is None:
            raise NotFoundError("Showcase release does not exist")
        cls._require_expected(channel, expected_draft_version)
        cls._require_publication_expected(channel, expected_publication_version)
        rows = cls._release_rows(session, target.id)
        manifest = [_item_manifest(item, media) for item, media in rows]
        cls._validate_publishable(manifest)
        cls._verify_media_storage(store, (media for _, media in rows))
        return cls._create_release(
            session,
            channel=channel,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            release_note=release_note,
            source_release_id=target.id,
            manifest=manifest,
            request_id=request_id,
            action="showcase.release.rollback",
        )

    @classmethod
    def unpublish(
        cls,
        session: Session,
        *,
        user_id: str,
        idempotency_key: str,
        expected_draft_version: int,
        expected_publication_version: int,
        release_note: str,
        request_id: str,
    ) -> ShowcasePublicationEvent:
        validate_idempotency_key(idempotency_key)
        fingerprint = _sha256_json(
            {
                "operation": "unpublish",
                "expected_draft_version": expected_draft_version,
                "expected_publication_version": expected_publication_version,
                "release_note": release_note,
            }
        )
        channel = cls._channel(session, lock=True)
        replay = session.scalar(
            select(ShowcasePublicationEvent).where(
                ShowcasePublicationEvent.actor_user_id == user_id,
                ShowcasePublicationEvent.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise ConflictError(
                    "Idempotency-Key was already used for a different "
                    "showcase publication event"
                )
            return replay
        cls._require_expected(channel, expected_draft_version)
        cls._require_publication_expected(channel, expected_publication_version)
        previous_release_id = channel.current_release_id
        if previous_release_id is None:
            raise ConflictError("Showcase is already unpublished")
        publication_version = int(channel.publication_version) + 1
        event = ShowcasePublicationEvent(
            id=new_id(),
            action="unpublish",
            actor_user_id=user_id,
            previous_release_id=previous_release_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            expected_draft_version=int(channel.draft_version),
            publication_version=publication_version,
            release_note=release_note,
            unpublished_at=utcnow(),
        )
        session.add(event)
        session.flush()
        channel.current_release_id = None
        channel.publication_version = publication_version
        channel.updated_by_user_id = user_id
        channel.updated_at = utcnow()
        session.flush()
        AuditService.append(
            session,
            actor_user_id=user_id,
            action="showcase.release.unpublish",
            target_type="showcase_publication_event",
            target_id=event.id,
            before_summary={"current_release_id": previous_release_id},
            after_summary={
                "current_release_id": None,
                "draft_version": int(channel.draft_version),
                "publication_version": int(channel.publication_version),
            },
            request_id=request_id,
        )
        return event

    @classmethod
    def admin_state(cls, session: Session) -> dict[str, Any]:
        channel = cls._channel(session)
        items = list(
            session.scalars(
                select(ShowcaseDraftItem).order_by(
                    ShowcaseDraftItem.retired_at.asc(),
                    ShowcaseDraftItem.sort_order,
                    ShowcaseDraftItem.id,
                )
            ).all()
        )
        media = list(
            session.scalars(
                select(ShowcaseMedia).order_by(
                    ShowcaseMedia.created_at.desc(), ShowcaseMedia.id.desc()
                )
            ).all()
        )
        releases = list(
            session.scalars(
                select(ShowcaseRelease).order_by(
                    ShowcaseRelease.version.desc()
                ).limit(100)
            ).all()
        )
        release_ids = [release.id for release in releases]
        release_item_counts = (
            {
                str(release_id): int(count)
                for release_id, count in session.execute(
                    select(
                        ShowcaseReleaseItem.release_id,
                        func.count(ShowcaseReleaseItem.id),
                    )
                    .where(ShowcaseReleaseItem.release_id.in_(release_ids))
                    .group_by(ShowcaseReleaseItem.release_id)
                ).all()
            }
            if release_ids
            else {}
        )
        current = (
            session.get(ShowcaseRelease, channel.current_release_id)
            if channel.current_release_id
            else None
        )
        publication_events = list(
            session.scalars(
                select(ShowcasePublicationEvent).order_by(
                ShowcasePublicationEvent.unpublished_at.desc(),
                ShowcasePublicationEvent.id.desc(),
                ).limit(100)
            ).all()
        )
        active_manifest = [
            _item_manifest(item, media)
            for item, media in cls._active_draft_rows(session)
        ]
        has_unpublished_changes = (
            bool(active_manifest)
            if current is None
            else _sha256_json(active_manifest) != current.manifest_sha256
        )
        return {
            "channel": channel,
            "items": items,
            "media": media,
            "releases": releases,
            "release_item_counts": release_item_counts,
            "current_release": current,
            "last_unpublished_event": (
                publication_events[0] if publication_events else None
            ),
            "publication_events": publication_events,
            "has_unpublished_changes": has_unpublished_changes,
        }

    @classmethod
    def public_release(
        cls, session: Session
    ) -> tuple[ShowcaseRelease | None, list[tuple[ShowcaseReleaseItem, ShowcaseMedia]]]:
        channel = session.get(ShowcaseChannel, SHOWCASE_CHANNEL_ID)
        if channel is None or channel.current_release_id is None:
            return None, []
        release = session.get(ShowcaseRelease, channel.current_release_id)
        if release is None:  # protected by a restrictive foreign key
            return None, []
        return release, cls._release_rows(session, release.id)

    @staticmethod
    def public_media(session: Session, media_id: str) -> ShowcaseMedia | None:
        return session.scalar(
            select(ShowcaseMedia)
            .join(
                ShowcaseReleaseItem,
                ShowcaseReleaseItem.media_id == ShowcaseMedia.id,
            )
            .join(
                ShowcaseChannel,
                ShowcaseChannel.current_release_id
                == ShowcaseReleaseItem.release_id,
            )
            .where(
                ShowcaseChannel.id == SHOWCASE_CHANNEL_ID,
                ShowcaseMedia.id == media_id,
            )
        )

    @staticmethod
    def clear_draft_for_tests(session: Session) -> None:
        """Narrow test helper; production routes never physically delete drafts."""

        session.execute(delete(ShowcaseDraftItem))
