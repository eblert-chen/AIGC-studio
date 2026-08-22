"""Bind each protected Platform process to one pre-provisioned DB principal.

Revision ID: 0036_platform_db_roles
Revises: 0035_operations_evidence

The migration deliberately never creates, alters, or passwords a PostgreSQL
role.  A cluster administrator must pre-provision the seven login roles with
the exact comments and flags checked by the shared attestation code, and must
create the dedicated database with ``platform_migration`` as owner.  This
preflight makes a same-named, pre-existing role fail closed instead of silently
receiving application data privileges.
"""

from __future__ import annotations

import re
from typing import Sequence

from alembic import op
from sqlalchemy import text

from platform_api import database_privileges_v1 as policy_v1
from platform_api.database_privileges_behavior_v1 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v1,
    validate_platform_migration_source_state,
    validate_platform_database_acl_evidence,
)

# A local historical alias keeps tests explicit while preventing the live
# process/bootstrap module from reinterpreting revision 0036.
protected_platform_runtime_requested = protected_platform_runtime_requested_v1


revision: str = "0036_platform_db_roles"
down_revision: str | None = "0035_operations_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _execute(statement: str) -> None:
    op.execute(text(statement))


def _apply_runtime_acl() -> None:
    connection = op.get_bind()
    database_name = connection.scalar(text("SELECT current_database()"))
    if not isinstance(database_name, str) or not database_name:
        raise RuntimeError("Platform database identity is unavailable")
    quoted_database = connection.dialect.identifier_preparer.quote(database_name)
    runtime_process_roles = tuple(policy_v1.PRIVILEGES_BY_PROCESS)
    runtime_database_roles = tuple(
        policy_v1.DATABASE_ROLE_BY_PROCESS[process_role]
        for process_role in runtime_process_roles
    )

    _execute(
        f"REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE {quoted_database} FROM PUBLIC"
    )
    _execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
    _execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    _execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    _execute("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")

    for database_role in runtime_database_roles:
        quoted_role = _quote_identifier(database_role)
        _execute(
            f"REVOKE ALL PRIVILEGES ON DATABASE {quoted_database} FROM {quoted_role}"
        )
        _execute(
            f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {quoted_role}"
        )
        _execute(
            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
            f"FROM {quoted_role}"
        )
        _execute(
            "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
            f"FROM {quoted_role}"
        )
        _execute(
            "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public "
            f"FROM {quoted_role}"
        )
        _execute(f"GRANT CONNECT ON DATABASE {quoted_database} TO {quoted_role}")
        _execute(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")

    quoted_migration_role = _quote_identifier(policy_v1.MIGRATION_DATABASE_ROLE)
    _execute(
        "ALTER DEFAULT PRIVILEGES "
        f"FOR ROLE {quoted_migration_role} IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
    )
    _execute(
        "ALTER DEFAULT PRIVILEGES "
        f"FOR ROLE {quoted_migration_role} IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC"
    )
    _execute(
        "ALTER DEFAULT PRIVILEGES "
        f"FOR ROLE {quoted_migration_role} "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )

    for process_role, privileges_by_table in (
        policy_v1.PRIVILEGES_BY_PROCESS.items()
    ):
        quoted_role = _quote_identifier(
            policy_v1.DATABASE_ROLE_BY_PROCESS[process_role]
        )
        for table_name, privileges in sorted(privileges_by_table.items()):
            privilege_list = ", ".join(sorted(privileges))
            _execute(
                f"GRANT {privilege_list} ON TABLE public."
                f"{_quote_identifier(table_name)} TO {quoted_role}"
            )
        _execute(
            "GRANT SELECT ON TABLE public.alembic_version "
            f"TO {quoted_role}"
        )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    if not protected_platform_runtime_requested():
        return

    # This is intentionally first and read-only.  It proves all principals are
    # explicitly pre-provisioned, unprivileged cluster logins, the migration
    # session owns the dedicated database and existing objects, and no role
    # memberships, role GUCs, or column grants can bypass the table manifest.
    validate_platform_migration_source_state(connection, policy=policy_v1)
    attest_platform_database_connection(
        connection,
        "migration",
        require_runtime_acl=False,
        require_head=False,
        policy=policy_v1,
    )
    _apply_runtime_acl()
    evidence = collect_platform_database_evidence(connection, policy=policy_v1)
    validate_platform_database_acl_evidence(
        evidence,
        require_head=False,
        policy=policy_v1,
    )


def downgrade() -> None:
    # ACL narrowing is a durable security boundary, not schema data that may be
    # safely widened on rollback.  A protected runtime remains fail-closed on
    # the older Alembic head until this revision is re-applied.
    return
