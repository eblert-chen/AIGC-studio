"""Represent user and system audit actors and isolate catalog synchronization.

Revision ID: 0045_system_audit_actor
Revises: 0044_account_product_partition
"""

from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v9 as policy_v9
from platform_api import database_privileges_v10 as policy_v10
from platform_api.database_privileges_behavior_v9 import (
    collect_platform_database_evidence as collect_v9_database_evidence,
    validate_platform_database_acl_evidence as validate_v9_database_acl_evidence,
)
from platform_api.database_privileges_behavior_v10 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v10,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0045_system_audit_actor"
down_revision: str | None = "0044_account_product_partition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_PROCESS = "relay-catalog-sync"


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def _apply_relay_catalog_sync_acl() -> None:
    connection = op.get_bind()
    database_name = connection.scalar(sa.text("SELECT current_database()"))
    if not isinstance(database_name, str) or not database_name:
        raise RuntimeError("Platform database identity is unavailable")
    quoted_database = connection.dialect.identifier_preparer.quote(database_name)
    database_role = policy_v10.DATABASE_ROLE_BY_PROCESS[_PROCESS]
    quoted_role = _quote_identifier(database_role)

    _execute(
        f"REVOKE ALL PRIVILEGES ON DATABASE {quoted_database} FROM {quoted_role}"
    )
    _execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {quoted_role}")
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
    for table_name, privileges in sorted(
        policy_v10.PRIVILEGES_BY_PROCESS[_PROCESS].items()
    ):
        privilege_list = ", ".join(sorted(privileges))
        _execute(
            f"GRANT {privilege_list} ON TABLE public."
            f"{_quote_identifier(table_name)} TO {quoted_role}"
        )
    _execute(
        "GRANT SELECT ON TABLE public.alembic_version "
        f"TO {quoted_role}"
    )


def _revoke_relay_catalog_sync_acl() -> None:
    connection = op.get_bind()
    database_name = connection.scalar(sa.text("SELECT current_database()"))
    if not isinstance(database_name, str) or not database_name:
        raise RuntimeError("Platform database identity is unavailable")
    quoted_database = connection.dialect.identifier_preparer.quote(database_name)
    quoted_role = _quote_identifier(
        policy_v10.DATABASE_ROLE_BY_PROCESS[_PROCESS]
    )
    _execute(
        f"REVOKE ALL PRIVILEGES ON DATABASE {quoted_database} FROM {quoted_role}"
    )
    _execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {quoted_role}")
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


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v10()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v10)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v10,
        )

    with op.batch_alter_table("audit_logs") as batch:
        batch.add_column(
            sa.Column(
                "actor_kind",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'user'"),
            )
        )
        batch.add_column(
            sa.Column("actor_key", sa.String(length=120), nullable=True)
        )
        batch.alter_column(
            "actor_user_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )

    # Every historical row was created through an authenticated user path.
    # Keep that evidence explicit rather than inferring it at read time.
    op.execute(
        sa.text(
            "UPDATE audit_logs SET actor_kind='user', actor_key=NULL"
        )
    )
    with op.batch_alter_table("audit_logs") as batch:
        batch.create_check_constraint(
            "ck_audit_log_actor_identity",
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL "
            "AND actor_key IS NULL) OR "
            "(actor_kind = 'system' AND actor_user_id IS NULL "
            "AND actor_key IS NOT NULL)",
        )
        batch.create_index(
            "ix_audit_system_actor_created",
            ["actor_key", "created_at"],
            unique=False,
        )

    if protected_postgres:
        _apply_relay_catalog_sync_acl()
        evidence = collect_platform_database_evidence(
            connection,
            policy=policy_v10,
        )
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v10,
        )


def downgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v10()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v10)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v10,
        )

    system_rows = int(
        connection.scalar(
            sa.text("SELECT count(*) FROM audit_logs WHERE actor_kind='system'")
        )
        or 0
    )
    if system_rows:
        raise RuntimeError(
            "0045 downgrade would erase system audit identity evidence"
        )

    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_index("ix_audit_system_actor_created")
        batch.drop_constraint("ck_audit_log_actor_identity", type_="check")
        batch.alter_column(
            "actor_user_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch.drop_column("actor_key")
        batch.drop_column("actor_kind")

    if protected_postgres:
        _revoke_relay_catalog_sync_acl()
        # A cluster administrator owns role lifecycle.  The extra login role
        # is deliberately not dropped by Alembic; an older protected runtime
        # remains fail-closed until its principal inventory is reconciled.
        evidence = collect_v9_database_evidence(connection, policy=policy_v9)
        validate_v9_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v9,
        )
