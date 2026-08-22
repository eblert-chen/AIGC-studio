"""Add platform admin resources and immutable audit log.

Revision ID: 0003_platform_admin
Revises: 0002_relay_submission_outbox
Create Date: 2026-07-29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_platform_admin"
down_revision: str | None = "0002_relay_submission_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


resource_kind = sa.Enum(
    "FEATURE",
    "AGENT",
    "EXTERNAL_API",
    name="resourcekind",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "resource_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("kind", resource_kind, nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "company_resource_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_override", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "resource_id", name="uq_company_resource_grant"
        ),
    )
    op.create_index(
        "ix_company_resource_grants_company_id",
        "company_resource_grants",
        ["company_id"],
    )
    op.create_index(
        "ix_company_resource_grants_resource_id",
        "company_resource_grants",
        ["resource_id"],
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("before_summary", sa.JSON(), nullable=False),
        sa.Column("after_summary", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"]
    )
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_created", "audit_logs", ["created_at"])
    op.create_index(
        "ix_audit_actor_created",
        "audit_logs",
        ["actor_user_id", "created_at"],
    )

    connection = op.get_bind()
    exists = connection.scalar(
        sa.text("SELECT COUNT(*) FROM permissions WHERE code = :code"),
        {"code": "resources.read"},
    )
    if not exists:
        connection.execute(
            sa.text(
                "INSERT INTO permissions (code, description) "
                "VALUES (:code, :description)"
            ),
            {
                "code": "resources.read",
                "description": "查看公司可用功能与资源",
            },
        )
    owner_role_ids = connection.execute(
        sa.text(
            "SELECT id FROM roles WHERE name = :name AND is_system = :is_system"
        ),
        {"name": "老板", "is_system": True},
    ).scalars()
    for role_id in owner_role_ids:
        assigned = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM role_permissions "
                "WHERE role_id = :role_id AND permission_code = :permission_code"
            ),
            {"role_id": role_id, "permission_code": "resources.read"},
        )
        if not assigned:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code) "
                    "VALUES (:role_id, :permission_code)"
                ),
                {"role_id": role_id, "permission_code": "resources.read"},
            )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_code = :permission_code"
        ),
        {"permission_code": "resources.read"},
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE code = :code"),
        {"code": "resources.read"},
    )
    op.drop_index("ix_audit_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        "ix_company_resource_grants_resource_id",
        table_name="company_resource_grants",
    )
    op.drop_index(
        "ix_company_resource_grants_company_id",
        table_name="company_resource_grants",
    )
    op.drop_table("company_resource_grants")
    op.drop_table("resource_definitions")
