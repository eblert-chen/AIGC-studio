from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
from typing import Any, BinaryIO, Literal, Protocol
from uuid import UUID
import warnings

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
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
NormalizationProfile = Literal["director_previs_mp4_v1"]

_TRUSTED_MEDIA_METADATA_VERSION = 1
_DIRECTOR_PREVIS_PROFILE = "director_previs_mp4_v1"
_LOCAL_ONLY_VIDEO_CONTENT_TYPES = frozenset({"video/webm"})
_DIRECTOR_PREVIS_MAX_SOURCE_BYTES = 24 * 1024 * 1024
_DIRECTOR_PREVIS_MAX_WIDTH = 640
_DIRECTOR_PREVIS_MAX_HEIGHT = 360
_DIRECTOR_PREVIS_MIN_DURATION_MS = 2_000
_DIRECTOR_PREVIS_MAX_DURATION_MS = 12_250
_MAX_INSPECTED_VIDEO_DURATION_MS = 60 * 60 * 1_000
_MAX_INSPECTED_DIMENSION = 8_192
_MAX_INSPECTED_VIDEO_FPS = 240.0


@dataclass(frozen=True)
class TrustedMediaMetadata:
    width_px: int
    height_px: int
    media_container: str | None = None
    video_codec: str | None = None
    video_fps: float | None = None
    duration_ms: int | None = None
    video_has_audio: bool | None = None

    def model_fields(self) -> dict[str, Any]:
        return {
            "media_metadata_version": _TRUSTED_MEDIA_METADATA_VERSION,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "media_container": self.media_container,
            "video_codec": self.video_codec,
            "video_fps": self.video_fps,
            "duration_ms": self.duration_ms,
            "video_has_audio": self.video_has_audio,
        }


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
    """Perform the cheap MIME/container signature check before full decode."""

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


def _media_decode_error() -> DomainError:
    return DomainError(
        "Uploaded media could not be fully decoded",
        "input_asset_media_decode_failed",
        422,
    )


def _media_inspection_unavailable() -> DomainError:
    return DomainError(
        "Media inspection is temporarily unavailable",
        "input_asset_media_inspection_unavailable",
        503,
    )


def _inspect_image(path: Path, content_type: str) -> TrustedMediaMetadata:
    expected_formats = {
        "image/avif": {"AVIF"},
        "image/gif": {"GIF"},
        "image/heic": {"HEIF", "HEIC"},
        "image/heif": {"HEIF", "HEIC"},
        "image/jpeg": {"JPEG"},
        "image/png": {"PNG"},
        "image/webp": {"WEBP"},
    }
    expected = expected_formats.get(content_type)
    if expected is None:
        raise _media_decode_error()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width_px, height_px = image.size
                image_format = (image.format or "").upper()
                if image_format not in expected:
                    raise _media_decode_error()
                if (
                    width_px <= 0
                    or height_px <= 0
                    or width_px > _MAX_INSPECTED_DIMENSION
                    or height_px > _MAX_INSPECTED_DIMENSION
                ):
                    raise _media_decode_error()
                image.verify()
            # ``verify`` deliberately invalidates the decoder. Reopen and load
            # every frame so a valid header plus a truncated payload never
            # becomes an ACTIVE asset.
            with Image.open(path) as image:
                frame_count = 0
                while True:
                    image.load()
                    frame_count += 1
                    if frame_count > 1_000:
                        raise _media_decode_error()
                    try:
                        image.seek(image.tell() + 1)
                    except EOFError:
                        break
    except DomainError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise _media_decode_error() from exc
    return TrustedMediaMetadata(width_px=width_px, height_px=height_px)


