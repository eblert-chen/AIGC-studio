from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


PLATFORM_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLATFORM_ROOT / "scripts" / "platform_source_snapshot.py"
SPEC = importlib.util.spec_from_file_location("platform_source_snapshot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
snapshot_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot_module)


def _copy_snapshot_inputs(destination: Path) -> None:
    for directory_name in snapshot_module.INCLUDED_DIRECTORIES:
        shutil.copytree(PLATFORM_ROOT / directory_name, destination / directory_name)
    for relative in snapshot_module.INCLUDED_FILES:
        source = PLATFORM_ROOT.joinpath(*Path(relative).parts)
        target = destination.joinpath(*Path(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def test_platform_source_snapshot_is_deterministic_and_covers_runtime_bytes(
    tmp_path: Path,
) -> None:
    first = snapshot_module.compute_platform_source_snapshot(PLATFORM_ROOT)
    second = snapshot_module.compute_platform_source_snapshot(PLATFORM_ROOT)
    assert first == second
    assert first[0].startswith("sha256:") and len(first[0]) == 71
    assert first[1] > 100

    copied = tmp_path / "source"
    copied.mkdir()
    _copy_snapshot_inputs(copied)
    assert snapshot_module.compute_platform_source_snapshot(copied) == first
    target = copied / "platform_api" / "process_secrets.py"
    target.write_bytes(target.read_bytes() + b"\n# synthetic source drift\n")
    assert snapshot_module.compute_platform_source_snapshot(copied) != first


def test_platform_source_snapshot_rejects_symlinked_runtime_input(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "source"
    copied.mkdir()
    _copy_snapshot_inputs(copied)
    target = copied / "platform_api" / "synthetic-link.py"
    try:
        target.symlink_to(copied / "platform_api" / "process_secrets.py")
    except OSError:
        pytest.skip("symbolic links are not available to this test user")
    with pytest.raises(snapshot_module.SourceSnapshotError):
        snapshot_module.compute_platform_source_snapshot(copied)


def test_platform_source_snapshot_ignores_only_docker_excluded_bytecode(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "source"
    copied.mkdir()
    _copy_snapshot_inputs(copied)
    before = snapshot_module.compute_platform_source_snapshot(copied)
    cache = copied / "platform_api" / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "synthetic.cpython-312.pyc").write_bytes(b"synthetic-bytecode")
    assert snapshot_module.compute_platform_source_snapshot(copied) == before


def test_platform_source_snapshot_cli_fails_closed_on_wrong_expected_digest() -> None:
    assert (
        snapshot_module._main(
            [
                "--root",
                str(PLATFORM_ROOT),
                "--expected",
                "sha256:" + "0" * 64,
            ]
        )
        == 2
    )
