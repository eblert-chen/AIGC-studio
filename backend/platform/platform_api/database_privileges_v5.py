"""Immutable Platform PostgreSQL principal/ACL policy for Alembic 0040."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from . import database_privileges_v4 as policy_v4


ALEMBIC_HEAD = "0040_showcase_management"
MIGRATION_DATABASE_ROLE = policy_v4.MIGRATION_DATABASE_ROLE
DATABASE_ROLE_BY_PROCESS = policy_v4.DATABASE_ROLE_BY_PROCESS
DATABASE_ROLE_COMMENT_BY_PROCESS = policy_v4.DATABASE_ROLE_COMMENT_BY_PROCESS
DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS = (
    policy_v4.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS
)

_SHOWCASE_TABLES = frozenset(
    {
        "showcase_channels",
        "showcase_draft_items",
        "showcase_media",
        "showcase_publication_events",
        "showcase_release_items",
        "showcase_releases",
    }
)
TABLES = policy_v4.TABLES | _SHOWCASE_TABLES

_APPEND = frozenset({"SELECT", "INSERT"})
_MUTABLE_NO_DELETE = frozenset({"SELECT", "INSERT", "UPDATE"})
_SINGLETON_MUTABLE = frozenset({"SELECT", "UPDATE"})
_privileges: dict[str, Mapping[str, frozenset[str]]] = {}
for _process, _tables in policy_v4.PRIVILEGES_BY_PROCESS.items():
    _copy = dict(_tables)
    if _process == "platform-api":
        _copy.update(
            {
                "showcase_channels": _SINGLETON_MUTABLE,
                "showcase_draft_items": _MUTABLE_NO_DELETE,
                "showcase_media": _APPEND,
                "showcase_publication_events": _APPEND,
                "showcase_release_items": _APPEND,
                "showcase_releases": _APPEND,
            }
        )
    _privileges[_process] = MappingProxyType(_copy)
PRIVILEGES_BY_PROCESS: Mapping[str, Mapping[str, frozenset[str]]] = MappingProxyType(
    _privileges
)


def _expected_table_acl() -> frozenset[tuple[str, str, str]]:
    expected: set[tuple[str, str, str]] = set()
    for process_role, privileges_by_table in PRIVILEGES_BY_PROCESS.items():
        database_role = DATABASE_ROLE_BY_PROCESS[process_role]
        for table_name, privileges in privileges_by_table.items():
            expected.update(
                (table_name, database_role, privilege) for privilege in privileges
            )
        expected.add(("alembic_version", database_role, "SELECT"))
    return frozenset(expected)


EXPECTED_TABLE_ACL = _expected_table_acl()
EXPECTED_DATABASE_ACL = policy_v4.EXPECTED_DATABASE_ACL
EXPECTED_SCHEMA_ACL = policy_v4.EXPECTED_SCHEMA_ACL
EXPECTED_DEFAULT_ACL = policy_v4.EXPECTED_DEFAULT_ACL

# Filled from repeatable PostgreSQL 16 qualification after applying 0040 to
# two independent, frozen 0039 source databases.
CATALOG_SHA256 = "ecd5b3faae20595e66396c59d37327d1e6e5b742c3d70697aaf6f109866591e6"
EMPTY_CATALOG_SHA256 = policy_v4.EMPTY_CATALOG_SHA256
MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD = MappingProxyType(
    {
        policy_v4.ALEMBIC_HEAD: policy_v4.CATALOG_SHA256,
        ALEMBIC_HEAD: CATALOG_SHA256,
    }
)

POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256 = (
    policy_v4.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256
)
SYSTEM_ACL_SHA256 = policy_v4.SYSTEM_ACL_SHA256
QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256 = (
    policy_v4.QUALIFIED_POSTGRES16_SYSTEM_ACL_SHA256
)
