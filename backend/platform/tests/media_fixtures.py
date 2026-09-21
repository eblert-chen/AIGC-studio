from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile

from PIL import Image


@lru_cache(maxsize=32)
def valid_png_bytes(width: int = 16, height: int = 9) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(16, 32, 48)).save(
        output,
        format="PNG",
        optimize=True,
    )
    return output.getvalue()


def _render_video_bytes(
    *,
    suffix: str,
    width: int,
    height: int,
    fps: int,
    duration_seconds: float,
    codec: str,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="platform-test-media-") as directory:
        target = Path(directory) / f"fixture{suffix}"
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                f"color=c=0x102030:s={width}x{height}:"
                f"r={fps}:d={duration_seconds}"
            ),
            "-an",
            "-c:v",
            codec,
        ]
        if codec == "libvpx":
            command.extend(["-deadline", "realtime", "-cpu-used", "8", "-b:v", "300k"])
        else:
            command.extend(["-preset", "ultrafast", "-pix_fmt", "yuv420p"])
        command.append(str(target))
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        return target.read_bytes()


@lru_cache(maxsize=16)
def valid_webm_bytes(
    width: int = 320,
    height: int = 180,
    fps: int = 15,
    duration_seconds: float = 2.2,
) -> bytes:
    return _render_video_bytes(
        suffix=".webm",
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration_seconds,
        codec="libvpx",
    )


@lru_cache(maxsize=16)
def valid_mp4_bytes(
    width: int = 320,
    height: int = 180,
    fps: int = 24,
    duration_seconds: float = 2.2,
) -> bytes:
    return _render_video_bytes(
        suffix=".mp4",
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration_seconds,
        codec="libx264",
    )
