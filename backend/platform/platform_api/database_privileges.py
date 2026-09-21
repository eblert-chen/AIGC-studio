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
from . import database_privileges_behavior_v6 as _behavior_v6
from . import database_privileges_behavior_v7 as _behavior_v7
from . import database_privileges_behavior_v8 as _behavior_v8
from . import database_privileges_behavior_v9 as _behavior_v9
from . import database_privileges_behavior_v10 as _behavior_v10
from . import database_privileges_behavior_v11 as _behavior_v11
from . import database_privileges_behavior_v12 as _behavior_v12
from . import database_privileges_behavior_v13 as _behavior_v13
from . import database_privileges_behavior_v14 as _behavior_v14
from . import database_privileges_behavior_v15 as _behavior_v15
from . import database_privileges_behavior_v16 as _behavior_v16
from . import database_privileges_behavior_v17 as _behavior_v17
from . import database_privileges_behavior_v18 as _behavior_v18
from . import database_privileges_behavior_v19 as _behavior_v19
from . import database_privileges_behavior_v20 as _behavior_v20
from . import database_privileges_behavior_v21 as _behavior_v21
from . import database_privileges_behavior_v22 as _behavior_v22
from . import database_privileges_behavior_v23 as _behavior_v23
from . import database_privileges_behavior_v24 as _behavior_v24
from . import database_privileges_behavior_v25 as _behavior_v25
from . import database_privileges_v1 as _policy_v1
from . import database_privileges_v2 as _policy_v2
from . import database_privileges_v3 as _policy_v3
from . import database_privileges_v4 as _policy_v4
from . import database_privileges_v5 as _policy_v5
from . import database_privileges_v6 as _policy_v6
from . import database_privileges_v7 as _policy_v7
from . import database_privileges_v8 as _policy_v8
from . import database_privileges_v9 as _policy_v9
from . import database_privileges_v10 as _policy_v10
from . import database_privileges_v11 as _policy_v11
from . import database_privileges_v12 as _policy_v12
from . import database_privileges_v13 as _policy_v13
from . import database_privileges_v14 as _policy_v14
from . import database_privileges_v15 as _policy_v15
from . import database_privileges_v16 as _policy_v16
from . import database_privileges_v17 as _policy_v17
from . import database_privileges_v18 as _policy_v18
from . import database_privileges_v19 as _policy_v19
from . import database_privileges_v20 as _policy_v20
from . import database_privileges_v21 as _policy_v21
from . import database_privileges_v22 as _policy_v22
from . import database_privileges_v23 as _policy_v23
from . import database_privileges_v24 as _policy_v24
from . import database_privileges_v25 as _policy_v25
from .database_privileges_behavior_v25 import (  # noqa: F401
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
        _policy_v6.ALEMBIC_HEAD: (_policy_v6, _behavior_v6),
        _policy_v7.ALEMBIC_HEAD: (_policy_v7, _behavior_v7),
        _policy_v8.ALEMBIC_HEAD: (_policy_v8, _behavior_v8),
        _policy_v9.ALEMBIC_HEAD: (_policy_v9, _behavior_v9),
        _policy_v10.ALEMBIC_HEAD: (_policy_v10, _behavior_v10),
        _policy_v11.ALEMBIC_HEAD: (_policy_v11, _behavior_v11),
        _policy_v12.ALEMBIC_HEAD: (_policy_v12, _behavior_v12),
        _policy_v13.ALEMBIC_HEAD: (_policy_v13, _behavior_v13),
        _policy_v14.ALEMBIC_HEAD: (_policy_v14, _behavior_v14),
        _policy_v15.ALEMBIC_HEAD: (_policy_v15, _behavior_v15),
        _policy_v16.ALEMBIC_HEAD: (_policy_v16, _behavior_v16),
        _policy_v17.ALEMBIC_HEAD: (_policy_v17, _behavior_v17),
        _policy_v18.ALEMBIC_HEAD: (_policy_v18, _behavior_v18),
        _policy_v19.ALEMBIC_HEAD: (_policy_v19, _behavior_v19),
        _policy_v20.ALEMBIC_HEAD: (_policy_v20, _behavior_v20),
        _policy_v21.ALEMBIC_HEAD: (_policy_v21, _behavior_v21),
        _policy_v22.ALEMBIC_HEAD: (_policy_v22, _behavior_v22),
        _policy_v23.ALEMBIC_HEAD: (_policy_v23, _behavior_v23),
        _policy_v24.ALEMBIC_HEAD: (_policy_v24, _behavior_v24),
        _policy_v25.ALEMBIC_HEAD: (_policy_v25, _behavior_v25),
    }
)
CURRENT_PLATFORM_DATABASE_PRIVILEGE_POLICY = _policy_v25
CURRENT_PLATFORM_DATABASE_PRIVILEGE_BEHAVIOR = _behavior_v25
PLATFORM_ALEMBIC_HEAD = _policy_v25.ALEMBIC_HEAD
PLATFORM_MIGRATION_DATABASE_ROLE = _policy_v25.MIGRATION_DATABASE_ROLE
PLATFORM_DATABASE_ROLE_BY_PROCESS = _policy_v25.DATABASE_ROLE_BY_PROCESS
PLATFORM_DATABASE_ROLE_COMMENT_BY_PROCESS = (
    _policy_v25.DATABASE_ROLE_COMMENT_BY_PROCESS
)
PLATFORM_TABLES = _policy_v25.TABLES
PLATFORM_DATABASE_PRIVILEGES_BY_PROCESS = _policy_v25.PRIVILEGES_BY_PROCESS
EXPECTED_PLATFORM_TABLE_ACL = _policy_v25.EXPECTED_TABLE_ACL
EXPECTED_PLATFORM_DATABASE_ACL = _policy_v25.EXPECTED_DATABASE_ACL
EXPECTED_PLATFORM_SCHEMA_ACL = _policy_v25.EXPECTED_SCHEMA_ACL