def _run_media_process(
    command: list[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        raise _media_inspection_unavailable() from exc


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _video_fps(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(key)
        if not isinstance(value, str) or value in {"", "0/0", "N/A"}:
            continue
        try:
            parsed = float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(parsed) and 0 < parsed <= _MAX_INSPECTED_VIDEO_FPS:
            return parsed
    return None


def _normalized_container(content_type: str, format_names: set[str]) -> str | None:
    if content_type == "video/webm" and "webm" in format_names:
        return "webm"
    if content_type == "video/mp4" and format_names.intersection(
        {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
    ):
        return "mp4"
    if content_type == "video/quicktime" and format_names.intersection(
        {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
    ):
        return "mov"
    if content_type == "video/mpeg" and any(
        name.startswith("mpeg") for name in format_names
    ):
        return "mpeg"
    return None


def _inspect_video(path: Path, content_type: str) -> TrustedMediaMetadata:
    probe = _run_media_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=format_name,duration:"
                "stream=codec_type,codec_name,width,height,avg_frame_rate,"
                "r_frame_rate,duration"
            ),
            "-of",
            "json",
            str(path),
        ],
        timeout_seconds=30,
    )
    if probe.returncode != 0:
        raise _media_decode_error()
    try:
        payload = json.loads(probe.stdout.decode("utf-8"))
        streams = payload["streams"]
        video_stream = next(
            stream for stream in streams if stream.get("codec_type") == "video"
        )
        format_names = {
            item.strip().lower()
            for item in str(payload.get("format", {}).get("format_name", "")).split(",")
            if item.strip()
        }
        width_px = int(video_stream["width"])
        height_px = int(video_stream["height"])
        codec = str(video_stream["codec_name"]).strip().lower()
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        raise _media_decode_error() from exc
    container = _normalized_container(content_type, format_names)
    fps = _video_fps(video_stream)
    duration_seconds = _positive_float(video_stream.get("duration")) or _positive_float(
        payload.get("format", {}).get("duration")
    )
    if (
        container is None
        or not codec
        or fps is None
        or duration_seconds is None
        or width_px <= 0
        or height_px <= 0
        or width_px > _MAX_INSPECTED_DIMENSION
        or height_px > _MAX_INSPECTED_DIMENSION
    ):
        raise _media_decode_error()
    duration_ms = int(round(duration_seconds * 1_000))
    if duration_ms <= 0 or duration_ms > _MAX_INSPECTED_VIDEO_DURATION_MS:
        raise _media_decode_error()

    decoded = _run_media_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ],
        timeout_seconds=120,
    )
    if decoded.returncode != 0:
        raise _media_decode_error()
    return TrustedMediaMetadata(
        width_px=width_px,
        height_px=height_px,
        media_container=container,
        video_codec=codec,
        video_fps=fps,
        duration_ms=duration_ms,
        video_has_audio=any(
            stream.get("codec_type") == "audio" for stream in streams
        ),
    )


def _inspect_trusted_media(
    path: Path,
    *,
    media_type: MediaType,
    content_type: str,
) -> TrustedMediaMetadata | None:
    if media_type == "image":
        return _inspect_image(path, content_type)
    if media_type == "video":
        return _inspect_video(path, content_type)
    return None


def _file_size_and_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size_bytes += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise _media_inspection_unavailable() from exc
    return size_bytes, digest.hexdigest()


def _normalized_previs_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = (stem or "director-previs")[:251]
    return f"{stem}.mp4"


