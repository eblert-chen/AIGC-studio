"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0041.

Revision 0041 adds capability-release columns to ``model_definitions`` and the
unique ``personal_model_grant_batch_journals`` concurrency journal.  The
Platform API receives only ``SELECT``, ``INSERT`` and ``UPDATE`` on that table;
every other process remains denied.  The complete schema head is pinned to an
independently qualified PostgreSQL 16 catalog fingerprint.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v5 as policy_v5


ALEMBIC_HEAD = "0041_model_capability_releases"
MIGRATION_DATABASE_ROLE = policy_v5.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v5.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v5.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v5.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

_PERSONAL_BATCH_JOURNAL = "personal_model_grant_batch_journals"
TABLES = policy_v5.TABLES | {_PERSONAL_BATCH_JOURNAL}
_privileges: dict[str, Mapping[str, frozenset[str]]] = {}
for _process, _tables in policy_v5.PRIVILEGES_BY_PROCESS.items():
    _copy = dict(_tables)
    if _process == "platform-api":
        _copy[_PERSONAL_BATCH_JOURNAL] = frozenset(
            {"SELECT", "INSERT", "UPDATE"}
        )
    _privileges[_process] = MappingProxyType(_copy)
PRIVILEGES_BY_PROCESS: Mapping[
    str, Mapping[str, frozenset[str]]
] = MappingProxyType(_privileges)


def _expected_table_acl() -> frozenset[tuple[str, str, str]]:
    expected: set[tuple[str, str, str]] = set()
    for process_role, privileges_by_table in PRIVILEGES_BY_PROCESS.items():
        database_role = DATABASE_ROLE_BY_PROCESS[process_role]
        for table_name, privileges in privileges_by_table.items():
            expected.update(
                (table_name, database_role, privilege)
                for privilege in privileges
            )
        expected.add(("alembic_version", database_role, "SELECT"))
    return frozenset(expected)


EXPECTED_TABLE_ACL = _expected_table_acl()
EXPECTED_DATABASE_ACL = policy_v5.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v5.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v5.EXPECTED_DEFAULT_ACL

# Qualified on three independent PostgreSQL 16.14 databases upgraded through
# the complete 0041 chain.  A 0041 -> 0040 -> 0041 round trip reproduced both
# normalized v6 source and current fingerprints.  The v6 projection preserves
# explicit storage and column semantics while normalizing dropped attnum slots
# and default internal TOAST allocation history.
CATALOG_SHA256 = "397e6923d44a7c68ab462d50895b95715f0edf7ffb7fd965227ce8ef02075ae9"
EMPTY_CATALOG_SHA256 = policy_v5.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v5.ALEMBIC_HEAD: (
            "15f8e992c8e6a88d587fe7383fa1b390da3696834c11acf255856520a6265cfa"
        ),
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)

POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v5.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v5.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v5.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
