"""Add company-scoped private input assets and task references.

Revision ID: 0009_input_assets
Revises: 0008_access_model_lifecycle
Create Date: 2026-08-03
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_input_assets"
down_revision: str | None = "0008_access_model_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


input_asset_status = sa.Enum(
    "ACTIVE",
    "DISABLED",
    name="inputassetstatus",
    native_enum=False,
)

ASSET_PERMISSIONS = {
    "assets.read": "View company input assets",
    "assets.manage": "Upload and disable company input assets",
}


def upgrade() -> None:
    op.create_table(
        "input_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("status", input_asset_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes > 0", name="ck_input_asset_size_positive"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_input_asset_object_key"),
        sa.UniqueConstraint(
            "company_id",
            "uploaded_by_user_id",
            "idempotency_key",
            name="uq_input_asset_uploader_idempotency",
        ),
    )
    op.create_index(
        "ix_input_assets_company_id", "input_assets", ["company_id"]
    )
    op.create_index(
        "ix_input_assets_uploaded_by_user_id",
        "input_assets",
        ["uploaded_by_user_id"],
    )
    op.create_index(
        "ix_input_asset_company_status_created",
        "input_assets",
        ["company_id", "status", "created_at"],
    )

    op.create_table(
        "task_input_assets",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name="ck_task_input_position_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["input_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("task_id", "asset_id"),
        sa.UniqueConstraint(
            "task_id", "position", name="uq_task_input_position"
        ),
    )
    op.create_index(
        "ix_task_input_asset_asset", "task_input_assets", ["asset_id"]
    )

    connection = op.get_bind()
    for code, description in ASSET_PERMISSIONS.items():
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
    for role_id, _system_key in role_rows:
        for permission_code in ASSET_PERMISSIONS:
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


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_code IN "
            "('assets.read', 'assets.manage')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM member_permission_overrides WHERE permission_code IN "
            "('assets.read', 'assets.manage')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN "
            "('assets.read', 'assets.manage')"
        )
    )
    op.drop_index("ix_task_input_asset_asset", table_name="task_input_assets")
    op.drop_table("task_input_assets")
    op.drop_index(
        "ix_input_asset_company_status_created", table_name="input_assets"
    )
    op.drop_index(
        "ix_input_assets_uploaded_by_user_id", table_name="input_assets"
    )
    op.drop_index("ix_input_assets_company_id", table_name="input_assets")
    op.drop_table("input_assets")