def _normalize_director_previs(
    source_path: Path,
    *,
    source_metadata: TrustedMediaMetadata,
    source_size_bytes: int,
) -> tuple[Path, TrustedMediaMetadata, int, str]:
    if (
        source_size_bytes > _DIRECTOR_PREVIS_MAX_SOURCE_BYTES
        or source_metadata.media_container != "webm"
        or source_metadata.video_codec != "vp8"
        or source_metadata.video_has_audio is not False
        or source_metadata.video_fps is None
        or not 14.0 <= source_metadata.video_fps <= 16.0
        or source_metadata.duration_ms is None
        or not _DIRECTOR_PREVIS_MIN_DURATION_MS
        <= source_metadata.duration_ms
        <= _DIRECTOR_PREVIS_MAX_DURATION_MS
        or source_metadata.width_px > _DIRECTOR_PREVIS_MAX_WIDTH
        or source_metadata.height_px > _DIRECTOR_PREVIS_MAX_HEIGHT
    ):
        raise DomainError(
            "director_previs_mp4_v1 requires the host VP8 WebM contract",
            "director_previs_source_contract_mismatch",
            422,
        )
    with tempfile.NamedTemporaryFile(
        prefix="platform-director-previs-",
        suffix=".mp4",
        delete=False,
    ) as target:
        target_path = Path(target.name)
    encoded = _run_media_process(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-xerror",
            "-nostdin",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            "scale=ceil(iw/2)*2:ceil(ih/2)*2,fps=24,format=yuv420p",
            "-t",
            "12",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-threads",
            "1",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-movflags",
            "+faststart",
            str(target_path),
        ],
        timeout_seconds=120,
    )
    if encoded.returncode != 0:
        target_path.unlink(missing_ok=True)
        raise DomainError(
            "Director previs normalization failed",
            "director_previs_normalization_failed",
            422,
        )
    try:
        output_size, output_sha256 = _file_size_and_sha256(target_path)
        if output_size <= 0 or output_size > _DIRECTOR_PREVIS_MAX_SOURCE_BYTES:
            raise DomainError(
                "Normalized director previs exceeds the supported size",
                "director_previs_normalized_size_invalid",
                422,
            )
        output_metadata = _inspect_video(target_path, "video/mp4")
        if (
            output_metadata.media_container != "mp4"
            or output_metadata.video_codec != "h264"
            or output_metadata.video_has_audio is not False
            or output_metadata.video_fps is None
            or not math.isclose(output_metadata.video_fps, 24.0, abs_tol=0.01)
            or output_metadata.duration_ms is None
            or not _DIRECTOR_PREVIS_MIN_DURATION_MS
            <= output_metadata.duration_ms
            <= 12_050
            or output_metadata.width_px > _DIRECTOR_PREVIS_MAX_WIDTH
            or output_metadata.height_px > _DIRECTOR_PREVIS_MAX_HEIGHT
        ):
            raise DomainError(
                "Normalized director previs does not satisfy the MP4 contract",
                "director_previs_normalized_contract_mismatch",
                422,
            )
        return target_path, output_metadata, output_size, output_sha256
    except Exception:
        target_path.unlink(missing_ok=True)
        raise


def _parse_aspect_ratio(value: Any) -> tuple[int, int] | None:
    """Parse a customer ratio without introducing floating-point tolerance.

    Decoded media dimensions are integers.  Keeping the declared ratio as an
    integer pair lets admission use cross multiplication, so a nearby ratio
    such as ``1777:1000`` can never pass as ``16:9``.
    """

    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    if not all(
        part.isascii() and part.isdigit() and 1 <= len(part) <= 9
        for part in parts
    ):
        return None
    numerator, denominator = (int(part) for part in parts)
    if numerator <= 0 or denominator <= 0:
        return None
    return numerator, denominator


def _dimensions_match_ratio(
    width_px: int,
    height_px: int,
    ratio: tuple[int, int],
) -> bool:
    numerator, denominator = ratio
    return width_px * denominator == height_px * numerator


def _dimensions_share_ratio(
    left_width_px: int,
    left_height_px: int,
    right_width_px: int,
    right_height_px: int,
) -> bool:
    return left_width_px * right_height_px == left_height_px * right_width_px


