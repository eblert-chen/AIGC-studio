"""Add company-scoped publishing connections and durable publication jobs.

Revision ID: 0020_publishing
Revises: 0019_channel_cost_evidence
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0020_publishing"
down_revision: str | None = "0019_channel_cost_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


publisher_connection_status = sa.Enum(
    "ACTIVE",
    "DISABLED",
    "REQUIRES_REAUTH",
    name="publisherconnectionstatus",
    native_enum=False,
)
publication_job_status = sa.Enum(
    "PENDING_APPROVAL",
    "SCHEDULED",
    "QUEUED",
    "SUBMITTING",
    "PUBLISHED",
    "FAILED",
    "SUBMISSION_UNKNOWN",
    "REQUIRES_REAUTH",
    "CANCELLED",
    name="publicationjobstatus",
    native_enum=False,
)
publication_attempt_status = sa.Enum(
    "SUBMITTING",
    "PUBLISHED",
    "FAILED",
    "SUBMISSION_UNKNOWN",
    "REQUIRES_REAUTH",
    name="publicationattemptstatus",
    native_enum=False,
)


PUBLISHING_PERMISSIONS = {
    "publish.accounts.read": "View company publishing accounts",
    "publish.accounts.manage": "Connect and manage company publishing accounts",
    "publish.jobs.read": "View company publication jobs",
    "publish.jobs.manage": "Create, approve, cancel, and retry publication jobs",
}
TEAM_LEAD_PUBLISHING_PERMISSIONS = frozenset(PUBLISHING_PERMISSIONS)
OPERATOR_PUBLISHING_PERMISSIONS = frozenset(
    {
        "publish.accounts.read",
        "publish.jobs.read",
        "publish.jobs.manage",
    }
)
AUTO_PUBLISH_RESOURCE_ID = "00000000-0000-4000-8000-000000000020"


def _seed_permissions_and_feature() -> None:
    connection = op.get_bind()
    for code, description in PUBLISHING_PERMISSIONS.items():
        exists = connection.execute(
            sa.text("SELECT code FROM permissions WHERE code = :code"),
            {"code": code},
        ).scalar()
        if not exists:
            connection.execute(
                sa.text(
                    "INSERT INTO permissions (code, description) "
                    "VALUES (:code, :description)"
                ),
                {"code": code, "description": description},
            )

    role_rows = connection.execute(
        sa.text(
            "SELECT id, system_key FROM roles "
            "WHERE system_key IN ('owner', 'team_lead', 'operator')"
        )
    ).all()
    for role_id, system_key in role_rows:
        if system_key == "operator":
            permission_codes = OPERATOR_PUBLISHING_PERMISSIONS
        elif system_key in {"owner", "team_lead"}:
            permission_codes = TEAM_LEAD_PUBLISHING_PERMISSIONS
        else:
            permission_codes = frozenset()
        for permission_code in permission_codes:
            assigned = connection.execute(
                sa.text(
                    "SELECT permission_code FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_code = :code"
                ),
                {"role_id": role_id, "code": permission_code},
            ).scalar()
            if not assigned:
                connection.execute(
                    sa.text(
                        "INSERT INTO role_permissions "
                        "(role_id, permission_code) VALUES (:role_id, :code)"
                    ),
                    {"role_id": role_id, "code": permission_code},
                )

    existing_feature = connection.execute(
        sa.text(
            "SELECT id FROM resource_definitions "
            "WHERE key = 'feature.auto_publish'"
        )
    ).scalar()
    if existing_feature is None:
        now = datetime.now(timezone.utc)
        connection.execute(
            sa.text(
                "INSERT INTO resource_definitions "
                "(id, key, kind, display_name, description, active, "
                "created_at, updated_at) VALUES "
                "(:id, :key, :kind, :display_name, :description, :active, "
                ":created_at, :updated_at)"
            ),
            {
                "id": AUTO_PUBLISH_RESOURCE_ID,
                "key": "feature.auto_publish",
                "kind": "FEATURE",
                "display_name": "Automatic publishing",
                "description": "Approve and publish stored artifacts to connected accounts",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )


def upgrade() -> None:
    op.create_table(
        "publisher_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("external_account_id", sa.String(length=160), nullable=False),
        sa.Column("status", publisher_connection_status, nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(provider) > 0 AND provider = lower(provider)",
            name="ck_publisher_connection_provider_normalized",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "provider",
            "external_account_id",
            name="uq_publisher_connection_account",
        ),
    )
    op.create_index(
        "ix_publisher_connections_company_id",
        "publisher_connections",
        ["company_id"],
    )
    op.create_index(
        "ix_publisher_connections_created_by_user_id",
        "publisher_connections",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_publisher_connection_company_status_created",
        "publisher_connections",
        ["company_id", "status", "created_at"],
    )

    op.create_table(
        "publication_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("task_artifact_id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", publication_job_status, nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_post_id", sa.String(length=200), nullable=True),
        sa.Column("external_post_url", sa.String(length=2048), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_publication_job_attempt_count"
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_publication_job_lease_complete",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_artifact_id"], ["task_artifacts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["publisher_connections.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_publication_job_company_idempotency",
        ),
        sa.UniqueConstraint(
            "connection_id",
            "external_post_id",
            name="uq_publication_job_connection_external_post",
        ),
    )
    for index_name, columns in (
        ("ix_publication_jobs_company_id", ["company_id"]),
        ("ix_publication_jobs_created_by_user_id", ["created_by_user_id"]),
        ("ix_publication_jobs_task_artifact_id", ["task_artifact_id"]),
        ("ix_publication_jobs_connection_id", ["connection_id"]),
        (
            "ix_publication_job_company_status_created",
            ["company_id", "status", "created_at"],
        ),
        (
            "ix_publication_job_dispatch",
            ["status", "next_attempt_at", "scheduled_at"],
        ),
    ):
        op.create_index(index_name, "publication_jobs", columns)

    op.create_table(
        "publication_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", publication_attempt_status, nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=False),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("external_post_id", sa.String(length=200), nullable=True),
        sa.Column("external_post_url", sa.String(length=2048), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_publication_attempt_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["publication_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "attempt_number", name="uq_publication_attempt_number"
        ),
    )
    op.create_index(
        "ix_publication_attempts_company_id",
        "publication_attempts",
        ["company_id"],
    )
    op.create_index(
        "ix_publication_attempts_job_id",
        "publication_attempts",
        ["job_id"],
    )
    op.create_index(
        "ix_publication_attempt_company_created",
        "publication_attempts",
        ["company_id", "created_at"],
    )
    _seed_permissions_and_feature()


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_code IN "
            "('publish.accounts.read', 'publish.accounts.manage', "
            "'publish.jobs.read', 'publish.jobs.manage')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM member_permission_overrides WHERE permission_code IN "
            "('publish.accounts.read', 'publish.accounts.manage', "
            "'publish.jobs.read', 'publish.jobs.manage')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN "
            "('publish.accounts.read', 'publish.accounts.manage', "
            "'publish.jobs.read', 'publish.jobs.manage')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM company_resource_grants WHERE resource_id = :resource_id"
        ),
        {"resource_id": AUTO_PUBLISH_RESOURCE_ID},
    )
    connection.execute(
        sa.text(
            "DELETE FROM resource_definitions "
            "WHERE id = :resource_id AND key = 'feature.auto_publish'"
        ),
        {"resource_id": AUTO_PUBLISH_RESOURCE_ID},
    )
    op.drop_index(
        "ix_publication_attempt_company_created",
        table_name="publication_attempts",
    )
    op.drop_index(
        "ix_publication_attempts_job_id", table_name="publication_attempts"
    )
    op.drop_index(
        "ix_publication_attempts_company_id", table_name="publication_attempts"
    )
    op.drop_table("publication_attempts")
    for index_name in (
        "ix_publication_job_dispatch",
        "ix_publication_job_company_status_created",
        "ix_publication_jobs_connection_id",
        "ix_publication_jobs_task_artifact_id",
        "ix_publication_jobs_created_by_user_id",
        "ix_publication_jobs_company_id",
    ):
        op.drop_index(index_name, table_name="publication_jobs")
    op.drop_table("publication_jobs")
    op.drop_index(
        "ix_publisher_connection_company_status_created",
        table_name="publisher_connections",
    )
    op.drop_index(
        "ix_publisher_connections_created_by_user_id",
        table_name="publisher_connections",
    )
    op.drop_index(
        "ix_publisher_connections_company_id",
        table_name="publisher_connections",
    )
    op.drop_table("publisher_connections")
