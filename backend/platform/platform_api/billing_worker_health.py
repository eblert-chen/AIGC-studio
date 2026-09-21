"""Local supervisor liveness, never a PSP or financial-reconciliation verdict."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time


def write_health(path: Path, *, healthy: bool, failed_jobs: tuple[str, ...], now: float | None = None) -> None:
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("Billing health path must be an absolute non-symlink file in an existing directory")
    payload = {"schema_version": 1, "checked_at": time.time() if now is None else now,
               "healthy": healthy, "failed_jobs": list(failed_jobs)}
    descriptor, temporary = tempfile.mkstemp(prefix=".billing-health-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_healthy(path: Path, *, max_age_seconds: float = 300, now: float | None = None) -> bool:
    try:
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or path.stat().st_size > 4096 or not 1 <= max_age_seconds <= 600):
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        checked = data.get("checked_at")
        current = time.time() if now is None else now
        return (type(data.get("schema_version")) is int and data["schema_version"] == 1
                and data.get("healthy") is True and data.get("failed_jobs") == []
                and type(checked) in {int, float} and 0 <= current - checked <= max_age_seconds)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local billing queue worker liveness only")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=float, default=300)
    args = parser.parse_args()
    raise SystemExit(0 if is_healthy(args.file, max_age_seconds=args.max_age_seconds) else 1)


if __name__ == "__main__":
    main()
