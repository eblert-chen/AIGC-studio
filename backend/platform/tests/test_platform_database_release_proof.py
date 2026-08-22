from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from platform_api import platform_database_release_proof as release_proof
from platform_api import database_privileges_v1 as policy_v1
from platform_api.database_system_semantic_v1 import (
    PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST,
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
)
from platform_api.platform_database_release_proof import (
    PlatformDatabaseReleaseProof,
    PlatformDatabaseReleaseProofError,
    attest_platform_database_release_proof,
    build_platform_database_release_proof,
    canonical_platform_database_release_proof,
    invalidate_platform_database_release_proof,
    load_and_install_platform_database_release_proof,
    parse_platform_database_release_proof,
    write_platform_database_release_proof,
)
from platform_api.platform_secret_receipt import PlatformSecretIsolationContext


def _context() -> PlatformSecretIsolationContext:
    return PlatformSecretIsolationContext(
        run_id="a" * 64,
        generation="root-proof-present",
        root_proof_id="b" * 64,
        platform_image=(
            "registry.example.invalid/ai-video/platform@sha256:" + "c" * 64
        ),
        platform_source_revision="d" * 40,
        platform_source_snapshot_sha256="sha256:" + "e" * 64,
    )


def _proof() -> PlatformDatabaseReleaseProof:
    context = _context()
    return PlatformDatabaseReleaseProof(
        environment="production",
        run_id=context.run_id,
        generation=context.generation,
        root_proof_id=context.root_proof_id,
        platform_image=context.platform_image,
        platform_source_revision=context.platform_source_revision,
        platform_source_snapshot_sha256=(
            context.platform_source_snapshot_sha256
        ),
        database_endpoint_sha256="f" * 64,
        postmaster_start_time="2026-08-18T01:02:03.123456Z",
        config_load_time="2026-08-18T01:03:04.654321Z",
        system_semantic_sha256=(
            POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
        ),
        system_acl_sha256=policy_v1.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256[
            POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
        ],
        extension_surface_exact=True,
        shared_preload_libraries=(
            PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST
        ),
        pgaudit_log_class_coverage=True,
        credential_logging_policy_exact=True,
    )


def _evidence(proof: PlatformDatabaseReleaseProof | None = None):
    expected = proof or _proof()
    return SimpleNamespace(
        system_semantic_sha256=expected.system_semantic_sha256,
        system_acl_sha256=expected.system_acl_sha256,
        system_extension_surface_exact=expected.extension_surface_exact,
        pgaudit_preloaded=(
            expected.shared_preload_libraries
            == PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST
        ),
        pgaudit_log_class_coverage=expected.pgaudit_log_class_coverage,
        credential_logging_policy_exact=(
            expected.credential_logging_policy_exact
        ),
    )


@pytest.fixture(autouse=True)
def _reset_installed_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_proof, "_installed_proof", None)
    monkeypatch.setattr(release_proof, "_after_temp_fsync", None)


