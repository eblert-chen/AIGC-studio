"""Non-secret, one-generation Platform database release proof.

The role predecessor is the only writer.  It commits the privileged PostgreSQL
settings that ordinary least-privilege roles cannot inspect.  Every downstream
process first verifies its unified secret-isolation receipt, snapshots this
proof from a read-only mount, and compares it with the same database connection
used for the runtime catalog attestation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .database_privileges import (
    CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY as policy_current,
)
from .database_system_semantic_v1 import (
    PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST,
    POSTGRES16_ALPINE_REHEARSAL_SYSTEM_SEMANTIC_SHA256,
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
    platform_postgres16_privileged_shared_preload_manifest,
)
from .platform_secret_receipt import (
    PlatformSecretIsolationContext,
    _read_protected_isolation_file,
)


PLATFORM_DATABASE_RELEASE_PROOF_ENV = "PLATFORM_DATABASE_RELEASE_PROOF_FILE"
PLATFORM_DATABASE_RELEASE_PROOF_KIND = "platform_database_release_proof"
PLATFORM_DATABASE_RELEASE_PROOF_SCHEMA_VERSION = 1

_MAXIMUM_PROOF_BYTES = 16 * 1024
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_PROOF_FIELD_ORDER = (
    "schema_version",
    "kind",
    "environment",
    "run_id",
    "generation",
    "root_proof_id",
    "platform_image",
    "platform_source_revision",
    "platform_source_snapshot_sha256",
    "database_endpoint_sha256",
    "postmaster_start_time",
    "config_load_time",
    "system_semantic_sha256",
    "system_acl_sha256",
    "extension_surface_exact",
    "shared_preload_libraries",
    "pgaudit_log_class_coverage",
    "credential_logging_policy_exact",
)
_PROOF_FIELDS = frozenset(_PROOF_FIELD_ORDER)


class PlatformDatabaseReleaseProofError(RuntimeError):
    """A value-free database release-proof failure."""


@dataclass(frozen=True)
class PlatformDatabaseReleaseProof:
    environment: str
    run_id: str
    generation: str
    root_proof_id: str
    platform_image: str
    platform_source_revision: str
    platform_source_snapshot_sha256: str
    database_endpoint_sha256: str
    postmaster_start_time: str
    config_load_time: str
    system_semantic_sha256: str
    system_acl_sha256: str
    extension_surface_exact: bool
    shared_preload_libraries: str
    pgaudit_log_class_coverage: bool
    credential_logging_policy_exact: bool


_installed_lock = Lock()
_installed_proof: PlatformDatabaseReleaseProof | None = None
_after_temp_fsync = None


def _invalid() -> None:
    raise PlatformDatabaseReleaseProofError(
        "protected Platform database release proof is invalid"
    )


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _canonical_document(proof: PlatformDatabaseReleaseProof) -> dict[str, Any]:
    return {
        "schema_version": PLATFORM_DATABASE_RELEASE_PROOF_SCHEMA_VERSION,
        "kind": PLATFORM_DATABASE_RELEASE_PROOF_KIND,
        "environment": proof.environment,
        "run_id": proof.run_id,
        "generation": proof.generation,
        "root_proof_id": proof.root_proof_id,
        "platform_image": proof.platform_image,
        "platform_source_revision": proof.platform_source_revision,
        "platform_source_snapshot_sha256": (
            proof.platform_source_snapshot_sha256
        ),
        "database_endpoint_sha256": proof.database_endpoint_sha256,
        "postmaster_start_time": proof.postmaster_start_time,
        "config_load_time": proof.config_load_time,
        "system_semantic_sha256": proof.system_semantic_sha256,
        "system_acl_sha256": proof.system_acl_sha256,
        "extension_surface_exact": proof.extension_surface_exact,
        "shared_preload_libraries": proof.shared_preload_libraries,
        "pgaudit_log_class_coverage": proof.pgaudit_log_class_coverage,
        "credential_logging_policy_exact": (
            proof.credential_logging_policy_exact
        ),
    }


def canonical_platform_database_release_proof(
    proof: PlatformDatabaseReleaseProof,
) -> bytes:
    return json.dumps(
        _canonical_document(proof),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") == value


def parse_platform_database_release_proof(
    raw: bytes,
) -> PlatformDatabaseReleaseProof:
    if not raw or len(raw) > _MAXIMUM_PROOF_BYTES:
        _invalid()
    try:
        decoded = raw.decode("ascii", errors="strict")
        document = json.loads(decoded, object_pairs_hook=_reject_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid()
    if not isinstance(document, dict) or set(document) != _PROOF_FIELDS:
        _invalid()
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"]
        != PLATFORM_DATABASE_RELEASE_PROOF_SCHEMA_VERSION
        or document["kind"] != PLATFORM_DATABASE_RELEASE_PROOF_KIND
        or document["environment"] not in {"staging", "production"}
        or not isinstance(document["run_id"], str)
        or _HEX_64.fullmatch(document["run_id"]) is None
        or document["generation"] != "root-proof-present"
        or not isinstance(document["root_proof_id"], str)
        or _HEX_64.fullmatch(document["root_proof_id"]) is None
        or not isinstance(document["platform_image"], str)
        or not document["platform_image"].isascii()
        or "@sha256:" not in document["platform_image"]
        or not isinstance(document["platform_source_revision"], str)
        or re.fullmatch(r"[0-9a-f]{40}", document["platform_source_revision"])
        is None
        or not isinstance(document["platform_source_snapshot_sha256"], str)
        or _SHA256.fullmatch(document["platform_source_snapshot_sha256"])
        is None
        or not isinstance(document["database_endpoint_sha256"], str)
        or _HEX_64.fullmatch(document["database_endpoint_sha256"]) is None
        or not _valid_timestamp(document["postmaster_start_time"])
        or not _valid_timestamp(document["config_load_time"])
        or not isinstance(document["system_semantic_sha256"], str)
        or _SHA256.fullmatch(document["system_semantic_sha256"]) is None
        or not isinstance(document["system_acl_sha256"], str)
        or _HEX_64.fullmatch(document["system_acl_sha256"]) is None
        or document["system_acl_sha256"]
        != policy_current.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256.get(
            document["system_semantic_sha256"]
        )
        or not isinstance(document["shared_preload_libraries"], str)
        or document["shared_preload_libraries"]
        not in {"", PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST}
        or (
            document["system_semantic_sha256"]
            == POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
            and document["shared_preload_libraries"]
            != PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST
        )
        or (
            document["system_semantic_sha256"]
            == POSTGRES16_ALPINE_REHEARSAL_SYSTEM_SEMANTIC_SHA256
            and document["shared_preload_libraries"] != ""
        )
        or any(
            type(document[field]) is not bool
            for field in (
                "extension_surface_exact",
                "pgaudit_log_class_coverage",
                "credential_logging_policy_exact",
            )
        )
        or not document["extension_surface_exact"]
        or not document["credential_logging_policy_exact"]
        or (
            document["environment"] == "production"
            and (
                document["system_semantic_sha256"]
                != POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
                or not document["pgaudit_log_class_coverage"]
            )
        )
        or (
            document["environment"] == "staging"
            and not (
                (
                    document["system_semantic_sha256"]
                    == POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
                    and document["pgaudit_log_class_coverage"]
                )
                or (
                    document["system_semantic_sha256"]
                    == POSTGRES16_ALPINE_REHEARSAL_SYSTEM_SEMANTIC_SHA256
                    and not document["pgaudit_log_class_coverage"]
                )
            )
        )
    ):
        _invalid()
    proof = PlatformDatabaseReleaseProof(
        **{field: document[field] for field in _PROOF_FIELD_ORDER[2:]}
    )
    if not hmac.compare_digest(raw, canonical_platform_database_release_proof(proof)):
        _invalid()
    return proof


def _proof_path() -> str:
    path = os.environ.get(PLATFORM_DATABASE_RELEASE_PROOF_ENV, "")
    if (
        not path
        or path != path.strip()
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
        or "\x00" in path
        or "\r" in path
        or "\n" in path
        or os.path.basename(path) != "attestation.json"
    ):
        _invalid()
    return path


def _open_proof_directory(path: str) -> tuple[int, str]:
    if not sys.platform.startswith("linux"):
        _invalid()
    directory, filename = os.path.split(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        _invalid()
    opened = os.fstat(descriptor)
    effective_uid = os.geteuid()
    if (
        not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid != effective_uid
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        os.close(descriptor)
        _invalid()
    return descriptor, filename


def invalidate_platform_database_release_proof() -> None:
    """Remove the previous generation before role-pre reads any source/DB."""

    path = _proof_path()
    directory, filename = _open_proof_directory(path)
    unsafe_old_entry = False
    try:
        for entry in os.listdir(directory):
            if entry != filename and not entry.startswith(".attestation."):
                _invalid()
            try:
                metadata = os.stat(entry, dir_fd=directory, follow_symlinks=False)
            except OSError:
                _invalid()
            if stat.S_ISDIR(metadata.st_mode):
                _invalid()
            if entry == filename and not stat.S_ISREG(metadata.st_mode):
                unsafe_old_entry = True
            try:
                os.unlink(entry, dir_fd=directory)
            except OSError:
                _invalid()
        os.fsync(directory)
    finally:
        os.close(directory)
    if unsafe_old_entry:
        _invalid()


def write_platform_database_release_proof(
    proof: PlatformDatabaseReleaseProof,
) -> None:
    raw = canonical_platform_database_release_proof(proof)
    # The writer and every reader share one closed canonical parser.  This
    # prevents a privileged producer bug from publishing a document that the
    # least-privilege consumers can never accept.
    parse_platform_database_release_proof(raw)
    path = _proof_path()
    directory, filename = _open_proof_directory(path)
    temporary = f".attestation.{os.getpid()}.{secrets.token_hex(16)}"
    descriptor: int | None = None
    linked = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o400, dir_fd=directory)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written < 1:
                _invalid()
            offset += written
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size != len(raw)
        ):
            _invalid()
        if _after_temp_fsync is not None:
            _after_temp_fsync()
        os.close(descriptor)
        descriptor = None
        # linkat is an atomic no-replace publication. An attacker cannot make
        # us overwrite an entry inserted after the initial invalidation.
        os.link(
            temporary,
            filename,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        linked = True
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    except PlatformDatabaseReleaseProofError:
        raise
    except OSError:
        _invalid()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not linked:
            try:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            except OSError:
                pass
        os.close(directory)


def read_protected_platform_database_release_proof() -> bytes:
    return _read_protected_isolation_file(
        PLATFORM_DATABASE_RELEASE_PROOF_ENV,
        "database release proof",
    )


def load_and_install_platform_database_release_proof(
    *,
    isolation: PlatformSecretIsolationContext,
    database_endpoint_sha256: str,
) -> PlatformDatabaseReleaseProof:
    proof = parse_platform_database_release_proof(
        read_protected_platform_database_release_proof()
    )
    environment = os.environ.get("ENVIRONMENT", "")
    expected = (
        environment,
        isolation.run_id,
        isolation.generation,
        isolation.root_proof_id,
        isolation.platform_image,
        isolation.platform_source_revision,
        isolation.platform_source_snapshot_sha256,
        database_endpoint_sha256,
    )
    actual = (
        proof.environment,
        proof.run_id,
        proof.generation,
        proof.root_proof_id,
        proof.platform_image,
        proof.platform_source_revision,
        proof.platform_source_snapshot_sha256,
        proof.database_endpoint_sha256,
    )
    if actual != expected:
        _invalid()
    global _installed_proof
    with _installed_lock:
        if _installed_proof is not None and _installed_proof != proof:
            _invalid()
        _installed_proof = proof
    return proof


def platform_database_connection_endpoint_sha256(connection: Connection) -> str:
    url = connection.engine.url
    host = (url.host or "").rstrip(".").lower()
    port = url.port or 5432
    database = (url.database or "").lstrip("/")
    if not host or not database or not 1 <= port <= 65535:
        _invalid()
    canonical = (
        "postgres-endpoint-v1\n"
        f"host={host}\n"
        f"port={port}\n"
        f"database={database}"
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def build_platform_database_release_proof(
    connection: Connection,
    *,
    environment: str,
    isolation: PlatformSecretIsolationContext,
    database_endpoint_sha256: str,
    evidence: Any,
) -> PlatformDatabaseReleaseProof:
    """Build a proof from the target connection immediately before publish."""

    if (
        environment not in {"staging", "production"}
        or _HEX_64.fullmatch(database_endpoint_sha256) is None
        or not hmac.compare_digest(
            platform_database_connection_endpoint_sha256(connection),
            database_endpoint_sha256,
        )
    ):
        _invalid()
    postmaster_start, config_load = platform_database_clock_identity(connection)
    shared_preload_manifest = (
        platform_postgres16_privileged_shared_preload_manifest(connection)
    )
    if shared_preload_manifest is None:
        _invalid()
    return PlatformDatabaseReleaseProof(
        environment=environment,
        run_id=isolation.run_id,
        generation=isolation.generation,
        root_proof_id=isolation.root_proof_id,
        platform_image=isolation.platform_image,
        platform_source_revision=isolation.platform_source_revision,
        platform_source_snapshot_sha256=(
            isolation.platform_source_snapshot_sha256
        ),
        database_endpoint_sha256=database_endpoint_sha256,
        postmaster_start_time=postmaster_start,
        config_load_time=config_load,
        system_semantic_sha256=evidence.system_semantic_sha256,
        system_acl_sha256=evidence.system_acl_sha256,
        extension_surface_exact=evidence.system_extension_surface_exact,
        shared_preload_libraries=shared_preload_manifest,
        pgaudit_log_class_coverage=evidence.pgaudit_log_class_coverage,
        credential_logging_policy_exact=(
            evidence.credential_logging_policy_exact
        ),
    )


def platform_database_clock_identity(
    connection: Connection,
) -> tuple[str, str]:
    row = connection.execute(
        text(
            "SELECT to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',"
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),"
            "to_char(pg_conf_load_time() AT TIME ZONE 'UTC',"
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
        )
    ).one()
    return str(row[0]), str(row[1])


def attest_platform_database_release_proof(
    connection: Connection,
    evidence: Any,
) -> None:
    with _installed_lock:
        proof = _installed_proof
    if proof is None:
        _invalid()
    postmaster_start, config_load = platform_database_clock_identity(connection)
    if (
        platform_database_connection_endpoint_sha256(connection)
        != proof.database_endpoint_sha256
        or postmaster_start != proof.postmaster_start_time
        or config_load != proof.config_load_time
        or evidence.system_semantic_sha256 != proof.system_semantic_sha256
        or evidence.system_acl_sha256 != proof.system_acl_sha256
        or evidence.system_extension_surface_exact
        is not proof.extension_surface_exact
        or evidence.pgaudit_preloaded
        is not (
            proof.shared_preload_libraries
            == PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST
        )
        or evidence.pgaudit_log_class_coverage
        is not proof.pgaudit_log_class_coverage
        or evidence.credential_logging_policy_exact
        is not proof.credential_logging_policy_exact
    ):
        _invalid()
