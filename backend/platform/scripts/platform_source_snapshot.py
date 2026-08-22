#!/usr/bin/env python3
"""Compute the canonical source snapshot embedded in the Platform image.

Only runtime/build inputs listed below participate.  The Dockerfile copies the
same inputs into a staging directory, verifies the caller-provided digest, and
installs the already-verified bytes into ``/app``.  This prevents a release
caller from attaching an arbitrary well-formed snapshot label to different
Platform source bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath


INCLUDED_DIRECTORIES = ("platform_api", "migrations")
INCLUDED_FILES = (
    ".dockerignore",
    "Dockerfile",
    "alembic.ini",
    "requirements.txt",
    "scripts/platform_source_snapshot.py",
)
_EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__"})
_EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})
_MAXIMUM_FILE_BYTES = 8 * 1024 * 1024


class SourceSnapshotError(RuntimeError):
    pass


def _regular_file_bytes(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SourceSnapshotError("Platform source snapshot input is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SourceSnapshotError("Platform source snapshot input is not a regular file")
    if metadata.st_size < 0 or metadata.st_size > _MAXIMUM_FILE_BYTES:
        raise SourceSnapshotError("Platform source snapshot input has an invalid size")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SourceSnapshotError("Platform source snapshot input cannot be read") from exc
    if len(data) != metadata.st_size:
        raise SourceSnapshotError("Platform source snapshot input changed while reading")
    return data


def _canonical_relative_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    normalized = PurePosixPath(*relative.parts).as_posix()
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise SourceSnapshotError("Platform source snapshot path is invalid")
    return normalized


def _iter_included_files(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for relative in INCLUDED_FILES:
        paths.append(root.joinpath(*PurePosixPath(relative).parts))
    for directory_name in INCLUDED_DIRECTORIES:
        directory = root / directory_name
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise SourceSnapshotError(
                "Platform source snapshot directory is unavailable"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SourceSnapshotError(
                "Platform source snapshot directory is invalid"
            )
        for current_root, directory_names, file_names in os.walk(
            directory, topdown=True, followlinks=False
        ):
            current = Path(current_root)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                candidate = current / name
                candidate_metadata = candidate.lstat()
                if name in _EXCLUDED_DIRECTORY_NAMES:
                    continue
                if stat.S_ISLNK(candidate_metadata.st_mode):
                    raise SourceSnapshotError(
                        "Platform source snapshot contains a symbolic link"
                    )
                if not stat.S_ISDIR(candidate_metadata.st_mode):
                    raise SourceSnapshotError(
                        "Platform source snapshot contains an invalid directory entry"
                    )
                kept_directories.append(name)
            directory_names[:] = kept_directories
            for name in sorted(file_names):
                candidate = current / name
                if candidate.suffix.lower() in _EXCLUDED_FILE_SUFFIXES:
                    continue
                paths.append(candidate)

    by_name: dict[str, Path] = {}
    for path in paths:
        relative = _canonical_relative_path(root, path)
        if relative in by_name:
            raise SourceSnapshotError("Platform source snapshot contains a duplicate path")
        by_name[relative] = path
    if not by_name:
        raise SourceSnapshotError("Platform source snapshot is empty")
    return tuple(by_name[name] for name in sorted(by_name))


def compute_platform_source_snapshot(root: Path) -> tuple[str, int]:
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    count = 0
    for path in _iter_included_files(root):
        data = _regular_file_bytes(path)
        relative = _canonical_relative_path(root, path)
        record = json.dumps(
            [relative, len(data), hashlib.sha256(data).hexdigest()],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest.update(record)
        digest.update(b"\n")
        count += 1
    return f"sha256:{digest.hexdigest()}", count


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        snapshot, file_count = compute_platform_source_snapshot(arguments.root)
        if arguments.expected is not None and arguments.expected != snapshot:
            raise SourceSnapshotError("Platform source snapshot digest mismatch")
    except (OSError, SourceSnapshotError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if arguments.json:
        print(
            json.dumps(
                {"sha256": snapshot, "file_count": file_count},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        print(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
