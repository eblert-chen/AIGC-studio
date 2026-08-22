"""Current Platform database-attestation facade.

Alembic revisions import a frozen behavior module directly. Runtime code uses
this facade to select the current revision without reinterpreting either
frozen historical migration.
"""

from __future__ import annotations

from types import MappingProxyType

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import database_privileges_behavior_v1 as _behavior_v1
from . import database_privileges_behavior_v2 as _behavior_v2
from . import database_privileges_behavior_v3 as _behavior_v3
from . import database_privileges_behavior_v4 as _behavior_v4
from . import database_privileges_behavior_v5 as _behavior_v5
from . import database_privileges_v1 as _policy_v1
from . import database_privileges_v2 as _policy_v2
from . import database_privileges_v3 as _policy_v3
from . import database_privileges_v4 as _policy_v4
from . import database_privileges_v5 as _policy_v5
from .database_privileges_behavior_v5 import (  # noqa: F401
    DatabasePrincipalEvidence,
    PlatformDatabaseAttestationError,
    PlatformDatabaseEvidence,
    assert_platform_database_manifest_matches_metadata,
    attest_platform_database,
    attest_platform_database_connection,
    collect_platform_database_evidence,
    expected_platform_table_acl,
    install_platform_database_connection_attestation,
    platform_catalog_sha256,
    platform_system_acl_sha256,
    validate_platform_database_acl_evidence,
    validate_platform_database_evidence,
    validate_privilege_manifest,
)


PLATFORM_DATABASE_PRIVILEGE_POLICY_REGISTRY = MappingProxyType(
    {
        _policy_v1.ALEMBIC_HEAD: (_policy_v1, _behavior_v1),
        _policy_v2.ALEMBIC_HEAD: (_policy_v2, _behavior_v2),
        _policy_v3.ALEMBIC_HEAD: (_policy_v3, _behavior_v3),
        _policy_v4.ALEMBIC_HEAD: (_policy_v4, _behavior_v4),
        _policy_v5.ALEMBIC_HEAD: (_policy_v5, _behavior_v5),
    }
)
CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY = _policy_v5
CURRENT_PLATFORM_DATABASE_PRIVILEGE_BEHAVIOR = _behavior_v5
PLATFORM_ALEMBIC_HEAD = _policy_v5.ALEMBIC_HEAD
PLATFORM_MIGRATION_DATABASE_ROLE = _policy_v5.MIGRATION_DATABASE_ROLE
PLATFORM_DATABASE_ROLE_BY_PROCESS = _policy_v5.DATABASE_ROLE_BY_PROCESS
PLATFORM_DATABASE_ROLE_COMMENT_BY_PROCESS = (
    _policy_v5.DATABASE_ROLE_COMMENT_BY_PROCESS
)
PLATFORM_TABLES = _policy_v5.TABLES
PLATFORM_DATABASE_PRIVILEGES_BY_PROCESS = _policy_v5.PRIVILEGES_BY_PROCESS
EXPECTED_PLATFORM_TABLE_ACL = _policy_v5.EXPECTED_TABLE_ACL
EXPECTED_PLATFORM_DATABASE_ACL = _policy_v5.EXPECTED_DATABASE_ACL
EXPECTED_PLATFORM_SCHEMA_ACL = _policy_v5.EXPECTED_SCHEMA_ACL


def _migration_source_policy(
    connection: Connection,
) -> tuple[object, object]:
    """Select the frozen target policy for one read-only source snapshot.

    The role predecessor and Alembic environment may enter each frozen policy
    only from that policy's exact predecessor. Route 0035 through v1, 0036
    through v2, 0037 through v3, and 0038 through v4. Route 0039, an empty
    database, the current head, multi-head, and unknown states through the
    current v5 gate. Each selected validator verifies the full normalized
    catalog fingerprint before Alembic can execute DDL.
    """

    table_names = frozenset(
        str(name)
        for name in connection.scalars(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='public' "
                "AND c.relkind IN ('r','p') ORDER BY c.relname"
            )
        )
    )
    if "alembic_version" not in table_names:
        return _policy_v5, _behavior_v5
    heads = tuple(
        str(head)
        for head in connection.scalars(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        )
    )
    if len(heads) == 1:
        for policy, behavior in (
            (_policy_v1, _behavior_v1),
            (_policy_v2, _behavior_v2),
            (_policy_v3, _behavior_v3),
            (_policy_v4, _behavior_v4),
        ):
            source_heads = set(policy.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD) - {
                policy.ALEMBIC_HEAD
            }
            if heads[0] in source_heads:
                return policy, behavior
    return _policy_v5, _behavior_v5


def validate_platform_migration_source_state(connection: Connection) -> None:
    """Validate a migration source using its exact frozen target policy."""

    try:
        _validated_migration_source_policy(connection)
    except _behavior_v5.PlatformDatabaseAttestationError:
        raise
    except _behavior_v1.PlatformDatabaseAttestationError as exc:
        # Runtime callers consume one facade error type regardless of which
        # historical source gate performed the read-only validation.
        raise PlatformDatabaseAttestationError(str(exc)) from None


def _validated_migration_source_policy(
    connection: Connection,
) -> tuple[object, object]:
    """Select and validate one frozen migration source on this connection."""

    policy, behavior = _migration_source_policy(connection)
    behavior.validate_platform_migration_source_state(
        connection,
        policy=policy,
    )
    return policy, behavior


def validate_platform_migration_database_evidence(
    connection: Connection,
) -> object:
    """Validate migration-only evidence with the frozen source policy.

    This deliberately has no process-role argument and is not used by API or
    worker connection hooks. It performs its own source gate on this same
    connection before collection, so no caller can bypass or race policy
    selection by invoking the evidence helper directly.
    """

    try:
        policy, behavior = _validated_migration_source_policy(connection)
        evidence = behavior.collect_platform_database_evidence(
            connection,
            policy=policy,
        )
        behavior.validate_platform_database_evidence(
            evidence,
            "migration",
            require_runtime_acl=False,
            require_head=evidence.alembic_heads == (policy.ALEMBIC_HEAD,),
            policy=policy,
        )
        return evidence
    except _behavior_v5.PlatformDatabaseAttestationError:
        raise
    except _behavior_v1.PlatformDatabaseAttestationError as exc:
        raise PlatformDatabaseAttestationError(str(exc)) from None
