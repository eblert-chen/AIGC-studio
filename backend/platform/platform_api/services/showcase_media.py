from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import DomainError


_IMAGE_FORMAT_BY_CONTENT_TYPE = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_MAX_IMAGE_PIXELS = 40_000_000
_VIDEO_SANITIZE_TIMEOUT_SECONDS = 300
_MAX_VIDEO_DURATION_SECONDS = 300.0


@dataclass(frozen=True)
class SanitizedShowcaseMedia:
    path: Path
    content_type: str
    size_bytes: int
    sha256: str


def _fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size_bytes += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise DomainError(
            "Showcase media sanitizer could not read its output",
            "showcase_media_sanitizer_failed",
            503,
        ) from exc
    return size_bytes, digest.hexdigest()


def _output_path(suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix="platform-showcase-sanitized-",
        suffix=suffix,
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path


def _sanitize_image(
    source_path: Path,
    *,
    content_type: str,
    max_bytes: int,
) -> SanitizedShowcaseMedia:
    output_path = _output_path(
        {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[
            content_type
        ]
    )
    try:
        try:
            with Image.open(source_path) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise DomainError(
                        "Animated images are not supported by the public showcase",
                        "unsupported_showcase_animation",
                        422,
                    )
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
                    raise DomainError(
                        "Showcase image dimensions are outside the safe limit",
                        "showcase_image_dimensions_invalid",
                        422,
                    )
                image.load()
                oriented = ImageOps.exif_transpose(image)
                has_alpha = oriented.mode in {"RGBA", "LA"} or (
                    oriented.mode == "P" and "transparency" in oriented.info
                )
                target_mode = (
                    "RGB"
                    if content_type == "image/jpeg" or not has_alpha
                    else "RGBA"
                )
                converted = oriented.convert(target_mode)
                # Copy pixels into a fresh image so EXIF, ICC, XMP, comments,
                # thumbnails, and other source info cannot survive implicitly.
                clean = Image.new(target_mode, converted.size)
                clean.paste(converted)
                save_kwargs: dict[str, object]
                if content_type == "image/jpeg":
                    save_kwargs = {
                        "quality": 92,
                        "optimize": True,
                        "progressive": True,
                    }
                elif content_type == "image/png":
                    save_kwargs = {"optimize": True, "compress_level": 9}
                else:
                    save_kwargs = {"lossless": True, "quality": 100, "method": 6}
                clean.save(
                    output_path,
                    format=_IMAGE_FORMAT_BY_CONTENT_TYPE[content_type],
                    **save_kwargs,
                )
                clean.close()
                converted.close()
        except DomainError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise DomainError(
                "Showcase image could not be safely decoded",
                "invalid_showcase_image",
                422,
            ) from exc
        size_bytes, sha256 = _fingerprint(output_path)
        if size_bytes <= 0 or size_bytes > max_bytes:
            raise DomainError(
                "Sanitized showcase image exceeds the configured limit",
                "showcase_media_too_large",
                413,
            )
        return SanitizedShowcaseMedia(
            path=output_path,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _sanitize_video(
    source_path: Path,
    *,
    max_bytes: int,
) -> SanitizedShowcaseMedia:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise DomainError(
            "Showcase video sanitizer is unavailable",
            "showcase_media_sanitizer_unavailable",
            503,
        )

    def probe_duration(path: Path) -> float:
        try:
            with path.open("rb") as source:
                probe = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "pipe,data",
                        "-i",
                        "pipe:0",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                    ],
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=_VIDEO_SANITIZE_TIMEOUT_SECONDS,
                    check=False,
                )
            duration = float(probe.stdout.decode("ascii", errors="strict").strip())
        except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as exc:
            raise DomainError(
                "Generated video metadata could not be safely inspected",
                "invalid_showcase_video",
                422,
            ) from exc
        if (
            probe.returncode != 0
            or not math.isfinite(duration)
            or duration <= 0
            or duration > _MAX_VIDEO_DURATION_SECONDS
        ):
            raise DomainError(
                "Generated video duration is outside the safe limit",
                "invalid_showcase_video",
                422,
            )
        return duration

    source_duration = probe_duration(source_path)
    output_path = _output_path(".mp4")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-protocol_whitelist",
        "pipe,data",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-sn",
        "-dn",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-threads",
        "2",
        "-movflags",
        "+faststart",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        "-fs",
        str(max_bytes + 1),
        str(output_path),
    ]
    try:
        try:
            with source_path.open("rb") as source:
                completed = subprocess.run(
                    command,
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=_VIDEO_SANITIZE_TIMEOUT_SECONDS,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DomainError(
                "Showcase video sanitizer did not complete",
                "showcase_media_sanitizer_failed",
                503,
            ) from exc
        if completed.returncode != 0:
            raise DomainError(
                "Generated video could not be safely normalized",
                "invalid_showcase_video",
                422,
            )
        output_duration = probe_duration(output_path)
        duration_tolerance = max(0.1, source_duration * 0.01)
        if abs(output_duration - source_duration) > duration_tolerance:
            raise DomainError(
                "Sanitized showcase video duration does not match its source",
                "showcase_video_truncated",
                502,
            )
        try:
            with output_path.open("rb") as sanitized_source:
                decoded = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-xerror",
                        "-protocol_whitelist",
                        "pipe,data",
                        "-i",
                        "pipe:0",
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-f",
                        "null",
                        "-",
                    ],
                    stdin=sanitized_source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=_VIDEO_SANITIZE_TIMEOUT_SECONDS,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DomainError(
                "Sanitized showcase video could not be fully decoded",
                "showcase_media_sanitizer_failed",
                503,
            ) from exc
        if decoded.returncode != 0:
            raise DomainError(
                "Sanitized showcase video failed full decode verification",
                "showcase_video_truncated",
                502,
            )
        size_bytes, sha256 = _fingerprint(output_path)
        if size_bytes <= 0 or size_bytes > max_bytes:
            raise DomainError(
                "Sanitized showcase video exceeds the configured limit",
                "showcase_media_too_large",
                413,
            )
        return SanitizedShowcaseMedia(
            path=output_path,
            content_type="video/mp4",
            size_bytes=size_bytes,
            sha256=sha256,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def sanitize_showcase_media(
    source_path: Path,
    *,
    content_type: str,
    max_bytes: int,
    trusted_generated_artifact: bool,
) -> SanitizedShowcaseMedia:
    """Create a metadata-free derivative; source bytes are never published."""

    if content_type in _IMAGE_FORMAT_BY_CONTENT_TYPE:
        return _sanitize_image(
            source_path,
            content_type=content_type,
            max_bytes=max_bytes,
        )
    if content_type in {"video/mp4", "video/webm"}:
        if not trusted_generated_artifact:
            raise DomainError(
                "Direct showcase video uploads are disabled; import a verified "
                "personal generated artifact instead",
                "direct_showcase_video_disabled",
                422,
            )
        return _sanitize_video(source_path, max_bytes=max_bytes)
    raise DomainError(
        "Showcase media type is not supported",
        "unsupported_showcase_media_type",
        422,
    )