def _migration_source_policy(
    connection: Connection,
) -> tuple[object, object]:
    """Select the frozen target policy for one read-only source snapshot.

    The role predecessor and Alembic environment may enter each frozen policy
    only from that policy's exact predecessor. Route 0035 through v1, 0036
    through v2, 0037 through v3, 0038 through v4, and 0039 through v5. Route
    0040 through frozen v6, 0041 through frozen v7, 0042 through frozen v8,
    0043 through frozen v9, 0044 through frozen v10, 0045 through frozen v11,
    0046 through frozen v12, 0047 through frozen v13, 0048 through frozen v14,
    revision 0057 through frozen v22, revision 0058 through frozen v23, and
    revision 0059 through frozen v24, and later revisions through the current
    v25 policy. Route an empty database, the current head, multi-head, and
    unknown states through the current v25 gate.
    Each selected validator verifies the full normalized catalog fingerprint
    before Alembic can execute DDL.
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
        return _policy_v25, _behavior_v25
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
            (_policy_v5, _behavior_v5),
            (_policy_v6, _behavior_v6),
            (_policy_v7, _behavior_v7),
            (_policy_v8, _behavior_v8),
            (_policy_v9, _behavior_v9),
            (_policy_v10, _behavior_v10),
            (_policy_v11, _behavior_v11),
            (_policy_v12, _behavior_v12),
            (_policy_v13, _behavior_v13),
            (_policy_v14, _behavior_v14),
            (_policy_v15, _behavior_v15),
            (_policy_v16, _behavior_v16),
            (_policy_v17, _behavior_v17),
            (_policy_v18, _behavior_v18),
            (_policy_v19, _behavior_v19),
            (_policy_v20, _behavior_v20),
            (_policy_v21, _behavior_v21),
            (_policy_v22, _behavior_v22),
            (_policy_v23, _behavior_v23),
            (_policy_v24, _behavior_v24),
            (_policy_v25, _behavior_v25),
        ):
            source_heads = set(policy.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD) - {
                policy.ALEMBIC_HEAD
            }
            if heads[0] in source_heads:
                return policy, behavior
    return _policy_v25, _behavior_v25


def validate_platform_migration_source_state(connection: Connection) -> None:
    """Validate a migration source using its exact frozen target policy."""

    try:
        _validated_migration_source_policy(connection)
    except _behavior_v6.PlatformDatabaseAttestationError:
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
    except _behavior_v6.PlatformDatabaseAttestationError:
        raise
    except _behavior_v1.PlatformDatabaseAttestationError as exc:
        raise PlatformDatabaseAttestationError(str(exc)) from None
