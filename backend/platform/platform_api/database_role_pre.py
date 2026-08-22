"""One-shot protected PostgreSQL role and ownership predecessor.

The command is intentionally separate from Alembic and every long-lived
Platform process.  It snapshots its nine owner-only sources once, verifies the
global networkless isolation receipt, seals the committed CA bytes in memory,
and only then opens PostgreSQL.  All failures are deliberately value-free.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from types import MappingProxyType
from collections.abc import Iterator
from typing import Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from .database_privileges import (
    CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY as policy_current,
    PlatformDatabaseAttestationError,
    collect_platform_database_evidence,
    platform_catalog_sha256,
    platform_system_acl_sha256,
    validate_platform_database_evidence,
    validate_platform_migration_database_evidence,
    validate_platform_migration_source_state,
)
from .database_system_semantic_v1 import (
    PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST,
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
    platform_postgres16_allowed_extension_surface_is_exact,
    platform_postgres16_audit_configuration,
    platform_postgres16_privileged_shared_preload_manifest,
    platform_postgres16_release_evidence_is_qualified,
    platform_postgres16_system_semantic_sha256,
)
from .platform_database_release_proof import (
    PLATFORM_DATABASE_RELEASE_PROOF_ENV,
    PlatformDatabaseReleaseProofError,
    build_platform_database_release_proof,
    invalidate_platform_database_release_proof,
    write_platform_database_release_proof,
)
from .platform_secret_receipt import (
    PlatformSecretIsolationContext,
    verify_platform_secret_isolation_receipt_sources,
)
from .process_secrets import (
    PLATFORM_DATABASE_CA_FILE_ENV,
    PLATFORM_DATABASE_CA_FILE_ID,
    PlatformProcessSecretError,
    _database_endpoint,
    _database_password,
    _database_target,
    _read_protected_platform_source,
    materialize_verified_platform_database_ca,
    protected_platform_runtime_requested,
    read_protected_platform_database_ca_file,
    reject_raw_platform_secret_environment,
    rewrite_platform_database_url_ca_path,
)


PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE_ENV = (
    "PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE"
)
PLATFORM_DATABASE_NAME_ENV = "PLATFORM_DATABASE_NAME"
PLATFORM_DATABASE_ROLE_VALID_UNTIL_ENV = "PLATFORM_DATABASE_ROLE_VALID_UNTIL"
PLATFORM_LEGACY_DATABASE_OWNER_ENV = "PLATFORM_LEGACY_DATABASE_OWNER"
PLATFORM_DATABASE_ROLE_PRE_CONSUMER = "platform-db-role-pre"
PLATFORM_DATABASE_ROLE_PRE_PROCESS_ROLE = "database-role-pre"
PLATFORM_DATABASE_RELEASE_PROOF_PATH = (
    "/run/platform-database-release-proof/attestation.json"
)
PLATFORM_DATABASE_ROLE_ADMIN = "platform_role_admin"
PLATFORM_DATABASE_ROLE_ADMIN_COMMENT = "ai-video/platform-db-role-admin/v1"
PLATFORM_DATABASE_ROLE_ADMIN_FILE_ID = "platform_role_admin_dsn"

_MAXIMUM_ROLE_ADMIN_DSN_BYTES = 8192
_MAXIMUM_DATABASE_PASSWORD_BYTES = 128
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_VALID_UNTIL = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_SCRAM_VERIFIER = (
    r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/]{22}==\$"
    r"[A-Za-z0-9+/]{43}=:[A-Za-z0-9+/]{43}=$"
)
_PROVISION_LOCK_KEY = "ai-video/platform-db-role-pre/v1"
_PROVISION_LOCK_TIMEOUT = "5s"
_PROVISION_STATEMENT_TIMEOUT = "30s"


@dataclass(frozen=True)
class PlatformDatabasePasswordSource:
    process_role: str
    environment: str
    file_id: str
    semantic_id: str


_PASSWORD_SOURCES = tuple(
    PlatformDatabasePasswordSource(
        process_role=process_role,
        environment=environment,
        file_id=file_id,
        semantic_id=f"{prefix}.database.password_file",
    )
    for process_role, environment, file_id, prefix in (
        (
            "migration",
            "PLATFORM_MIGRATION_DATABASE_PASSWORD_FILE",
            "platform_migration_password",
            "platform.migration",
        ),
        (
            "platform-api",
            "PLATFORM_API_DATABASE_PASSWORD_FILE",
            "platform_api_password",
            "platform.api",
        ),
        (
            "dispatcher",
            "PLATFORM_DISPATCHER_DATABASE_PASSWORD_FILE",
            "platform_dispatcher_password",
            "platform.dispatcher",
        ),
        (
            "relay-sync",
            "PLATFORM_RELAY_SYNC_DATABASE_PASSWORD_FILE",
            "platform_relay_sync_password",
            "platform.relay_sync",
        ),
        (
            "timeout-worker",
            "PLATFORM_TIMEOUT_WORKER_DATABASE_PASSWORD_FILE",
            "platform_timeout_worker_password",
            "platform.timeout_worker",
        ),
        (
            "publishing-worker",
            "PLATFORM_PUBLISHING_WORKER_DATABASE_PASSWORD_FILE",
            "platform_publishing_worker_password",
            "platform.publishing_worker",
        ),
        (
            "download-gateway-registration-worker",
            "PLATFORM_DOWNLOAD_GATEWAY_WORKER_DATABASE_PASSWORD_FILE",
            "platform_download_gateway_worker_password",
            "platform.download_gateway_worker",
        ),
    )
)


@dataclass(frozen=True)
class PlatformDatabaseRolePreSources:
    role_admin_database_url: str
    target_database_name: str
    valid_until: str
    legacy_owner: str | None
    passwords: Mapping[str, str]
    isolation_context: PlatformSecretIsolationContext | None = None
    database_endpoint_sha256: str | None = None


class PlatformDatabaseRolePreError(RuntimeError):
    """A value-free protected role-pre failure."""


def _fail(invariant: str) -> None:
    raise PlatformDatabaseRolePreError(
        f"protected Platform database role predecessor failed: {invariant}"
    )


def _validate_platform_database_role_pre_invocation() -> None:
    """Authorize the one exact command and named-volume deletion scope."""

    if (
        os.environ.get("ENVIRONMENT") not in {"staging", "production"}
        or os.environ.get("PLATFORM_PROTECTED_RUNTIME") != "true"
        or os.environ.get("PLATFORM_PROCESS_ROLE")
        != PLATFORM_DATABASE_ROLE_PRE_PROCESS_ROLE
        or os.environ.get(PLATFORM_DATABASE_RELEASE_PROOF_ENV)
        != PLATFORM_DATABASE_RELEASE_PROOF_PATH
    ):
        _fail("role-pre invocation")


def _canonical_identifier(environment: str, *, required: bool) -> str | None:
    value = os.environ.get(environment)
    if value in {None, ""} and not required:
        return None
    if value is None or _IDENTIFIER.fullmatch(value) is None:
        _fail("environment identity")
    return value


def _canonical_valid_until() -> str:
    value = os.environ.get(PLATFORM_DATABASE_ROLE_VALID_UNTIL_ENV, "")
    if _VALID_UNTIL.fullmatch(value) is None:
        _fail("role credential validity")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        _fail("role credential validity")
    now = datetime.now(timezone.utc)
    if not now + timedelta(hours=24) < parsed <= now + timedelta(days=366):
        _fail("role credential validity")
    return value


def _parse_role_admin_dsn(
    raw: bytes,
    target_database_name: str,
) -> tuple[str, str, str]:
    try:
        value = raw.decode("utf-8", errors="strict")
        parsed = urlsplit(value)
        port = parsed.port
        query_items = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except (UnicodeDecodeError, ValueError):
        _fail("role-admin source")
    password = unquote(parsed.password or "")
    expected_query = urlencode(
        (
            ("sslmode", "verify-full"),
            ("sslrootcert", os.environ.get(PLATFORM_DATABASE_CA_FILE_ENV, "")),
        )
    )
    if (
        not value
        or value != value.strip()
        or len(raw) > _MAXIMUM_ROLE_ADMIN_DSN_BYTES
        or parsed.scheme != "postgresql+psycopg"
        or parsed.username != PLATFORM_DATABASE_ROLE_ADMIN
        or not parsed.hostname
        or port is None
        or not 1 <= port <= 65535
        or parsed.path != "/postgres"
        or parsed.fragment
        or query_items
        != [
            ("sslmode", "verify-full"),
            ("sslrootcert", os.environ.get(PLATFORM_DATABASE_CA_FILE_ENV, "")),
        ]
        or parsed.query != expected_query
    ):
        _fail("role-admin source")
    try:
        password = _database_password(password)
        target = _database_target(value, database_override=target_database_name)
        endpoint = _database_endpoint(value, database_override=target_database_name)
    except PlatformProcessSecretError:
        _fail("role-admin source")
    return password, target, endpoint


def _read_password_source(source: PlatformDatabasePasswordSource) -> tuple[bytes, str]:
    try:
        raw = _read_protected_platform_source(
            environment=source.environment,
            label="database password",
            maximum_bytes=_MAXIMUM_DATABASE_PASSWORD_BYTES,
        )
        password = _database_password(raw.decode("utf-8", errors="strict"))
    except (PlatformProcessSecretError, UnicodeDecodeError):
        _fail("database password source")
    return raw, password


def _replace_database(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/" + database_name,
            parsed.query,
            "",
        )
    )


def _replace_principal(
    database_url: str,
    *,
    database_name: str,
    username: str,
    password: str,
) -> str:
    parsed = urlsplit(database_url)
    host = parsed.hostname or ""
    host_port = f"[{host}]:{parsed.port}" if ":" in host else f"{host}:{parsed.port}"
    return urlunsplit(
        (
            parsed.scheme,
            f"{username}:{password}@{host_port}",
            "/" + database_name,
            parsed.query,
            "",
        )
    )


def load_platform_database_role_pre_sources() -> PlatformDatabaseRolePreSources:
    """Snapshot/verify all sources before importing a database side effect."""

    if not protected_platform_runtime_requested():
        _fail("protected runtime")
    reject_raw_platform_secret_environment()
    target_database_name = _canonical_identifier(
        PLATFORM_DATABASE_NAME_ENV,
        required=True,
    )
    assert target_database_name is not None
    if target_database_name in {"postgres", "template0", "template1"}:
        _fail("target database")
    valid_until = _canonical_valid_until()
    legacy_owner = _canonical_identifier(
        PLATFORM_LEGACY_DATABASE_OWNER_ENV,
        required=False,
    )
    if legacy_owner in {
        PLATFORM_DATABASE_ROLE_ADMIN,
        *policy_current.DATABASE_ROLE_BY_PROCESS.values(),
        "postgres",
    }:
        _fail("legacy owner")

    try:
        role_admin_raw = _read_protected_platform_source(
            environment=PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE_ENV,
            label="role-admin database credential",
            maximum_bytes=_MAXIMUM_ROLE_ADMIN_DSN_BYTES,
        )
        database_ca_raw = read_protected_platform_database_ca_file()
    except PlatformProcessSecretError:
        _fail("protected source")
    role_admin_password, target, endpoint = _parse_role_admin_dsn(
        role_admin_raw,
        target_database_name,
    )

    files: dict[str, bytes] = {
        PLATFORM_DATABASE_ROLE_ADMIN_FILE_ID: role_admin_raw,
        PLATFORM_DATABASE_CA_FILE_ID: database_ca_raw,
    }
    passwords: dict[str, str] = {}
    semantics = [
        {
            "id": "platform.role_admin.database.password",
            "sha256": hashlib.sha256(role_admin_password.encode("ascii")).hexdigest(),
        },
        {
            "id": "platform.role_admin.database.target",
            "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest(),
        },
        {
            "id": "platform.role_admin.database.endpoint",
            "sha256": hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
        },
    ]
    for source in _PASSWORD_SOURCES:
        raw, password = _read_password_source(source)
        files[source.file_id] = raw
        passwords[source.process_role] = password
        semantics.append(
            {
                "id": source.semantic_id,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    if len({role_admin_password, *passwords.values()}) != 1 + len(passwords):
        _fail("database password isolation")
    semantics.sort(key=lambda item: item["id"])
    try:
        isolation_context = verify_platform_secret_isolation_receipt_sources(
            consumer=PLATFORM_DATABASE_ROLE_PRE_CONSUMER,
            files=files,
            semantics=semantics,
        )
        snapshot_path = materialize_verified_platform_database_ca(database_ca_raw)
    except RuntimeError:
        _fail("secret isolation receipt")
    role_admin_database_url = rewrite_platform_database_url_ca_path(
        role_admin_raw.decode("utf-8"),
        snapshot_path=snapshot_path,
    )
    return PlatformDatabaseRolePreSources(
        role_admin_database_url=role_admin_database_url,
        target_database_name=target_database_name,
        valid_until=valid_until,
        legacy_owner=legacy_owner,
        passwords=MappingProxyType(passwords),
        isolation_context=isolation_context,
        database_endpoint_sha256=hashlib.sha256(
            endpoint.encode("utf-8")
        ).hexdigest(),
    )


def _engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        poolclass=NullPool,
        pool_pre_ping=False,
        connect_args={
            "application_name": "platform-db-role-pre",
            "connect_timeout": 10,
            "options": (
                "-c statement_timeout=30000 "
                "-c lock_timeout=5000"
            ),
        },
    )


@contextmanager
def _held_provision_lock(database_url: str) -> Iterator[Connection]:
    """Hold one bounded session lock across every preflight and transition."""

    engine = _engine(database_url)
    acquired = False
    try:
        with engine.connect() as connection:
            try:
                connection.exec_driver_sql(
                    f"SET statement_timeout='{_PROVISION_STATEMENT_TIMEOUT}'"
                )
                connection.exec_driver_sql(
                    f"SET lock_timeout='{_PROVISION_LOCK_TIMEOUT}'"
                )
                connection.commit()
                acquired = bool(
                    connection.scalar(
                        text(
                            "SELECT pg_try_advisory_lock("
                            "hashtextextended(:lock_key,0))"
                        ),
                        {"lock_key": _PROVISION_LOCK_KEY},
                    )
                )
                connection.commit()
            except Exception:
                _fail("provision lock")
            if not acquired:
                _fail("provision lock")
            try:
                yield connection
            finally:
                if connection.in_transaction():
                    connection.rollback()
                try:
                    connection.scalar(
                        text(
                            "SELECT pg_advisory_unlock("
                            "hashtextextended(:lock_key,0))"
                        ),
                        {"lock_key": _PROVISION_LOCK_KEY},
                    )
                    connection.commit()
                except Exception:
                    # Closing the physical session releases the lock.  Never
                    # replace an earlier fixed failure with driver diagnostics.
                    if connection.in_transaction():
                        connection.rollback()
    except PlatformDatabaseRolePreError:
        raise
    except Exception:
        _fail("provision lock")
    finally:
        engine.dispose()


def _psycopg_dsn(database_url: str) -> str:
    if not database_url.startswith("postgresql+psycopg://"):
        _fail("database driver")
    return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")


def _external_object_count(connection: Connection) -> int:
    return int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN "
                "('pg_default','pg_global')) + "
                "(SELECT count(*) FROM pg_foreign_data_wrapper) + "
                "(SELECT count(*) FROM pg_foreign_server) + "
                "(SELECT count(*) FROM pg_user_mappings) + "
                "(SELECT count(*) FROM pg_largeobject_metadata) + "
                "(SELECT count(*) FROM pg_publication) + "
                "(SELECT count(*) FROM pg_subscription) + "
                "(SELECT count(*) FROM pg_event_trigger event_trigger "
                "WHERE NOT EXISTS (SELECT 1 FROM pg_depend dependency "
                "JOIN pg_extension extension ON extension.oid=dependency.refobjid "
                "WHERE dependency.refclassid='pg_extension'::regclass "
                "AND dependency.classid='pg_event_trigger'::regclass "
                "AND dependency.objid=event_trigger.oid AND dependency.objsubid=0 "
                "AND dependency.deptype='e' AND extension.extname='pgaudit'))"
            )
        )
        or 0
    )


def _attest_connection_timeouts(connection: Connection) -> None:
    row = connection.execute(
        text(
            "SELECT current_setting('statement_timeout')::interval="
            "interval '30 seconds',"
            "current_setting('lock_timeout')::interval=interval '5 seconds'"
        )
    ).one()
    if not all(bool(value) for value in row):
        _fail("database timeout policy")


def _protected_owned_object_count(connection: Connection) -> int:
    roles = tuple(policy_current.DATABASE_ROLE_BY_PROCESS.values())
    parameters = {f"role_{index}": role for index, role in enumerate(roles)}
    placeholders = ",".join(f":role_{index}" for index in range(len(roles)))
    return int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_namespace n WHERE "
                f"pg_get_userbyid(n.nspowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_class c WHERE "
                f"pg_get_userbyid(c.relowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_proc p WHERE "
                f"pg_get_userbyid(p.proowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_type t WHERE "
                f"pg_get_userbyid(t.typowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_operator o WHERE "
                f"pg_get_userbyid(o.oprowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_opclass o WHERE "
                f"pg_get_userbyid(o.opcowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_opfamily o WHERE "
                f"pg_get_userbyid(o.opfowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_collation o WHERE "
                f"pg_get_userbyid(o.collowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_conversion o WHERE "
                f"pg_get_userbyid(o.conowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_ts_config o WHERE "
                f"pg_get_userbyid(o.cfgowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_ts_dict o WHERE "
                f"pg_get_userbyid(o.dictowner) IN ({placeholders})) + "
                "(SELECT count(*) FROM pg_statistic_ext o WHERE "
                f"pg_get_userbyid(o.stxowner) IN ({placeholders}))"
            ),
            parameters,
        )
        or 0
    )


def _attest_database_system_surface(
    connection: Connection,
    *,
    require_empty_public: bool,
) -> None:
    _attest_connection_timeouts(connection)
    environment = os.environ.get("ENVIRONMENT", "").strip().lower()
    if environment not in {"staging", "production"}:
        _fail("database release environment")
    identity = connection.execute(
        text(
            "SELECT current_setting('server_version_num')::integer, "
            "COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false), "
            "current_schema(), current_schemas(false)"
        )
    ).one()
    system_semantic_sha256 = platform_postgres16_system_semantic_sha256(
        connection
    )
    require_pgaudit = (
        environment == "production"
        or system_semantic_sha256
        == POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
    )
    extension_surface_exact = (
        platform_postgres16_allowed_extension_surface_is_exact(
            connection,
            require_pgaudit=require_pgaudit,
        )
    )
    pgaudit_preloaded, pgaudit_log_class_coverage = (
        platform_postgres16_audit_configuration(connection)
    )
    shared_preload_manifest = (
        platform_postgres16_privileged_shared_preload_manifest(connection)
    )
    expected_shared_preload_manifest = (
        PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST
        if system_semantic_sha256
        == POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
        else ""
    )
    if (
        not 160000 <= int(identity[0]) < 170000
        or not bool(identity[1])
        or identity[2] != "public"
        or tuple(identity[3] or ()) != ("public",)
        or platform_system_acl_sha256(connection)
        != policy_current.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256.get(
            system_semantic_sha256
        )
        or not platform_postgres16_release_evidence_is_qualified(
            environment=environment,
            system_semantic_sha256=system_semantic_sha256,
            extension_surface_exact=extension_surface_exact,
            pgaudit_preloaded=pgaudit_preloaded,
            pgaudit_log_class_coverage=pgaudit_log_class_coverage,
        )
        or shared_preload_manifest != expected_shared_preload_manifest
        or _external_object_count(connection)
    ):
        _fail("database system surface")
    if require_empty_public:
        if (
            platform_catalog_sha256(connection) != policy_current.EMPTY_CATALOG_SHA256
            or _protected_owned_object_count(connection)
        ):
            _fail("maintenance database surface")


def _attest_role_admin(connection: Connection) -> None:
    _attest_connection_timeouts(connection)
    exact = connection.scalar(
        text(
            "SELECT current_user=:role AND session_user=:role "
            "AND COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false) "
            "AND r.rolcanlogin AND r.rolsuper AND NOT r.rolinherit "
            "AND r.rolcreaterole AND r.rolcreatedb AND NOT r.rolreplication "
            "AND r.rolbypassrls AND r.rolconnlimit=1 "
            "AND r.rolvaliduntil > statement_timestamp()+interval '24 hours' "
            "AND r.rolvaliduntil <= statement_timestamp()+interval '31 days' "
            "AND shobj_description(r.oid,'pg_authid')=:comment "
            "AND a.rolpassword IS NOT NULL AND a.rolpassword ~ :verifier "
            "FROM pg_roles r JOIN pg_authid a ON a.oid=r.oid "
            "WHERE r.rolname=:role"
        ),
        {
            "role": PLATFORM_DATABASE_ROLE_ADMIN,
            "comment": PLATFORM_DATABASE_ROLE_ADMIN_COMMENT,
            "verifier": _SCRAM_VERIFIER,
        },
    )
    membership_or_setting = int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_auth_members m JOIN pg_roles p ON p.oid=m.roleid "
                "JOIN pg_roles c ON c.oid=m.member WHERE p.rolname=:role OR c.rolname=:role) + "
                "(SELECT count(*) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole "
                "WHERE r.rolname=:role)"
            ),
            {"role": PLATFORM_DATABASE_ROLE_ADMIN},
        )
        or 0
    )
    if not exact or membership_or_setting:
        _fail("role-admin principal")


def _expected_principal_rows(connection: Connection, valid_until: str) -> int:
    expected_roles = tuple(policy_current.DATABASE_ROLE_BY_PROCESS.values())
    rows = connection.execute(
        text(
            "SELECT r.rolname, shobj_description(r.oid,'pg_authid'), "
            "r.rolcanlogin,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,"
            "r.rolreplication,r.rolbypassrls,r.rolconnlimit,"
            "r.rolvaliduntil,"
            "r.rolvaliduntil > statement_timestamp(),"
            "r.rolvaliduntil <= CAST(:valid_until AS timestamptz),"
            "a.rolpassword IS NOT NULL AND a.rolpassword ~ :verifier "
            "FROM pg_roles r JOIN pg_authid a ON a.oid=r.oid "
            "WHERE r.rolname = ANY(CAST(:roles AS text[])) ORDER BY r.rolname"
        ),
        {
            "valid_until": valid_until,
            "verifier": _SCRAM_VERIFIER,
            "roles": list(expected_roles),
        },
    ).all()
    if not rows:
        return 0
    process_by_role = {
        role: process_role
        for process_role, role in policy_current.DATABASE_ROLE_BY_PROCESS.items()
    }
    if len(rows) != len(expected_roles):
        _fail("partial principal inventory")
    # Credential maintenance is a monotonic, all-seven transition.  The
    # server proves one exact shared current expiry; the desired file-free
    # release value may replay it or extend it, never repair arbitrary drift,
    # shorten it, revive an expired role, or create split expiries.
    current_expiries = {row[10] for row in rows}
    if len(current_expiries) != 1:
        _fail("principal credential generation")
    for row in rows:
        process_role = process_by_role.get(str(row[0]))
        if process_role is None or (
            row[1] != policy_current.DATABASE_ROLE_COMMENT_BY_PROCESS[process_role]
            or not bool(row[2])
            or bool(row[3])
            or bool(row[4])
            or bool(row[5])
            or bool(row[6])
            or bool(row[7])
            or bool(row[8])
            or int(row[9])
            != policy_current.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[process_role]
            or not bool(row[10])
            or not bool(row[11])
            or not bool(row[12])
            or not bool(row[13])
        ):
            _fail("principal properties")
    return len(rows)


def _database_inventory(
    connection: Connection,
    target_database_name: str,
    legacy_owner: str | None,
) -> bool:
    rows = connection.execute(
        text(
            "SELECT datname,datallowconn,datistemplate,pg_get_userbyid(datdba) "
            "FROM pg_database ORDER BY datname"
        )
    ).all()
    by_name = {str(row[0]): row for row in rows}
    allowed = {"postgres", "template0", "template1", target_database_name}
    if not set(by_name).issubset(allowed) or not {
        "postgres",
        "template0",
        "template1",
    }.issubset(by_name):
        _fail("dedicated database inventory")
    if (
        bool(by_name["template0"][1])
        or not bool(by_name["template0"][2])
        or not bool(by_name["template1"][2])
        or str(by_name["postgres"][3]) != "postgres"
        or str(by_name["template0"][3]) != "postgres"
        or str(by_name["template1"][3]) != "postgres"
    ):
        _fail("maintenance database inventory")
    target = by_name.get(target_database_name)
    if target is None:
        return False
    if not bool(target[1]) or bool(target[2]) or str(target[3]) not in {
        policy_current.MIGRATION_DATABASE_ROLE,
        legacy_owner,
    }:
        _fail("target database identity")
    return True


def _attest_cluster_role_inventory(
    connection: Connection,
    *,
    legacy_owner: str | None,
) -> None:
    allowed = {
        PLATFORM_DATABASE_ROLE_ADMIN,
        *policy_current.DATABASE_ROLE_BY_PROCESS.values(),
    }
    if legacy_owner is not None:
        allowed.add(legacy_owner)
    extras = {
        str(value)
        for value in connection.scalars(
            text(
                "SELECT rolname FROM pg_roles WHERE rolname<>'postgres' "
                "AND rolname NOT LIKE 'pg\\_%' ESCAPE '\\'"
            )
        )
    } - allowed
    if extras:
        _fail("dedicated role inventory")


def _attest_logging_boundary(connection: Connection) -> None:
    row = connection.execute(
        text(
            "SELECT current_setting('log_parameter_max_length')='0',"
            "current_setting('log_parameter_max_length_on_error')='0',"
            "COALESCE(NULLIF(current_setting("
            "'auto_explain.log_parameter_max_length',true),''),'0')='0',"
            "lower(COALESCE(NULLIF(current_setting("
            "'pgaudit.log_parameter',true),''),'off'))='off'"
        )
    ).one()
    if not all(bool(value) for value in row):
        _fail("credential logging policy")


def _target_head(connection: Connection) -> str | None:
    table_exists = bool(
        connection.scalar(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        )
    )
    if not table_exists:
        return None
    heads = tuple(
        str(value)
        for value in connection.scalars(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        )
    )
    if len(heads) != 1:
        _fail("target migration head")
    return heads[0]


def _attest_target_owner_surface(
    connection: Connection,
    *,
    expected_owner: str,
) -> None:
    row = connection.execute(
        text(
            "SELECT pg_get_userbyid(d.datdba),pg_get_userbyid(n.nspowner) "
            "FROM pg_database d JOIN pg_namespace n ON n.nspname='public' "
            "WHERE d.datname=current_database()"
        )
    ).one()
    if str(row[0]) != expected_owner or str(row[1]) not in {
        expected_owner,
        "pg_database_owner",
    }:
        _fail("target ownership")
    foreign_count = int(
        connection.scalar(
            text(
                "SELECT "
                "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(c.relowner)<>:owner) + "
                "(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(p.proowner)<>:owner) + "
                "(SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(t.typowner)<>:owner) + "
                "(SELECT count(*) FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.oprowner)<>:owner) + "
                "(SELECT count(*) FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.opcowner)<>:owner) + "
                "(SELECT count(*) FROM pg_opfamily o JOIN pg_namespace n ON n.oid=o.opfnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.opfowner)<>:owner) + "
                "(SELECT count(*) FROM pg_collation o JOIN pg_namespace n ON n.oid=o.collnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.collowner)<>:owner) + "
                "(SELECT count(*) FROM pg_conversion o JOIN pg_namespace n ON n.oid=o.connamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.conowner)<>:owner) + "
                "(SELECT count(*) FROM pg_ts_config o JOIN pg_namespace n ON n.oid=o.cfgnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.cfgowner)<>:owner) + "
                "(SELECT count(*) FROM pg_ts_dict o JOIN pg_namespace n ON n.oid=o.dictnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.dictowner)<>:owner) + "
                "(SELECT count(*) FROM pg_statistic_ext o JOIN pg_namespace n ON n.oid=o.stxnamespace "
                "WHERE n.nspname='public' AND pg_get_userbyid(o.stxowner)<>:owner)"
            ),
            {"owner": expected_owner},
        )
        or 0
    )
    if foreign_count:
        _fail("target object ownership")


@dataclass(frozen=True)
class _PreflightState:
    protected_principal_count: int
    target_exists: bool
    target_head: str | None
    target_owner: str | None


def _maintenance_preflight_state(
    connection: Connection,
    sources: PlatformDatabaseRolePreSources,
) -> tuple[int, bool]:
    _attest_role_admin(connection)
    _attest_logging_boundary(connection)
    _attest_cluster_role_inventory(
        connection,
        legacy_owner=sources.legacy_owner,
    )
    principal_count = _expected_principal_rows(
        connection,
        sources.valid_until,
    )
    target_exists = _database_inventory(
        connection,
        sources.target_database_name,
        sources.legacy_owner,
    )
    _attest_database_system_surface(
        connection,
        require_empty_public=True,
    )
    return principal_count, target_exists


def _target_preflight_state(
    connection: Connection,
    sources: PlatformDatabaseRolePreSources,
) -> tuple[str | None, str]:
    _attest_logging_boundary(connection)
    _attest_database_system_surface(
        connection,
        require_empty_public=False,
    )
    validate_platform_migration_source_state(connection)
    source_evidence = collect_platform_database_evidence(connection)
    for count in (
        source_evidence.membership_count,
        source_evidence.role_setting_count,
        source_evidence.parameter_acl_count,
        source_evidence.external_owned_object_count,
        source_evidence.system_acl_count,
        source_evidence.system_unsafe_object_count,
        source_evidence.public_unsafe_object_count,
        source_evidence.legacy_pending_work_count,
        source_evidence.column_acl_count,
    ):
        if count:
            _fail("target pre-migration surface")
    target_head = _target_head(connection)
    target_owner = str(
        connection.scalar(
            text(
                "SELECT pg_get_userbyid(datdba) FROM pg_database "
                "WHERE datname=current_database()"
            )
        )
    )
    _attest_target_owner_surface(
        connection,
        expected_owner=target_owner,
    )
    if target_owner == policy_current.MIGRATION_DATABASE_ROLE:
        if target_head == policy_current.ALEMBIC_HEAD:
            evidence = replace(
                source_evidence,
                current_user=policy_current.MIGRATION_DATABASE_ROLE,
                session_user=policy_current.MIGRATION_DATABASE_ROLE,
            )
            validate_platform_database_evidence(
                evidence,
                "migration",
                require_runtime_acl=False,
                require_head=True,
            )
    elif sources.legacy_owner is None or target_owner != sources.legacy_owner:
        _fail("legacy ownership contract")
    return target_head, target_owner


def _preflight(
    sources: PlatformDatabaseRolePreSources,
    maintenance_connection: Connection | None = None,
) -> _PreflightState:
    if maintenance_connection is None:
        maintenance_engine = _engine(sources.role_admin_database_url)
        try:
            with maintenance_engine.connect() as connection:
                return _preflight(sources, connection)
        finally:
            maintenance_engine.dispose()

    principal_count, target_exists = _maintenance_preflight_state(
        maintenance_connection,
        sources,
    )
    template_engine = _engine(
        _replace_database(sources.role_admin_database_url, "template1")
    )
    try:
        with template_engine.connect() as connection:
            _attest_database_system_surface(
                connection,
                require_empty_public=True,
            )
    finally:
        template_engine.dispose()

    target_head: str | None = None
    target_owner: str | None = None
    if target_exists:
        target_engine = _engine(
            _replace_database(
                sources.role_admin_database_url,
                sources.target_database_name,
            )
        )
        try:
            with target_engine.connect() as connection:
                target_head, target_owner = _target_preflight_state(
                    connection,
                    sources,
                )
        except PlatformDatabaseAttestationError:
            _fail("target database attestation")
        finally:
            target_engine.dispose()
    elif sources.legacy_owner is not None:
        _fail("legacy database is unavailable")
    return _PreflightState(
        protected_principal_count=principal_count,
        target_exists=target_exists,
        target_head=target_head,
        target_owner=target_owner,
    )


def generate_scram_sha256_verifier(password: str, *, salt: bytes | None = None) -> str:
    """Generate the PostgreSQL SCRAM verifier without sending cleartext SQL."""

    try:
        normalized = _database_password(password)
    except PlatformProcessSecretError:
        _fail("database password")
    actual_salt = secrets.token_bytes(16) if salt is None else salt
    if len(actual_salt) != 16:
        _fail("SCRAM salt")
    salted = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("ascii"),
        actual_salt,
        4096,
    )
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return (
        "SCRAM-SHA-256$4096:"
        + base64.b64encode(actual_salt).decode("ascii")
        + "$"
        + base64.b64encode(stored_key).decode("ascii")
        + ":"
        + base64.b64encode(server_key).decode("ascii")
    )


def _install_logging_guards(cursor: psycopg.Cursor[object]) -> None:
    for statement in (
        "SET LOCAL log_statement='none'",
        "SET LOCAL log_min_error_statement='PANIC'",
        "SET LOCAL log_min_duration_statement=-1",
        "SET LOCAL log_min_duration_sample=-1",
        "SET LOCAL log_statement_sample_rate=0",
        "SET LOCAL log_transaction_sample_rate=0",
        "SET LOCAL log_parameter_max_length=0",
        "SET LOCAL log_parameter_max_length_on_error=0",
        "SET LOCAL auto_explain.log_parameter_max_length=0",
        "SET LOCAL pgaudit.log_parameter=off",
    ):
        cursor.execute(statement, prepare=True)
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pgaudit')",
        prepare=True,
    )
    if bool(cursor.fetchone()[0]):
        cursor.execute("SET LOCAL pgaudit.log='none'", prepare=True)
    cursor.execute(
        "SELECT current_setting('log_statement')='none' "
        "AND lower(current_setting('log_min_error_statement'))='panic' "
        "AND current_setting('log_min_duration_statement')::integer=-1 "
        "AND current_setting('log_min_duration_sample')::integer=-1 "
        "AND current_setting('log_statement_sample_rate')::double precision=0 "
        "AND current_setting('log_transaction_sample_rate')::double precision=0 "
        "AND current_setting('log_parameter_max_length')::integer=0 "
        "AND current_setting('log_parameter_max_length_on_error')::integer=0 "
        "AND current_setting('auto_explain.log_parameter_max_length')::integer=0 "
        "AND lower(current_setting('pgaudit.log_parameter'))='off'",
        prepare=True,
    )
    if not bool(cursor.fetchone()[0]):
        _fail("credential logging guards")


def _install_role_passwords(
    cursor: psycopg.Cursor[object],
    verifiers: Mapping[str, str],
) -> None:
    """Install verifiers while keeping them out of the top-level SQL text.

    PostgreSQL 16 rejects a bind in the ALTER ROLE PASSWORD grammar
    (``PASSWORD $1``).  This transaction-local helper makes the observable
    top-level command a prepared SELECT with bound role/verifier parameters;
    its SPI command runs only after the fixed logging/audit guards above.
    """

    allowed_roles = sql.SQL(",").join(
        sql.Literal(role) for role in policy_current.DATABASE_ROLE_BY_PROCESS.values()
    )
    cursor.execute(
        sql.SQL(
            "CREATE OR REPLACE FUNCTION "
            "pg_temp.platform_install_role_password_v1("
            "role_name text, verifier text) RETURNS void "
            "LANGUAGE plpgsql SECURITY INVOKER "
            "SET search_path TO pg_catalog,pg_temp AS $body$ "
            "BEGIN "
            "IF NOT role_name = ANY(ARRAY[{}]::text[]) THEN "
            "RAISE EXCEPTION 'role password target rejected'; "
            "END IF; "
            "IF verifier !~ {} THEN "
            "RAISE EXCEPTION 'role password verifier rejected'; "
            "END IF; "
            "EXECUTE format('ALTER ROLE %I PASSWORD %L',role_name,verifier); "
            "END $body$"
        ).format(allowed_roles, sql.Literal(_SCRAM_VERIFIER)),
        prepare=True,
    )
    for process_role, database_role in policy_current.DATABASE_ROLE_BY_PROCESS.items():
        cursor.execute(
            "SELECT pg_temp.platform_install_role_password_v1(%s,%s)",
            (database_role, verifiers[process_role]),
            prepare=True,
        )


def _require_unchanged_maintenance_state(
    connection: Connection,
    sources: PlatformDatabaseRolePreSources,
    *,
    expected_principal_count: int,
    expected_target_exists: bool,
) -> None:
    principal_count, target_exists = _maintenance_preflight_state(
        connection,
        sources,
    )
    if (
        principal_count != expected_principal_count
        or target_exists is not expected_target_exists
    ):
        _fail("concurrent database state")


def _provision_principals(
    sources: PlatformDatabaseRolePreSources,
    state: _PreflightState,
    maintenance_connection: Connection,
) -> None:
    verifiers = {
        process_role: generate_scram_sha256_verifier(password)
        for process_role, password in sources.passwords.items()
    }
    try:
        if maintenance_connection.in_transaction():
            maintenance_connection.rollback()
        with maintenance_connection.begin():
            # This is deliberately in the same transaction and immediately
            # before the first role DDL, not merely at process start.
            _require_unchanged_maintenance_state(
                maintenance_connection,
                sources,
                expected_principal_count=state.protected_principal_count,
                expected_target_exists=state.target_exists,
            )
            driver_connection = maintenance_connection.connection.driver_connection
            with driver_connection.cursor() as cursor:
                if state.protected_principal_count == 0:
                    for process_role, database_role in (
                        policy_current.DATABASE_ROLE_BY_PROCESS.items()
                    ):
                        cursor.execute(
                            sql.SQL(
                                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER "
                                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                                "CONNECTION LIMIT {} VALID UNTIL {}"
                            ).format(
                                sql.Identifier(database_role),
                                sql.Literal(
                                    policy_current.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[
                                        process_role
                                    ]
                                ),
                                sql.Literal(sources.valid_until),
                            ),
                            prepare=True,
                        )
                        cursor.execute(
                            sql.SQL("COMMENT ON ROLE {} IS {}").format(
                                sql.Identifier(database_role),
                                sql.Literal(
                                    policy_current.DATABASE_ROLE_COMMENT_BY_PROCESS[
                                        process_role
                                    ]
                                ),
                            ),
                            prepare=True,
                        )
                for database_role in policy_current.DATABASE_ROLE_BY_PROCESS.values():
                    cursor.execute(
                        sql.SQL("ALTER ROLE {} VALID UNTIL {}").format(
                            sql.Identifier(database_role),
                            sql.Literal(sources.valid_until),
                        ),
                        prepare=True,
                    )
                _install_logging_guards(cursor)
                _install_role_passwords(cursor, verifiers)
                cursor.execute(
                    "SELECT count(*)=%s AND bool_and("
                    "rolvaliduntil=CAST(%s AS timestamptz) AND rolpassword ~ %s) "
                    "FROM pg_authid WHERE rolname=ANY(%s)",
                    (
                        len(policy_current.DATABASE_ROLE_BY_PROCESS),
                        sources.valid_until,
                        _SCRAM_VERIFIER,
                        list(policy_current.DATABASE_ROLE_BY_PROCESS.values()),
                    ),
                    prepare=True,
                )
                if not bool(cursor.fetchone()[0]):
                    _fail("SCRAM verifier postcondition")
    except PlatformDatabaseRolePreError:
        raise
    except Exception:
        _fail("principal transaction")


def _create_target_database(
    sources: PlatformDatabaseRolePreSources,
    state: _PreflightState,
) -> None:
    template_engine = _engine(
        _replace_database(sources.role_admin_database_url, "template1")
    )
    try:
        # Recheck the template immediately before the non-transactional
        # CREATE DATABASE.  The actual template is template0; template1 must
        # nevertheless remain an exact empty maintenance database.
        with template_engine.connect() as template_connection:
            _attest_database_system_surface(
                template_connection,
                require_empty_public=True,
            )
    finally:
        template_engine.dispose()

    maintenance_engine = _engine(sources.role_admin_database_url)
    try:
        with maintenance_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql(
                f"SET statement_timeout='{_PROVISION_STATEMENT_TIMEOUT}'"
            )
            connection.exec_driver_sql(
                f"SET lock_timeout='{_PROVISION_LOCK_TIMEOUT}'"
            )
            # Same physical session, immediately adjacent to CREATE DATABASE.
            _require_unchanged_maintenance_state(
                connection,
                sources,
                expected_principal_count=len(policy_current.DATABASE_ROLE_BY_PROCESS),
                expected_target_exists=state.target_exists,
            )
            driver_connection = connection.connection.driver_connection
            with driver_connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
                        sql.Identifier(sources.target_database_name),
                        sql.Identifier(policy_current.MIGRATION_DATABASE_ROLE),
                    ),
                    prepare=True,
                )
    except Exception:
        _fail("target database creation")
    finally:
        maintenance_engine.dispose()


def _normalize_cluster_database_acl(
    sources: PlatformDatabaseRolePreSources,
    maintenance_connection: Connection,
) -> None:
    try:
        if maintenance_connection.in_transaction():
            maintenance_connection.rollback()
        with maintenance_connection.begin():
            _require_unchanged_maintenance_state(
                maintenance_connection,
                sources,
                expected_principal_count=len(policy_current.DATABASE_ROLE_BY_PROCESS),
                expected_target_exists=True,
            )
            driver_connection = maintenance_connection.connection.driver_connection
            with driver_connection.cursor() as cursor:
                for database_name in (
                    "postgres",
                    "template0",
                    "template1",
                    sources.target_database_name,
                ):
                    cursor.execute(
                        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                            sql.Identifier(database_name)
                        ),
                        prepare=True,
                    )
                    for database_role in policy_current.DATABASE_ROLE_BY_PROCESS.values():
                        cursor.execute(
                            sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                                sql.Identifier(database_name),
                                sql.Identifier(database_role),
                            ),
                            prepare=True,
                        )
                for process_role, database_role in (
                    policy_current.DATABASE_ROLE_BY_PROCESS.items()
                ):
                    cursor.execute(
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                            sql.Identifier(sources.target_database_name),
                            sql.Identifier(database_role),
                        ),
                        prepare=True,
                    )
    except Exception:
        _fail("database ACL transaction")


def _handoff_target_ownership(
    sources: PlatformDatabaseRolePreSources,
    state: _PreflightState,
) -> None:
    target_url = _replace_database(
        sources.role_admin_database_url,
        sources.target_database_name,
    )
    target_engine = _engine(target_url)
    try:
        with target_engine.connect() as connection:
            with connection.begin():
                try:
                    target_head, target_owner = _target_preflight_state(
                        connection,
                        sources,
                    )
                except PlatformDatabaseAttestationError:
                    _fail("target database attestation")
                if (
                    target_head != state.target_head
                    or target_owner != state.target_owner
                ):
                    _fail("concurrent target state")
                driver_connection = connection.connection.driver_connection
                with driver_connection.cursor() as cursor:
                    if state.target_owner == sources.legacy_owner:
                        if sources.legacy_owner is None:
                            _fail("legacy owner")
                        cursor.execute(
                            sql.SQL("REASSIGN OWNED BY {} TO {}").format(
                                sql.Identifier(sources.legacy_owner),
                                sql.Identifier(policy_current.MIGRATION_DATABASE_ROLE),
                            ),
                            prepare=True,
                        )
                        cursor.execute(
                            sql.SQL("DROP OWNED BY {}").format(
                                sql.Identifier(sources.legacy_owner)
                            ),
                            prepare=True,
                        )
                        cursor.execute(
                            sql.SQL(
                                "ALTER ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                            ).format(sql.Identifier(sources.legacy_owner)),
                            prepare=True,
                        )
                    cursor.execute(
                        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                            sql.Identifier(sources.target_database_name),
                            sql.Identifier(policy_current.MIGRATION_DATABASE_ROLE),
                        ),
                        prepare=True,
                    )
                    cursor.execute(
                        sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                            sql.Identifier(policy_current.MIGRATION_DATABASE_ROLE)
                        ),
                        prepare=True,
                    )
                    if state.target_head != policy_current.ALEMBIC_HEAD:
                        cursor.execute(
                            "REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC",
                            prepare=True,
                        )
    except PlatformDatabaseRolePreError:
        raise
    except Exception:
        _fail("ownership handoff transaction")
    finally:
        target_engine.dispose()


def _verify_role_logins(sources: PlatformDatabaseRolePreSources) -> None:
    for process_role, database_role in policy_current.DATABASE_ROLE_BY_PROCESS.items():
        database_url = _replace_principal(
            sources.role_admin_database_url,
            database_name=sources.target_database_name,
            username=database_role,
            password=sources.passwords[process_role],
        )
        engine = _engine(database_url)
        try:
            with engine.connect() as connection:
                _attest_connection_timeouts(connection)
                row = connection.execute(
                    text(
                        "SELECT current_user,session_user,"
                        "COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false)"
                    )
                ).one()
                if row[0] != database_role or row[1] != database_role or not row[2]:
                    _fail("runtime principal login")
                if process_role == "migration":
                    validate_platform_migration_source_state(connection)
                    validate_platform_migration_database_evidence(connection)
        except PlatformDatabaseAttestationError:
            _fail("runtime database attestation")
        except PlatformDatabaseRolePreError:
            raise
        except Exception:
            _fail("runtime principal login")
        finally:
            engine.dispose()


def _publish_platform_database_release_proof(
    sources: PlatformDatabaseRolePreSources,
) -> None:
    """Publish the final proof from the attested target database connection."""

    if (
        sources.isolation_context is None
        or sources.database_endpoint_sha256 is None
    ):
        # Direct unit/integration helpers may exercise the database transition
        # without the container bootstrap.  The protected command itself never
        # permits that path (``main`` checks the verified context explicitly).
        return
    target_engine = _engine(
        _replace_database(
            sources.role_admin_database_url,
            sources.target_database_name,
        )
    )
    try:
        with target_engine.connect() as connection:
            _attest_database_system_surface(
                connection,
                require_empty_public=False,
            )
            _attest_logging_boundary(connection)
            evidence = collect_platform_database_evidence(connection)
            proof = build_platform_database_release_proof(
                connection,
                environment=os.environ.get("ENVIRONMENT", ""),
                isolation=sources.isolation_context,
                database_endpoint_sha256=sources.database_endpoint_sha256,
                evidence=evidence,
            )
            write_platform_database_release_proof(proof)
    except (PlatformDatabaseRolePreError, PlatformDatabaseReleaseProofError):
        raise
    except Exception:
        _fail("database release proof")
    finally:
        target_engine.dispose()


def provision_platform_database_roles(
    sources: PlatformDatabaseRolePreSources,
) -> None:
    """Run the read-only cluster gate, then the bounded one-shot transition."""

    # The session lock is acquired before the first preflight and stays held
    # until every postcondition completes.  It serializes concurrent deploys;
    # each mutation also repeats its relevant read-only gate in the same
    # physical session/transaction to reject non-cooperating DBA drift.
    with _held_provision_lock(sources.role_admin_database_url) as maintenance:
        state = _preflight(sources, maintenance)
        _provision_principals(sources, state, maintenance)
        state = replace(
            state,
            protected_principal_count=len(policy_current.DATABASE_ROLE_BY_PROCESS),
        )
        if not state.target_exists:
            _create_target_database(sources, state)
            state = replace(
                state,
                target_exists=True,
                target_owner=policy_current.MIGRATION_DATABASE_ROLE,
            )
        _handoff_target_ownership(sources, state)
        _normalize_cluster_database_acl(sources, maintenance)
        _verify_role_logins(sources)
        # This is intentionally the last action while the cross-session
        # advisory lock is still held.  A failure before this point leaves the
        # generation without a reusable proof.
        _publish_platform_database_release_proof(sources)


def main() -> int:
    try:
        _validate_platform_database_role_pre_invocation()
        # Remove the previous generation before reading any secret source or
        # opening PostgreSQL.  A failed or killed predecessor therefore cannot
        # leave an older proof usable by a new unified commit marker.
        invalidate_platform_database_release_proof()
        sources = load_platform_database_role_pre_sources()
        if (
            sources.isolation_context is None
            or sources.database_endpoint_sha256 is None
        ):
            _fail("database release proof context")
        provision_platform_database_roles(sources)
    except (
        PlatformDatabaseReleaseProofError,
        PlatformDatabaseRolePreError,
        PlatformProcessSecretError,
    ):
        # No exception text contains source values; keep stderr fixed as an
        # additional defense against driver diagnostics embedding SQL.
        sys.stderr.write("protected Platform database role predecessor failed\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "platform_database_role_pre",
                "state": "provisioned",
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a container command
    raise SystemExit(main())
