"""Add download audit records and report permissions.

Revision ID: 0006_download_reports
Revises: 0005_task_idempotency
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_download_reports"
down_revision: str | None = "0005_task_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REPORT_PERMISSIONS = {
    "reports.read": "查看公司任务、消费和下载报表",
    "reports.export": "导出公司任务和消费报表",
}


def upgrade() -> None:
    op.create_table(
        "download_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_seconds", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_seconds > 0", name="ck_download_expiry_positive"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_download_records_company_id", "download_records", ["company_id"]
    )
    op.create_index(
        "ix_download_records_requested_by_user_id",
        "download_records",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_download_records_request_id", "download_records", ["request_id"]
    )
    op.create_index(
        "ix_download_company_created",
        "download_records",
        ["company_id", "created_at"],
    )
    op.create_index(
        "ix_download_task_created",
        "download_records",
        ["task_id", "created_at"],
    )

    connection = op.get_bind()
    for permission_code, description in REPORT_PERMISSIONS.items():
        exists = connection.scalar(
            sa.text("SELECT COUNT(*) FROM permissions WHERE code = :code"),
            {"code": permission_code},
        )
        if not exists:
            connection.execute(
                sa.text(
                    "INSERT INTO permissions (code, description) "
                    "VALUES (:code, :description)"
                ),
                {"code": permission_code, "description": description},
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
                {"role_id": role_id, "permission_code": permission_code},
            )
            if not assigned:
                connection.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission_code) "
                        "VALUES (:role_id, :permission_code)"
                    ),
                    {"role_id": role_id, "permission_code": permission_code},
                )


def downgrade() -> None:
    connection = op.get_bind()
    for permission_code in REPORT_PERMISSIONS:
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE permission_code = :permission_code"
            ),
            {"permission_code": permission_code},
        )
        connection.execute(
            sa.text("DELETE FROM permissions WHERE code = :code"),
            {"code": permission_code},
        )

    op.drop_index("ix_download_task_created", table_name="download_records")
    op.drop_index("ix_download_company_created", table_name="download_records")
    op.drop_index(
        "ix_download_records_request_id", table_name="download_records"
    )
    op.drop_index(
        "ix_download_records_requested_by_user_id",
        table_name="download_records",
    )
    op.drop_index(
        "ix_download_records_company_id", table_name="download_records"
    )
    op.drop_table("download_records")
