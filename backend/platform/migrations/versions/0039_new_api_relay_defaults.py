"""pin native new-api Relay defaults without rewriting historical affinity

Revision ID: 0039_new_api_relay_defaults
Revises: 0038_download_evidence_checks
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v4 as policy_v4
from platform_api.database_privileges_behavior_v4 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v4,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0039_new_api_relay_defaults"
down_revision: str | None = "0038_download_evidence_checks"
branch_labels: str | None = None
depends_on: str | None = None

NEW_API_BACKEND_ID = "new-api-v1"
LEGACY_BACKEND_ID = "legacy-default-v1"


def _set_relay_backend_defaults(backend_id: str) -> None:
    for table_name in ("generation_tasks", "relay_submission_outbox"):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "relay_backend_id",
                existing_type=sa.String(length=64),
                existing_nullable=False,
                server_default=backend_id,
            )


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v4()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v4)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v4,
        )

    # Existing rows retain their immutable backend affinity.  Only future
    # inserts that omit the column receive the completed-cutover identity.
    _set_relay_backend_defaults(NEW_API_BACKEND_ID)

    if protected_postgres:
        evidence = collect_platform_database_evidence(connection, policy=policy_v4)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v4,
        )


def downgrade() -> None:
    # Downgrade restores only the historical schema default; it never rewrites
    # rows created while this revision was active.
    _set_relay_backend_defaults(LEGACY_BACKEND_ID)