def test_proof_canonical_round_trip_is_closed_and_has_no_trailing_bytes() -> None:
    proof = _proof()
    raw = canonical_platform_database_release_proof(proof)
    assert raw[-1:] == b"}"
    assert parse_platform_database_release_proof(raw) == proof
    assert tuple(json.loads(raw)) == release_proof._PROOF_FIELD_ORDER


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw + b"\n",
        lambda raw: raw[:-1],
        lambda raw: raw.replace(
            b'"kind":"platform_database_release_proof"',
            b'"kind":"platform_database_release_proof","kind":"duplicate"',
        ),
        lambda raw: raw[:-1] + b',"unknown":true}',
        lambda raw: raw.replace(
            b'"credential_logging_policy_exact":true',
            b'"credential_logging_policy_exact":1',
        ),
    ],
)
def test_proof_rejects_trailing_truncated_duplicate_unknown_and_wrong_type(
    mutate,
) -> None:
    with pytest.raises(PlatformDatabaseReleaseProofError):
        parse_platform_database_release_proof(
            mutate(canonical_platform_database_release_proof(_proof()))
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "staging"),
        ("run_id", "9" * 64),
        ("root_proof_id", "8" * 64),
        (
            "platform_image",
            "registry.example.invalid/platform@sha256:" + "7" * 64,
        ),
        ("platform_source_revision", "6" * 40),
        ("platform_source_snapshot_sha256", "sha256:" + "5" * 64),
        ("database_endpoint_sha256", "4" * 64),
    ],
)
def test_consumer_rejects_stale_cross_release_and_different_database_proofs(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    raw = canonical_platform_database_release_proof(
        replace(_proof(), **{field: value})
    )
    monkeypatch.setattr(
        release_proof,
        "read_protected_platform_database_release_proof",
        lambda: raw,
    )
    with pytest.raises(PlatformDatabaseReleaseProofError):
        load_and_install_platform_database_release_proof(
            isolation=_context(),
            database_endpoint_sha256="f" * 64,
        )


def test_consumer_installs_only_the_exact_receipt_bound_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = _proof()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(
        release_proof,
        "read_protected_platform_database_release_proof",
        lambda: canonical_platform_database_release_proof(proof),
    )
    assert load_and_install_platform_database_release_proof(
        isolation=_context(),
        database_endpoint_sha256=proof.database_endpoint_sha256,
    ) == proof
    assert release_proof._installed_proof == proof


@pytest.mark.parametrize(
    "manifest",
    [
        "pgaudit",
        "pgaudit,pgaudit",
        "pgaudit,unknown_hook",
        "auto_explain,pgaudit,pg_stat_statements",
        "pgaudit,auto_explain",
    ],
)
def test_proof_rejects_noncanonical_or_nonexact_preload_manifests(
    manifest: str,
) -> None:
    with pytest.raises(PlatformDatabaseReleaseProofError):
        parse_platform_database_release_proof(
            canonical_platform_database_release_proof(
                replace(_proof(), shared_preload_libraries=manifest)
            )
        )


def test_same_connection_attestation_rejects_restart_reload_endpoint_or_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = _proof()
    monkeypatch.setattr(release_proof, "_installed_proof", proof)
    monkeypatch.setattr(
        release_proof,
        "platform_database_connection_endpoint_sha256",
        lambda _: proof.database_endpoint_sha256,
    )
    monkeypatch.setattr(
        release_proof,
        "platform_database_clock_identity",
        lambda _: (proof.postmaster_start_time, proof.config_load_time),
    )
    attest_platform_database_release_proof(object(), _evidence(proof))

    for clocks, endpoint, evidence in (
        (
            ("2026-08-18T01:02:04.123456Z", proof.config_load_time),
            proof.database_endpoint_sha256,
            _evidence(proof),
        ),
        (
            (proof.postmaster_start_time, "2026-08-18T01:03:05.654321Z"),
            proof.database_endpoint_sha256,
            _evidence(proof),
        ),
        (
            (proof.postmaster_start_time, proof.config_load_time),
            "0" * 64,
            _evidence(proof),
        ),
        (
            (proof.postmaster_start_time, proof.config_load_time),
            proof.database_endpoint_sha256,
            SimpleNamespace(
                **{
                    **vars(_evidence(proof)),
                    "credential_logging_policy_exact": False,
                }
            ),
        ),
    ):
        monkeypatch.setattr(
            release_proof,
            "platform_database_clock_identity",
            lambda _, clocks=clocks: clocks,
        )
        monkeypatch.setattr(
            release_proof,
            "platform_database_connection_endpoint_sha256",
            lambda _, endpoint=endpoint: endpoint,
        )
        with pytest.raises(PlatformDatabaseReleaseProofError):
            attest_platform_database_release_proof(object(), evidence)


def test_builder_uses_one_target_connection_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _proof()
    monkeypatch.setattr(
        release_proof,
        "platform_database_connection_endpoint_sha256",
        lambda _: expected.database_endpoint_sha256,
    )
    monkeypatch.setattr(
        release_proof,
        "platform_database_clock_identity",
        lambda _: (expected.postmaster_start_time, expected.config_load_time),
    )
    monkeypatch.setattr(
        release_proof,
        "platform_postgres16_privileged_shared_preload_manifest",
        lambda _: PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST,
    )
    assert build_platform_database_release_proof(
        object(),
        environment="production",
        isolation=_context(),
        database_endpoint_sha256=expected.database_endpoint_sha256,
        evidence=_evidence(expected),
    ) == expected


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="owner/mode/no-follow publication is a Linux container boundary",
)
def test_writer_invalidates_old_proof_and_publishes_owner_only_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "proof"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / "attestation.json"
    monkeypatch.setenv(release_proof.PLATFORM_DATABASE_RELEASE_PROOF_ENV, str(path))

    path.write_bytes(b"old-generation")
    os.chmod(path, 0o400)
    invalidate_platform_database_release_proof()
    assert not path.exists()
    write_platform_database_release_proof(_proof())
    metadata = path.stat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o400
    assert metadata.st_uid == os.geteuid()
    assert parse_platform_database_release_proof(path.read_bytes()) == _proof()
    assert list(directory.iterdir()) == [path]

    # No overwrite: an unexpected second writer cannot replace the committed
    # inode or leave a temporary entry behind.
    before = path.read_bytes()
    with pytest.raises(PlatformDatabaseReleaseProofError):
        write_platform_database_release_proof(
            replace(_proof(), root_proof_id="3" * 64)
        )
    assert path.read_bytes() == before
    assert list(directory.iterdir()) == [path]


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="owner/mode/no-follow publication is a Linux container boundary",
)
def test_writer_kill_window_and_symlink_leave_no_reusable_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "proof"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / "attestation.json"
    monkeypatch.setenv(release_proof.PLATFORM_DATABASE_RELEASE_PROOF_ENV, str(path))

    def killed_after_fsync() -> None:
        raise RuntimeError("synthetic kill boundary")

    monkeypatch.setattr(release_proof, "_after_temp_fsync", killed_after_fsync)
    with pytest.raises(RuntimeError, match="synthetic kill boundary"):
        write_platform_database_release_proof(_proof())
    assert not path.exists()
    assert list(directory.iterdir()) == []

    outside = tmp_path / "outside"
    outside.write_bytes(b"must-stay")
    path.symlink_to(outside)
    with pytest.raises(PlatformDatabaseReleaseProofError):
        invalidate_platform_database_release_proof()
    assert not path.exists()
    assert outside.read_bytes() == b"must-stay"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="owner/mode/no-follow publication is a Linux container boundary",
)
def test_writer_rejects_non_owner_only_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "proof"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    path = directory / "attestation.json"
    monkeypatch.setenv(release_proof.PLATFORM_DATABASE_RELEASE_PROOF_ENV, str(path))
    with pytest.raises(PlatformDatabaseReleaseProofError):
        invalidate_platform_database_release_proof()
