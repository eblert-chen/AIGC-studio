"""separate Relay capability candidates from approved Platform ceilings

Revision ID: 0041_model_capability_releases
Revises: 0040_showcase_management
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from platform_api import database_privileges_v6 as policy_v6
from platform_api.database_privileges_behavior_v6 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v6,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0041_model_capability_releases"
down_revision: str | None = "0040_showcase_management"
branch_labels: str | None = None
depends_on: str | None = None


_PERSONAL_BATCH_JOURNAL = "personal_model_grant_batch_journals"


def _create_personal_batch_journal() -> None:
    op.create_table(
        _PERSONAL_BATCH_JOURNAL,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("request_sha256", sa.String(length=71), nullable=False),
        sa.Column("expected_snapshot", sa.String(length=71), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'succeeded')",
            name="ck_personal_model_grant_batch_state",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 71 "
            "AND substr(request_sha256, 1, 7) = 'sha256:'",
            name="ck_personal_model_grant_batch_request_sha256",
        ),
        sa.CheckConstraint(
            "length(expected_snapshot) = 71 "
            "AND substr(expected_snapshot, 1, 7) = 'sha256:'",
            name="ck_personal_model_grant_batch_snapshot_sha256",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND result_payload IS NULL) OR "
            "(state = 'succeeded' AND result_payload IS NOT NULL)",
            name="ck_personal_model_grant_batch_result_shape",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_personal_model_grant_batch_idempotency",
        ),
    )
    op.create_index(
        "ix_personal_model_grant_batch_actor_created",
        _PERSONAL_BATCH_JOURNAL,
        ["actor_user_id", "created_at"],
    )


def _apply_personal_batch_journal_acl() -> None:
    api_role = policy_v6.DATABASE_ROLE_BY_PROCESS["platform-api"]
    runtime_roles = {
        policy_v6.DATABASE_ROLE_BY_PROCESS[process]
        for process in policy_v6.PRIVILEGES_BY_PROCESS
    }
    for role in runtime_roles:
        op.execute(
            text(
                "REVOKE ALL PRIVILEGES ON TABLE "
                f"public.{_PERSONAL_BATCH_JOURNAL} FROM {role}"
            )
        )
    privileges = policy_v6.PRIVILEGES_BY_PROCESS["platform-api"][
        _PERSONAL_BATCH_JOURNAL
    ]
    op.execute(
        text(
            f"GRANT {', '.join(sorted(privileges))} ON TABLE "
            f"public.{_PERSONAL_BATCH_JOURNAL} TO {api_role}"
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v6()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v6)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v6,
        )

    _create_personal_batch_journal()

    op.add_column(
        "model_definitions",
        sa.Column(
            "relay_capability_candidate_revision",
            sa.String(length=71),
            nullable=True,
        ),
    )

    op.add_column(
        "model_definitions",
        sa.Column(
            "relay_capability_candidate_catalog_revision",
            sa.String(length=71),
            nullable=True,
        ),
    )

    op.add_column(
        "model_definitions",
        sa.Column("relay_capability_candidate", sa.JSON(), nullable=True),
    )
    op.add_column(
        "model_definitions",
        sa.Column(
            "relay_capability_candidate_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "model_definitions",
        sa.Column("relay_capability_approved_ceiling", sa.JSON(), nullable=True),
    )
    op.add_column(
        "model_definitions",
        sa.Column(
            "relay_capability_approved_catalog_revision",
            sa.String(length=71),
            nullable=True,
        ),
    )

    # Deliberately do not backfill a candidate or approved ceiling from the
    # legacy relay_capability_revision.  That revision is historical evidence,
    # not proof of the live capability document.  Existing models therefore
    # enter an unavailable/legacy-pending state and must pass a fresh Relay
    # catalog sync plus explicit owner approval before new distribution or
    # admission is reopened.

    # This revision also adds the unique personal-distribution batch journal.
    # Its least-privilege ACL was applied above; validate the complete v6
    # schema and ACL surface before Alembic commits the new head.
    if protected_postgres:
        _apply_personal_batch_journal_acl()
        evidence = collect_platform_database_evidence(connection, policy=policy_v6)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v6,
        )


def downgrade() -> None:
    for column_name in (
        "relay_capability_approved_catalog_revision",
        "relay_capability_approved_ceiling",
        "relay_capability_candidate_synced_at",
        "relay_capability_candidate",
        "relay_capability_candidate_catalog_revision",
        "relay_capability_candidate_revision",
    ):
        op.drop_column("model_definitions", column_name)
    op.drop_table(_PERSONAL_BATCH_JOURNAL)