def _apply_input_asset_row_lock(statement: Any, *, dialect_name: str) -> Any:
    """Apply the production row lock while keeping SQLite tests executable.

    Every task admission, package seal and disable operation acquires mutable
    InputAsset rows in ascending id order. PostgreSQL holds these locks until
    the endpoint transaction has linked/reserved the task. SQLite has no row
    level ``FOR UPDATE`` primitive, so its already-serialized test transaction
    keeps the same query/order without emitting unsupported syntax.
    """

    if dialect_name == "postgresql":
        return statement.with_for_update()
    return statement


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
    _INPUT_ROLE_MEDIA_TYPE = {
        "reference_image": "image",
        "first_frame": "image",
        "last_frame": "image",
        "reference_video": "video",
        "director_previs": "video",
        "driving_audio": "audio",
    }

    @staticmethod
    def _scope_values(
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
    ) -> tuple[str, str]:
        if (company_id is None) == (personal_workspace_id is None):
            raise RuntimeError(
                "An input asset must belong to exactly one workspace scope"
            )
        if company_id is not None:
            return "company", company_id
        assert personal_workspace_id is not None
        return "personal", personal_workspace_id

    @staticmethod
    def _scope_filters(
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
    ) -> tuple[Any, Any]:
        scope_kind, scope_id = InputAssetService._scope_values(
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        if scope_kind == "company":
            return (
                InputAsset.company_id == scope_id,
                InputAsset.personal_workspace_id.is_(None),
            )
        return (
            InputAsset.company_id.is_(None),
            InputAsset.personal_workspace_id == scope_id,
        )

    @staticmethod
    def _lock_statement(session: Session, statement: Any) -> Any:
        return _apply_input_asset_row_lock(
            statement,
            dialect_name=session.get_bind().dialect.name,
        )

    @classmethod
    def lock_scope_assets(
        cls,
        session: Session,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        asset_ids: list[str] | tuple[str, ...] | set[str],
        require_active: bool,
    ) -> list[InputAsset]:
        """Resolve scoped assets under the canonical mutable-row lock order."""

        ordered_ids = sorted(set(asset_ids))
        if not ordered_ids:
            return []
        statement = (
            select(InputAsset)
            .where(
                *cls._scope_filters(
                    company_id=company_id,
                    personal_workspace_id=personal_workspace_id,
                ),
                InputAsset.id.in_(ordered_ids),
            )
            .order_by(InputAsset.id)
        )
        if require_active:
            statement = statement.where(
                InputAsset.status == InputAssetStatus.ACTIVE
            )
        statement = cls._lock_statement(session, statement)
        return list(session.scalars(statement).all())

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
            trusted_metadata = _inspect_trusted_media(
                temporary_path,
                media_type=artifact.media_type,
                content_type=artifact.content_type,
            )

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
                **(trusted_metadata.model_fields() if trusted_metadata else {}),
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
        normalization_profile: str | None,
    ) -> InputAsset:
        if normalization_profile == _DIRECTOR_PREVIS_PROFILE:
            matches = (
                existing.source_task_artifact_id is None
                and existing.normalization_profile == normalization_profile
                and existing.source_sha256 == sha256
                and existing.original_filename
                == _normalized_previs_filename(original_filename)
                and existing.media_type == "video"
                and existing.content_type == "video/mp4"
            )
        else:
            matches = (
                existing.source_task_artifact_id is None
                and existing.normalization_profile is None
                and existing.source_sha256 is None
                and existing.original_filename == original_filename
                and existing.media_type == media_type
                and existing.content_type == content_type
                and existing.size_bytes == size_bytes
                and existing.sha256 == sha256
            )
        if not matches:
            raise ConflictError(
                "Idempotency-Key was already used for a different input asset"
            )
        return existing

    @staticmethod
    def create_from_upload(
        session: Session,
        *,
        store: InputAssetStore,
        company_id: str | None,
        personal_workspace_id: str | None = None,
        user_id: str,
        upload: UploadFile,
        requested_media_type: str | None,
        max_bytes: int,
        idempotency_key: str | None = None,
        normalization_profile: NormalizationProfile | None = None,
    ) -> InputAsset:
        scope_kind, scope_id = InputAssetService._scope_values(
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
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
        if normalization_profile is not None and normalization_profile != (
            _DIRECTOR_PREVIS_PROFILE
        ):
            raise DomainError(
                "Unsupported input asset normalization profile",
                "unsupported_input_asset_normalization_profile",
                422,
            )
        if normalization_profile == _DIRECTOR_PREVIS_PROFILE and (
            inferred_media_type != "video" or content_type != "video/webm"
        ):
            raise DomainError(
                "director_previs_mp4_v1 only accepts host WebM video",
                "director_previs_source_type_mismatch",
                422,
            )
        if (
            normalization_profile == _DIRECTOR_PREVIS_PROFILE
            and idempotency_key is None
        ):
            raise DomainError(
                "director_previs_mp4_v1 requires Idempotency-Key",
                "director_previs_idempotency_key_required",
                422,
            )

        temporary_path: Path | None = None
        normalized_path: Path | None = None
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

            uploaded_sha256 = digest.hexdigest()
            if idempotency_key is not None:
                existing = session.scalar(
                    select(InputAsset).where(
                        *InputAssetService._scope_filters(
                            company_id=company_id,
                            personal_workspace_id=personal_workspace_id,
                        ),
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
                        sha256=uploaded_sha256,
                        normalization_profile=normalization_profile,
                    )

            trusted_metadata = _inspect_trusted_media(
                temporary_path,
                media_type=inferred_media_type,
                content_type=content_type,
            )
            stored_path = temporary_path
            stored_filename = original_filename
            stored_media_type = inferred_media_type
            stored_content_type = content_type
            stored_size_bytes = size_bytes
            stored_sha256 = uploaded_sha256
            source_sha256: str | None = None
            if normalization_profile == _DIRECTOR_PREVIS_PROFILE:
                assert trusted_metadata is not None
                (
                    normalized_path,
                    trusted_metadata,
                    stored_size_bytes,
                    stored_sha256,
                ) = _normalize_director_previs(
                    temporary_path,
                    source_metadata=trusted_metadata,
                    source_size_bytes=size_bytes,
                )
                stored_path = normalized_path
                stored_filename = _normalized_previs_filename(original_filename)
                stored_media_type = "video"
                stored_content_type = "video/mp4"
                source_sha256 = uploaded_sha256
                if stored_sha256 == source_sha256:
                    raise DomainError(
                        "Normalized director previs must be a distinct derived asset",
                        "director_previs_normalized_digest_invalid",
                        422,
                    )

            asset_id = new_id()
            object_key = (
                f"inputs/{scope_id}/{asset_id}"
                if scope_kind == "company"
                else f"inputs/personal/{scope_id}/{asset_id}"
            )
            asset = InputAsset(
                id=asset_id,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
                uploaded_by_user_id=user_id,
                idempotency_key=idempotency_key,
                original_filename=stored_filename,
                media_type=stored_media_type,
                content_type=stored_content_type,
                size_bytes=stored_size_bytes,
                sha256=stored_sha256,
                **(trusted_metadata.model_fields() if trusted_metadata else {}),
                normalization_profile=normalization_profile,
                source_sha256=source_sha256,
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
                        *InputAssetService._scope_filters(
                            company_id=company_id,
                            personal_workspace_id=personal_workspace_id,
                        ),
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
                    sha256=uploaded_sha256,
                    normalization_profile=normalization_profile,
                )

            stored = store.put_file(
                object_key,
                stored_path,
                content_type=stored_content_type,
                size_bytes=stored_size_bytes,
                sha256=stored_sha256,
            )
            if (
                stored.size_bytes != stored_size_bytes
                or stored.sha256 != stored_sha256
            ):
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
            if normalized_path is not None:
                try:
                    normalized_path.unlink(missing_ok=True)
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
    def list_personal(
        session: Session,
        *,
        personal_workspace_id: str,
        status: InputAssetStatus | None = InputAssetStatus.ACTIVE,
        media_type: str | None = None,
        limit: int = 200,
    ) -> list[InputAsset]:
        statement = select(InputAsset).where(
            InputAsset.company_id.is_(None),
            InputAsset.personal_workspace_id == personal_workspace_id,
        )
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
    def get_personal_asset(
        session: Session,
        *,
        personal_workspace_id: str,
        asset_id: str,
        require_active: bool = True,
    ) -> InputAsset:
        statement = select(InputAsset).where(
            InputAsset.id == asset_id,
            InputAsset.company_id.is_(None),
            InputAsset.personal_workspace_id == personal_workspace_id,
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
        locked = InputAssetService.lock_scope_assets(
            session,
            company_id=company_id,
            personal_workspace_id=None,
            asset_ids=[asset_id],
            require_active=False,
        )
        if not locked:
            raise NotFoundError("Input asset does not exist")
        asset = locked[0]
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
    def disable_personal(
        session: Session,
        *,
        personal_workspace_id: str,
        asset_id: str,
    ) -> InputAsset:
        locked = InputAssetService.lock_scope_assets(
            session,
            company_id=None,
            personal_workspace_id=personal_workspace_id,
            asset_ids=[asset_id],
            require_active=False,
        )
        if not locked:
            raise NotFoundError("Input asset does not exist")
        asset = locked[0]
        if asset.status == InputAssetStatus.DISABLED:
            return asset
        active_reference = session.scalar(
            select(TaskInputAsset.task_id)
            .join(GenerationTask, GenerationTask.id == TaskInputAsset.task_id)
            .where(
                TaskInputAsset.asset_id == asset_id,
                GenerationTask.company_id.is_(None),
                GenerationTask.personal_workspace_id == personal_workspace_id,
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
    def canonicalize_task_payload(
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize asset references without consulting mutable asset state.

        This pure step is intentionally usable before an idempotency replay
        lookup.  An already accepted task must remain replayable after one of
        its inputs is disabled, while every genuinely new request still goes
        through the ACTIVE, tenant and media-type checks below.
        """

        raw_assets = request_payload.get("assets", [])
        if raw_assets is None:
            raw_assets = []
        if not isinstance(raw_assets, list):
            raise ConflictError("request_payload.assets must be a list")
        if len(raw_assets) > 15:
            raise ConflictError("A generation task supports at most 15 input assets")

        requested: list[tuple[str, str, str | None]] = []
        seen: set[str] = set()
        for reference in raw_assets:
            if not isinstance(reference, dict):
                raise ConflictError("Every input asset reference must be an object")
            reference_keys = set(reference)
            if reference_keys not in (
                {"asset_id", "media_type"},
                {"asset_id", "media_type", "role"},
            ):
                raise ConflictError(
                    "Every input asset must contain asset_id, media_type and an optional role"
                )
            asset_id = _valid_uuid_string(reference.get("asset_id"))
            media_type = reference.get("media_type")
            if asset_id is None or media_type not in {"image", "video", "audio"}:
                raise ConflictError("Input asset reference is invalid")
            role = reference.get("role")
            if role is not None:
                expected_media_type = InputAssetService._INPUT_ROLE_MEDIA_TYPE.get(role)
                if expected_media_type is None:
                    raise ConflictError("Input asset role is invalid")
                if expected_media_type != media_type:
                    raise ConflictError("Input asset role does not match media_type")
            if asset_id in seen:
                raise ConflictError("Duplicate input asset references are not allowed")
            seen.add(asset_id)
            requested.append((asset_id, media_type, role))

        normalized = dict(request_payload)
        normalized["assets"] = [
            {
                "asset_id": asset_id,
                "media_type": media_type,
                **({"role": role} if role is not None else {}),
            }
            for asset_id, media_type, role in requested
        ]
        return normalized

    @staticmethod
    def normalize_task_payload(
        session: Session,
        *,
        company_id: str | None,
        request_payload: dict[str, Any],
        personal_workspace_id: str | None = None,
        lock_for_task_admission: bool = True,
    ) -> tuple[dict[str, Any], list[InputAsset]]:
        scope_kind, _ = InputAssetService._scope_values(
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
        )
        normalized = InputAssetService.canonicalize_task_payload(
            request_payload
        )
        requested = [
            (
                reference["asset_id"],
                reference["media_type"],
                reference.get("role"),
            )
            for reference in normalized["assets"]
        ]

        if not requested:
            return normalized, []

        # Hold every mutable input row through task insert, links, reservation
        # and outbox creation (the request dependency commits afterwards).
        # Disable follows the same ascending-id lock order, closing the former
        # ACTIVE-read / task-link TOCTOU window.
        requested_ids = [asset_id for asset_id, _, _ in requested]
        if lock_for_task_admission:
            assets = InputAssetService.lock_scope_assets(
                session,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
                asset_ids=requested_ids,
                require_active=True,
            )
        else:
            # Dispatcher is deliberately read-only. It resolves an already
            # linked non-terminal task whose inputs cannot be disabled through
            # the public service, so it must not require UPDATE/FOR UPDATE.
            assets = list(
                session.scalars(
                    select(InputAsset)
                    .where(
                        *InputAssetService._scope_filters(
                            company_id=company_id,
                            personal_workspace_id=personal_workspace_id,
                        ),
                        InputAsset.id.in_(sorted(set(requested_ids))),
                        InputAsset.status == InputAssetStatus.ACTIVE,
                    )
                    .order_by(InputAsset.id)
                ).all()
            )
        by_id = {asset.id: asset for asset in assets}
        if len(by_id) != len(requested):
            # Do not reveal whether a missing identifier belongs to another company.
            raise NotFoundError(
                "One or more active input assets do not exist in this "
                + ("company" if scope_kind == "company" else "personal workspace")
            )
        ordered: list[InputAsset] = []
        canonical_references: list[dict[str, str]] = []
        temporal_frame_dimensions: dict[str, tuple[int, int]] = {}
        for asset_id, requested_media_type, role in requested:
            asset = by_id[asset_id]
            if asset.media_type != requested_media_type:
                raise ConflictError(
                    "Input asset media_type does not match stored metadata"
                )
            if asset.normalization_profile == _DIRECTOR_PREVIS_PROFILE:
                raise ConflictError(
                    "Normalized director previs is local-only until an exact "
                    "provider route accepts its media contract"
                )
            if asset.content_type in _LOCAL_ONLY_VIDEO_CONTENT_TYPES:
                # Browser-recorded StoryAI previs is WebM. Keep those bytes
                # available in the owning workspace for local review, but do
                # not let an omitted role wash out that provenance and turn
                # the file into a provider reference_video at dispatch time.
                # Published provider contracts currently accept MP4/MOV, not
                # WebM; a future route must qualify and name an exact WebM
                # input contract before this fail-closed boundary can move.
                raise ConflictError(
                    "WebM video is local-only and cannot be attached to a "
                    "provider-bound generation task"
                )
            if role == "director_previs":
                raise ConflictError(
                    "director_previs is a local preview and is not accepted by "
                    "a published provider route"
                )
            if role in {"first_frame", "last_frame"}:
                if (
                    asset.media_metadata_version
                    != _TRUSTED_MEDIA_METADATA_VERSION
                    or asset.width_px is None
                    or asset.height_px is None
                ):
                    raise ConflictError(
                        "Temporal frame assets require trusted decoded dimensions"
                    )
                temporal_frame_dimensions[role] = (
                    asset.width_px,
                    asset.height_px,
                )
            ordered.append(asset)
            canonical_references.append(
                {
                    "asset_id": asset.id,
                    "media_type": asset.media_type,
                    **({"role": role} if role is not None else {}),
                }
            )
        if temporal_frame_dimensions:
            task_ratio = _parse_aspect_ratio(normalized.get("aspect_ratio"))
            if task_ratio is None:
                raise ConflictError(
                    "Temporal frame assets require an explicit valid aspect_ratio"
                )
            if any(
                not _dimensions_match_ratio(width_px, height_px, task_ratio)
                for width_px, height_px in temporal_frame_dimensions.values()
            ):
                raise ConflictError(
                    "Temporal frame dimensions do not match task aspect_ratio"
                )
            first_dimensions = temporal_frame_dimensions.get("first_frame")
            last_dimensions = temporal_frame_dimensions.get("last_frame")
            if (
                first_dimensions is not None
                and last_dimensions is not None
                and not _dimensions_share_ratio(
                    *first_dimensions,
                    *last_dimensions,
                )
            ):
                raise ConflictError(
                    "First and last frame aspect ratios must match"
                )
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
        references: list[dict[str, Any]],
        store: InputAssetStore,
        signer: FilesystemInputAssetSigner | None,
        expires_seconds: int,
    ) -> list[dict[str, str]]:
        if len(assets) != len(references):
            raise RuntimeError("Resolved input assets lost their request ordering")
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
                **(
                    {"role": reference["role"]}
                    if reference.get("role") is not None
                    else {}
                ),
            }
            for asset, reference in zip(assets, references)
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
        self,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        references: list[dict[str, Any]],
        aspect_ratio: str | None,
    ) -> list[dict[str, str]]:
        # Revalidate against the accepted task's exact output, never a guessed
        # ratio or the current model defaults. Temporal frames require it even
        # though this read-only dispatcher only returns signed asset URLs.
        payload = {"assets": references, "aspect_ratio": aspect_ratio}
        with self._session_factory() as session:
            normalized, assets = InputAssetService.normalize_task_payload(
                session,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
                request_payload=payload,
                lock_for_task_admission=False,
            )
            return InputAssetService.relay_assets(
                assets=assets,
                references=normalized["assets"],
                store=self._store,
                signer=self._signer,
                expires_seconds=self._expires_seconds,
            )
