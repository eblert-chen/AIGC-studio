"""Add granular roles and grants for platform administrators.

Revision ID: 0024_platform_admin_access
Revises: 0023_download_gateway_attempts
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0024_platform_admin_access"
down_revision: str | None = "0023_download_gateway_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DOMAIN_DESCRIPTIONS = {
    "analytics": "经营、任务、模型盈利和企业健康分析",
    "companies": "企业资料、状态和企业生命周期",
    "entitlements": "企业模型、功能、智能体、外部 API 和自动发布权益",
    "models": "模型目录、能力声明、定价模式和 Relay 映射审批",
    "resources": "功能、智能体和外部 API 资源目录",
    "finance": "充值、结算收入、企业余额和账务异常",
    "provider_costs": "渠道成本、成本缺失和毛利对账",
    "publishing_exceptions": "发布失败、未知提交和 OAuth 异常",
    "asset_exceptions": "产物转存和下载登记异常",
    "audit": "平台操作审计、筛选和导出线索",
    "relay_health": "Relay 渠道、账号池、限流、切换和告警摘要",
    "admin_access": "平台管理员角色和权限分配",
}


def _permission_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for domain, subject in _DOMAIN_DESCRIPTIONS.items():
        rows.extend(
            (
                {
                    "code": f"platform.{domain}.read",
                    "domain": domain,
                    "action": "read",
                    "description": f"查看{subject}",
                },
                {
                    "code": f"platform.{domain}.manage",
                    "domain": domain,
                    "action": "manage",
                    "description": f"管理{subject}",
                },
            )
        )
    return rows


def upgrade() -> None:
    op.create_table(
        "platform_admin_permissions",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("domain", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index(
        "ix_platform_admin_permissions_domain",
        "platform_admin_permissions",
        ["domain"],
        unique=False,
    )
    op.create_table(
        "platform_admin_roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_platform_admin_role_version"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(
        "ix_platform_admin_roles_created_by_user_id",
        "platform_admin_roles",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_admin_roles_updated_by_user_id",
        "platform_admin_roles",
        ["updated_by_user_id"],
        unique=False,
    )
    op.create_table(
        "platform_admin_role_permissions",
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("permission_code", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_code"],
            ["platform_admin_permissions.code"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["platform_admin_roles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_code"),
    )
    op.create_table(
        "platform_admin_access_profiles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lock_version >= 1",
            name="ck_platform_admin_access_profile_version",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_platform_admin_access_profiles_updated_by_user_id",
        "platform_admin_access_profiles",
        ["updated_by_user_id"],
        unique=False,
    )
    op.create_table(
        "platform_admin_role_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["platform_admin_roles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_admin_access_profiles.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "role_id", name="uq_platform_admin_role_assignment"
        ),
    )
    op.create_index(
        "ix_platform_admin_role_assignments_user_id",
        "platform_admin_role_assignments",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_admin_role_assignment_role",
        "platform_admin_role_assignments",
        ["role_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "platform_admin_user_permission_overrides",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("permission_code", sa.String(length=100), nullable=False),
        sa.Column(
            "effect",
            sa.Enum(
                "ALLOW",
                "DENY",
                name="platformadminpermissioneffect",
                native_enum=False,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("changed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["permission_code"],
            ["platform_admin_permissions.code"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_admin_access_profiles.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "permission_code"),
    )

    permissions = sa.table(
        "platform_admin_permissions",
        sa.column("code", sa.String()),
        sa.column("domain", sa.String()),
        sa.column("action", sa.String()),
        sa.column("description", sa.String()),
    )
    op.bulk_insert(permissions, _permission_rows())


def downgrade() -> None:
    op.drop_table("platform_admin_user_permission_overrides")
    op.drop_index(
        "ix_platform_admin_role_assignment_role",
        table_name="platform_admin_role_assignments",
    )
    op.drop_index(
        "ix_platform_admin_role_assignments_user_id",
        table_name="platform_admin_role_assignments",
    )
    op.drop_table("platform_admin_role_assignments")
    op.drop_index(
        "ix_platform_admin_access_profiles_updated_by_user_id",
        table_name="platform_admin_access_profiles",
    )
    op.drop_table("platform_admin_access_profiles")
    op.drop_table("platform_admin_role_permissions")
    op.drop_index(
        "ix_platform_admin_roles_updated_by_user_id",
        table_name="platform_admin_roles",
    )
    op.drop_index(
        "ix_platform_admin_roles_created_by_user_id",
        table_name="platform_admin_roles",
    )
    op.drop_table("platform_admin_roles")
    op.drop_index(
        "ix_platform_admin_permissions_domain",
        table_name="platform_admin_permissions",
    )
    op.drop_table("platform_admin_permissions")

