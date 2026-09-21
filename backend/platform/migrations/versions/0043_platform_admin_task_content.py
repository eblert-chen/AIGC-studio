"""Add the task-prompt collection administrator permission domain.

Revision ID: 0043_admin_task_content
Revises: 0042_entitlement_batch_journal
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v8 as policy_v8
from platform_api.database_privileges_behavior_v8 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v8,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0043_admin_task_content"
down_revision: str | None = "0042_entitlement_batch_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROWS = (
    {
        "code": "platform.task_content.read",
        "domain": "task_content",
        "action": "read",
        "description": "查看用户生成提示词自动汇总库",
    },
)


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v8()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v8)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v8,
        )
    permissions = sa.table(
        "platform_admin_permissions",
        sa.column("code", sa.String()),
        sa.column("domain", sa.String()),
        sa.column("action", sa.String()),
        sa.column("description", sa.String()),
    )
    op.bulk_insert(permissions, list(_ROWS))
    if protected_postgres:
        evidence = collect_platform_database_evidence(connection, policy=policy_v8)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v8,
        )


def downgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v8()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v8)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v8,
        )
    read_code = _ROWS[0]["code"]
    connection.execute(
        sa.text(
            "DELETE FROM platform_admin_user_permission_overrides "
            "WHERE permission_code = :read_code"
        ),
        {"read_code": read_code},
    )
    connection.execute(
        sa.text(
            "DELETE FROM platform_admin_role_permissions "
            "WHERE permission_code = :read_code"
        ),
        {"read_code": read_code},
    )
    connection.execute(
        sa.text("DELETE FROM platform_admin_permissions WHERE code = :read_code"),
        {"read_code": read_code},
    )
    if protected_postgres:
        evidence = collect_platform_database_evidence(connection, policy=policy_v8)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v8,
        )
