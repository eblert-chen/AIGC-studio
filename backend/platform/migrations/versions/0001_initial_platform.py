"""Initial customer platform schema.

Revision ID: 0001_initial_platform
Revises:
Create Date: 2026-07-29
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_platform"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


company_status = sa.Enum(
    "ACTIVE", "SUSPENDED", name="companystatus", native_enum=False
)
membership_status = sa.Enum(
    "ACTIVE", "DISABLED", name="membershipstatus", native_enum=False
)
permission_effect = sa.Enum(
    "ALLOW", "DENY", name="permissioneffect", native_enum=False
)
task_status = sa.Enum(
    "DRAFT",
    "QUEUED",
    "PROCESSING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="taskstatus",
    native_enum=False,
)
ledger_kind = sa.Enum(
    "RECHARGE",
    "RESERVE",
    "SETTLE",
    "RELEASE",
    name="ledgerkind",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", company_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "model_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("provider_key", sa.String(length=80), nullable=False),
        sa.Column("capability_version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "company_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", membership_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "user_id", name="uq_membership_company_user"
        ),
    )
    op.create_index(
        "ix_company_memberships_company_id",
        "company_memberships",
        ["company_id"],
    )
    op.create_index(
        "ix_company_memberships_user_id", "company_memberships", ["user_id"]
    )
    op.create_index(
        "ix_membership_company_status",
        "company_memberships",
        ["company_id", "status"],
    )
    op.create_table(
        "model_capabilities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=False),
        sa.Column("capability_key", sa.String(length=80), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id", "capability_key", name="uq_model_capability_key"
        ),
    )
    op.create_index(
        "ix_model_capabilities_model_id", "model_capabilities", ["model_id"]
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_role_company_name"),
    )
    op.create_index("ix_roles_company_id", "roles", ["company_id"])
    op.create_table(
        "wallet_accounts",
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("available_cents", sa.Integer(), nullable=False),
        sa.Column("reserved_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "available_cents >= 0", name="ck_wallet_available_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_cents >= 0", name="ck_wallet_reserved_nonnegative"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id"),
    )
    op.create_table(
        "company_model_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("price_per_second_cents", sa.Integer(), nullable=True),
        sa.Column("price_per_item_cents", sa.Integer(), nullable=True),
        sa.Column("config_override", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "price_per_item_cents IS NULL OR price_per_item_cents > 0",
            name="ck_grant_item_price_positive",
        ),
        sa.CheckConstraint(
            "price_per_second_cents IS NULL OR price_per_second_cents > 0",
            name="ck_grant_second_price_positive",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "model_id", name="uq_company_model_grant"
        ),
    )
    op.create_index(
        "ix_company_model_grants_company_id",
        "company_model_grants",
        ["company_id"],
    )
    op.create_index(
        "ix_company_model_grants_model_id", "company_model_grants", ["model_id"]
    )
    op.create_table(
        "membership_roles",
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["company_memberships.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("membership_id", "role_id"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("permission_code", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_code"], ["permissions.code"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_code"),
    )
    op.create_table(
        "member_permission_overrides",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column("permission_code", sa.String(length=80), nullable=False),
        sa.Column("effect", permission_effect, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["company_memberships.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["permission_code"], ["permissions.code"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id",
            "permission_code",
            name="uq_member_permission_override",
        ),
    )
    op.create_index(
        "ix_member_permission_overrides_membership_id",
        "member_permission_overrides",
        ["membership_id"],
    )
    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("quote_cents", sa.Integer(), nullable=False),
        sa.Column("pricing_snapshot", sa.JSON(), nullable=False),
        sa.Column("capability_snapshot", sa.JSON(), nullable=False),
        sa.Column("reserved_cents", sa.Integer(), nullable=False),
        sa.Column("actual_cost_cents", sa.Integer(), nullable=True),
        sa.Column("provider_task_id", sa.String(length=160), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actual_cost_cents IS NULL OR actual_cost_cents >= 0",
            name="ck_task_actual_cost_nonnegative",
        ),
        sa.CheckConstraint("quote_cents > 0", name="ck_task_quote_positive"),
        sa.CheckConstraint(
            "reserved_cents >= 0", name="ck_task_reserved_nonnegative"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model_definitions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_task_company_created",
        "generation_tasks",
        ["company_id", "created_at"],
    )
    op.create_index(
        "ix_generation_tasks_company_id", "generation_tasks", ["company_id"]
    )
    op.create_index(
        "ix_generation_tasks_model_id", "generation_tasks", ["model_id"]
    )
    op.create_index(
        "ix_generation_tasks_user_id", "generation_tasks", ["user_id"]
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", ledger_kind, nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("available_delta_cents", sa.Integer(), nullable=False),
        sa.Column("reserved_delta_cents", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_cents >= 0", name="ck_ledger_amount_nonnegative"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "idempotency_key", name="uq_ledger_idempotency"
        ),
    )
    op.create_index(
        "ix_ledger_company_created",
        "ledger_entries",
        ["company_id", "created_at"],
    )
    op.create_index(
        "ix_ledger_entries_company_id", "ledger_entries", ["company_id"]
    )
    op.create_index("ix_ledger_entries_task_id", "ledger_entries", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_task_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_company_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_company_created", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_generation_tasks_user_id", table_name="generation_tasks")
    op.drop_index("ix_generation_tasks_model_id", table_name="generation_tasks")
    op.drop_index("ix_generation_tasks_company_id", table_name="generation_tasks")
    op.drop_index(
        "ix_generation_task_company_created", table_name="generation_tasks"
    )
    op.drop_table("generation_tasks")
    op.drop_index(
        "ix_member_permission_overrides_membership_id",
        table_name="member_permission_overrides",
    )
    op.drop_table("member_permission_overrides")
    op.drop_table("role_permissions")
    op.drop_table("membership_roles")
    op.drop_index(
        "ix_company_model_grants_model_id", table_name="company_model_grants"
    )
    op.drop_index(
        "ix_company_model_grants_company_id", table_name="company_model_grants"
    )
    op.drop_table("company_model_grants")
    op.drop_table("wallet_accounts")
    op.drop_index("ix_roles_company_id", table_name="roles")
    op.drop_table("roles")
    op.drop_index(
        "ix_model_capabilities_model_id", table_name="model_capabilities"
    )
    op.drop_table("model_capabilities")
    op.drop_index(
        "ix_membership_company_status", table_name="company_memberships"
    )
    op.drop_index(
        "ix_company_memberships_user_id", table_name="company_memberships"
    )
    op.drop_index(
        "ix_company_memberships_company_id", table_name="company_memberships"
    )
    op.drop_table("company_memberships")
    op.drop_table("model_definitions")
    op.drop_table("users")
    op.drop_table("permissions")
    op.drop_table("companies")
