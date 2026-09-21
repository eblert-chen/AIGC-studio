"""Add evidence-bound model commercial release plans.

Revision ID: 0050_model_commercial_release
Revises: 0049_payment_finance_closure
"""
from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v15 as policy_v15
from platform_api.database_privileges_behavior_v15 import (
    protected_platform_runtime_requested_v15,
)


revision: str = "0050_model_commercial_release"
down_revision: str | None = "0049_payment_finance_closure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_TABLES = (
    "model_commercial_release_plans",
    "model_commercial_release_executions",
)


def _quote(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _sha256_constraints(column: str, name: str):
    sqlite = (
        f"length({column}) = 64 AND lower({column}) = {column} "
        f"AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    postgres = f"{column} ~ '^[0-9a-f]{{64}}$'"
    return (
        sa.CheckConstraint(sqlite, name=name).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(postgres, name=name).ddl_if(dialect="postgresql"),
    )


def _apply_acl() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    runtime_roles = tuple(
        role
        for process, role in policy_v15.DATABASE_ROLE_BY_PROCESS.items()
        if process != "migration"
    )
    for table_name in _TABLES:
        for role in runtime_roles:
            op.execute(
                sa.text(
                    f"REVOKE ALL PRIVILEGES ON TABLE public.{_quote(table_name)} "
                    f"FROM {_quote(role)}"
                )
            )
    for process, privileges_by_table in policy_v15.PRIVILEGES_BY_PROCESS.items():
        role = _quote(policy_v15.DATABASE_ROLE_BY_PROCESS[process])
        for table_name in _TABLES:
            privileges = privileges_by_table.get(table_name)
            if privileges:
                op.execute(
                    sa.text(
                        f"GRANT {', '.join(sorted(privileges))} ON TABLE public."
                        f"{_quote(table_name)} TO {role}"
                    )
                )
    # The worker's expansion is explicit and remains narrower than API access.
    worker_role = _quote(
        policy_v15.DATABASE_ROLE_BY_PROCESS["relay-catalog-sync"]
    )
    for table_name in (
        "companies",
        "company_model_grants",
        "company_point_price_versions",
        "personal_retail_model_grants",
    ):
        privileges = policy_v15.PRIVILEGES_BY_PROCESS[
            "relay-catalog-sync"
        ][table_name]
        op.execute(
            sa.text(
                f"GRANT {', '.join(sorted(privileges))} ON TABLE public."
                f"{_quote(table_name)} TO {worker_role}"
            )
        )


def _create_immutable_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    "CREATE TRIGGER trg_model_commercial_release_plan_no_"
                    f"{action.lower()} BEFORE {action} ON "
                    "model_commercial_release_plans BEGIN SELECT RAISE(ABORT, "
                    "'model commercial release plans are immutable'); END"
                )
            )
    elif dialect == "postgresql":
        op.execute(
            sa.text(
                "CREATE FUNCTION reject_model_commercial_release_plan_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'model commercial release plans are immutable'; END $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_model_commercial_release_plan_immutable "
                "BEFORE UPDATE OR DELETE ON model_commercial_release_plans "
                "FOR EACH ROW EXECUTE FUNCTION "
                "reject_model_commercial_release_plan_mutation()"
            )
        )


def _drop_immutable_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for action in ("update", "delete"):
            op.execute(
                sa.text(
                    "DROP TRIGGER IF EXISTS "
                    f"trg_model_commercial_release_plan_no_{action}"
                )
            )
    elif dialect == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS "
                "trg_model_commercial_release_plan_immutable ON "
                "model_commercial_release_plans"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS "
                "reject_model_commercial_release_plan_mutation"
            )
        )


def upgrade() -> None:
    op.create_table(
        "model_commercial_release_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(36), nullable=False),
        sa.Column("candidate_revision", sa.String(71), nullable=False),
        sa.Column("candidate_catalog_revision", sa.String(71), nullable=False),
        sa.Column("capability_version", sa.Integer(), nullable=False),
        sa.Column("billing_mode", sa.String(24), nullable=False),
        sa.Column("provider_cost_currency", sa.String(3), nullable=False),
        sa.Column("provider_cost_formula", sa.JSON(), nullable=False),
        sa.Column("provider_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("provider_cost_cny_micros", sa.BigInteger(), nullable=False),
        sa.Column("provider_cost_evidence_kind", sa.String(32), nullable=False),
        sa.Column("provider_cost_evidence_reference", sa.String(500), nullable=False),
        sa.Column("provider_cost_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("provider_cost_effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fx_cny_micros_per_currency_unit", sa.BigInteger(), nullable=False),
        sa.Column("fx_source", sa.String(120), nullable=False),
        sa.Column("fx_version", sa.String(160), nullable=False),
        sa.Column("fx_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("fx_effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("points_per_cny", sa.Integer(), nullable=False),
        sa.Column("target_margin_bps", sa.Integer(), nullable=False),
        sa.Column("minimum_price_points", sa.BigInteger(), nullable=False),
        sa.Column("personal_price_points", sa.BigInteger(), nullable=False),
        sa.Column("enterprise_price_points", sa.BigInteger(), nullable=False),
        sa.Column("enterprise_distribution_scope", sa.String(80), nullable=False),
        sa.Column("personal_config_override", sa.JSON(), nullable=False),
        sa.Column("enterprise_config_override", sa.JSON(), nullable=False),
        sa.Column("approval_reason", sa.String(500), nullable=False),
        sa.Column("approved_by_user_id", sa.String(36), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["model_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "model_id", "candidate_revision", name="uq_model_commercial_plan_candidate"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_model_commercial_plan_idempotency"),
        sa.UniqueConstraint("content_sha256", name="uq_model_commercial_plan_content"),
        sa.CheckConstraint(
            "billing_mode IN ('per_second','per_item')",
            name="ck_model_commercial_plan_billing_mode",
        ),
        sa.CheckConstraint(
            "provider_cost_currency IN ('CNY','USD')",
            name="ck_model_commercial_plan_currency",
        ),
        sa.CheckConstraint(
            "provider_cost_micros > 0 AND provider_cost_cny_micros > 0",
            name="ck_model_commercial_plan_cost_positive",
        ),
        sa.CheckConstraint(
            "fx_cny_micros_per_currency_unit > 0",
            name="ck_model_commercial_plan_fx_positive",
        ),
        sa.CheckConstraint("points_per_cny = 10", name="ck_model_commercial_plan_points_exchange"),
        sa.CheckConstraint("target_margin_bps = 3000", name="ck_model_commercial_plan_margin"),
        sa.CheckConstraint(
            "minimum_price_points > 0 AND personal_price_points >= minimum_price_points "
            "AND enterprise_price_points >= minimum_price_points",
            name="ck_model_commercial_plan_prices",
        ),
        sa.CheckConstraint(
            "enterprise_distribution_scope = 'all_active_point_companies_at_release'",
            name="ck_model_commercial_plan_distribution_scope",
        ),
        *_sha256_constraints("provider_cost_evidence_sha256", "ck_model_commercial_plan_cost_sha"),
        *_sha256_constraints("fx_evidence_sha256", "ck_model_commercial_plan_fx_sha"),
        *_sha256_constraints("content_sha256", "ck_model_commercial_plan_content_sha"),
    )
    op.create_index(
        "ix_model_commercial_plan_model_created",
        "model_commercial_release_plans",
        ["model_id", "created_at", "id"],
    )
    op.create_index(
        "ix_model_commercial_release_plans_model_id",
        "model_commercial_release_plans",
        ["model_id"],
    )
    op.create_table(
        "model_commercial_release_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_blocker_code", sa.String(80), nullable=True),
        sa.Column("last_blocker_message", sa.String(500), nullable=True),
        sa.Column("route_release_evidence", sa.JSON(), nullable=True),
        sa.Column("personal_grant_id", sa.String(36), nullable=True),
        sa.Column("company_grant_count", sa.Integer(), nullable=False),
        sa.Column("company_ids", sa.JSON(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["model_commercial_release_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["personal_grant_id"], ["personal_retail_model_grants.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("plan_id", name="uq_model_commercial_execution_plan"),
        sa.CheckConstraint(
            "state IN ('approved','blocked','released')",
            name="ck_model_commercial_execution_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND company_grant_count >= 0",
            name="ck_model_commercial_execution_counts",
        ),
        sa.CheckConstraint(
            "(state = 'released' AND released_at IS NOT NULL "
            "AND last_blocker_code IS NULL AND last_blocker_message IS NULL) OR "
            "(state <> 'released' AND released_at IS NULL)",
            name="ck_model_commercial_execution_terminal",
        ),
    )
    op.create_index(
        "ix_model_commercial_release_executions_plan_id",
        "model_commercial_release_executions",
        ["plan_id"],
    )
    _create_immutable_guard()
    if (
        op.get_bind().dialect.name == "postgresql"
        and protected_platform_runtime_requested_v15()
    ):
        _apply_acl()


def downgrade() -> None:
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text("SELECT count(*) FROM model_commercial_release_plans")
        ).scalar_one()
    )
    if count:
        raise RuntimeError("0050 downgrade blocked by commercial release plans")
    _drop_immutable_guard()
    op.drop_index(
        "ix_model_commercial_release_executions_plan_id",
        table_name="model_commercial_release_executions",
    )
    op.drop_table("model_commercial_release_executions")
    op.drop_index(
        "ix_model_commercial_release_plans_model_id",
        table_name="model_commercial_release_plans",
    )
    op.drop_index(
        "ix_model_commercial_plan_model_created",
        table_name="model_commercial_release_plans",
    )
    op.drop_table("model_commercial_release_plans")
