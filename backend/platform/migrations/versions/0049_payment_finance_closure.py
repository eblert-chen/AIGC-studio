"""Harden payment delivery, settlement evidence and enterprise collections.

Revision ID: 0049_payment_finance_closure
Revises: 0048_commercial_billing
"""
from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v14 as policy_v14
from platform_api.database_privileges_behavior_v14 import (
    protected_platform_runtime_requested_v14,
)


revision: str = "0049_payment_finance_closure"
down_revision: str | None = "0048_commercial_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_APPEND_ONLY_TABLES = (
    "enterprise_dunning_actions",
    "finance_reconciliation_run_sources",
    "payment_dispute_debt_recovery_allocations",
    "payment_dispute_debt_recovery_reversals",
    "payment_settlement_batches",
    "provider_cost_statement_batches",
    "provider_cost_statement_lines",
)
_DELIVERY_IDENTITY_COLUMNS = {
    "payment_provider_commands": (
        "id", "operation", "order_id", "refund_id", "provider", "merchant_account",
        "idempotency_key", "dedupe_key", "request_payload", "request_sha256", "created_at",
    ),
    "payment_webhook_inbox_events": (
        "id", "provider", "merchant_account", "provider_event_id", "event_type",
        "payload_sha256", "payload_json", "signature_key_id", "signature_timestamp",
        "signature_verified_at", "provider_occurred_at", "received_at", "created_at",
    ),
}
_NO_TRUNCATE_TABLES = _APPEND_ONLY_TABLES + tuple(_DELIVERY_IDENTITY_COLUMNS) + (
    "payment_webhook_receipts",
    "payment_transactions",
    "point_lot_settlement_value_allocations",
    "company_billing_contract_versions",
    "company_invoice_lines",
    "accounts_receivable_ledger_entries",
    "payment_settlement_entries",
    "finance_reconciliation_runs",
    "finance_reconciliation_snapshots",
    "finance_reconciliation_exceptions",
    "finance_reconciliation_resolutions",
)
_NEW_TABLES = (
    "enterprise_dunning_actions",
    "enterprise_dunning_runs",
    "finance_reconciliation_run_sources",
    "payment_dispute_debt_recovery_reversals",
    "payment_dispute_debt_recovery_allocations",
    "payment_provider_commands",
    "payment_webhook_inbox_events",
    "provider_cost_statement_lines",
    "provider_cost_statement_batches",
    "payment_settlement_batches",
    "payment_mandates",
)


def _sha256_constraints(column: str, name: str, *, nullable: bool = False):
    sqlite = (
        f"length({column}) = 64 AND lower({column}) = {column} "
        f"AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    postgres = f"{column} ~ '^[0-9a-f]{{64}}$'"
    if nullable:
        sqlite = f"{column} IS NULL OR ({sqlite})"
        postgres = f"{column} IS NULL OR ({postgres})"
    return (
        sa.CheckConstraint(sqlite, name=name).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(postgres, name=name).ddl_if(dialect="postgresql"),
    )


def _quote(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _apply_acl() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    runtime_roles = tuple(
        role
        for process, role in policy_v14.DATABASE_ROLE_BY_PROCESS.items()
        if process != "migration"
    )
    for table_name in sorted(policy_v14.CLOSURE_TABLES):
        quoted_table = _quote(table_name)
        op.execute(sa.text(f"REVOKE ALL PRIVILEGES ON TABLE public.{quoted_table} FROM PUBLIC"))
        for role in runtime_roles:
            op.execute(
                sa.text(
                    f"REVOKE ALL PRIVILEGES ON TABLE public.{quoted_table} FROM {_quote(role)}"
                )
            )
    for process, privileges_by_table in policy_v14.PRIVILEGES_BY_PROCESS.items():
        role = _quote(policy_v14.DATABASE_ROLE_BY_PROCESS[process])
        for table_name in sorted(policy_v14.CLOSURE_TABLES):
            privileges = privileges_by_table.get(table_name)
            if privileges:
                op.execute(
                    sa.text(
                        f"GRANT {', '.join(sorted(privileges))} ON TABLE "
                        f"public.{_quote(table_name)} TO {role}"
                    )
                )


def _assert_empty_settlement_projection() -> None:
    count = int(
        op.get_bind().execute(sa.text("SELECT count(*) FROM payment_settlement_entries")).scalar_one()
    )
    if count:
        raise RuntimeError(
            "0049 requires an operator-reviewed, history-preserving migration plan "
            "for legacy unbound settlement rows; no source evidence is inferred"
        )


def _assert_no_unbound_automatic_orders() -> None:
    count = int(
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM payment_orders WHERE automatic = true"))
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            "0049 requires an operator-reviewed, history-preserving migration plan "
            "for legacy automatic payment orders; no payment mandate is inferred"
        )


def _repair_postgres_deferred_foreign_keys() -> None:
    """Materialize 0048 use_alter FKs that PostgreSQL did not emit inline."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_foreign_key(
        "fk_company_contract_supersedes",
        "company_billing_contract_versions",
        "company_billing_contract_versions",
        ["supersedes_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_personal_point_lot_payment_order",
        "personal_point_lots",
        "payment_orders",
        ["payment_order_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def _drop_repaired_postgres_deferred_foreign_keys() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(
        "fk_personal_point_lot_payment_order",
        "personal_point_lots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_company_contract_supersedes",
        "company_billing_contract_versions",
        type_="foreignkey",
    )


def _create_company_billing_cycle_overlap_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_company_billing_cycle_no_overlap "
                "BEFORE INSERT ON company_billing_cycles WHEN EXISTS ("
                "SELECT 1 FROM company_billing_cycles c "
                "WHERE c.company_id = NEW.company_id "
                "AND c.period_start < NEW.period_end "
                "AND c.period_end > NEW.period_start) BEGIN SELECT "
                "RAISE(ABORT, 'company billing cycle period overlaps existing cycle'); END"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_company_billing_cycle_period_immutable "
                "BEFORE UPDATE OF company_id,period_start,period_end "
                "ON company_billing_cycles BEGIN SELECT "
                "RAISE(ABORT, 'company billing cycle period is immutable'); END"
            )
        )
        return
    if dialect != "postgresql":
        return
    op.execute(
        sa.text(
            "CREATE FUNCTION guard_company_billing_cycle_no_overlap() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF TG_OP = 'UPDATE' AND (NEW.company_id IS DISTINCT FROM OLD.company_id "
            "OR NEW.period_start IS DISTINCT FROM OLD.period_start "
            "OR NEW.period_end IS DISTINCT FROM OLD.period_end) THEN "
            "RAISE EXCEPTION 'company billing cycle period is immutable'; END IF; "
            "PERFORM pg_advisory_xact_lock(hashtextextended(NEW.company_id, 0)); "
            "IF EXISTS (SELECT 1 FROM company_billing_cycles c "
            "WHERE c.company_id = NEW.company_id AND c.id <> NEW.id "
            "AND tstzrange(c.period_start, c.period_end, '[)') "
            "&& tstzrange(NEW.period_start, NEW.period_end, '[)')) THEN "
            "RAISE EXCEPTION 'company billing cycle period overlaps existing cycle' "
            "USING ERRCODE = '23P01'; END IF; RETURN NEW; END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_company_billing_cycle_no_overlap "
            "BEFORE INSERT "
            "ON company_billing_cycles FOR EACH ROW EXECUTE FUNCTION "
            "guard_company_billing_cycle_no_overlap()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_company_billing_cycle_period_immutable "
            "BEFORE UPDATE OF company_id,period_start,period_end "
            "ON company_billing_cycles FOR EACH ROW EXECUTE FUNCTION "
            "guard_company_billing_cycle_no_overlap()"
        )
    )
    op.execute(
        sa.text(
            "REVOKE ALL PRIVILEGES ON FUNCTION "
            "guard_company_billing_cycle_no_overlap() FROM PUBLIC"
        )
    )


def _drop_company_billing_cycle_overlap_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_company_billing_cycle_no_overlap"))
        op.execute(
            sa.text("DROP TRIGGER IF EXISTS trg_company_billing_cycle_period_immutable")
        )
        return
    if dialect != "postgresql":
        return
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_company_billing_cycle_no_overlap "
            "ON company_billing_cycles"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_company_billing_cycle_period_immutable "
            "ON company_billing_cycles"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS guard_company_billing_cycle_no_overlap()")
    )


def _create_immutable_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table_name, columns in _DELIVERY_IDENTITY_COLUMNS.items():
            changed = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_{table_name}_identity BEFORE UPDATE ON {table_name} "
                    f"WHEN {changed} BEGIN SELECT "
                    "RAISE(ABORT, 'payment delivery identity is immutable'); END"
                )
            )
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name} "
                    "BEGIN SELECT RAISE(ABORT, 'payment delivery records are durable'); END"
                )
            )
        for table_name in _APPEND_ONLY_TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER trg_{table_name}_closure_no_{action.lower()} "
                        f"BEFORE {action} ON {table_name} BEGIN SELECT "
                        "RAISE(ABORT, 'payment closure fact is immutable'); END"
                    )
                )
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_settlement_entries_no_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_settlement_entries_no_delete"))
        for action in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_payment_settlement_entries_no_{action.lower()} "
                    f"BEFORE {action} ON payment_settlement_entries BEGIN SELECT "
                    "RAISE(ABORT, 'commercial billing fact is immutable'); END"
                )
            )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_finance_reconciliation_run_finalize BEFORE UPDATE ON "
                "finance_reconciliation_runs WHEN OLD.status <> 'RUNNING' OR "
                "NEW.status = 'RUNNING' OR NEW.run_kind <> OLD.run_kind OR "
                "NEW.period_start <> OLD.period_start OR NEW.period_end <> OLD.period_end OR "
                "NEW.provider IS NOT OLD.provider OR "
                "NEW.merchant_account IS NOT OLD.merchant_account OR "
                "NEW.source_watermarks <> OLD.source_watermarks OR "
                "NEW.idempotency_key <> OLD.idempotency_key OR "
                "NEW.started_at <> OLD.started_at "
                "BEGIN SELECT RAISE(ABORT, 'finance reconciliation conclusion is immutable'); END"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_finance_reconciliation_run_no_delete BEFORE DELETE ON "
                "finance_reconciliation_runs BEGIN SELECT "
                "RAISE(ABORT, 'finance reconciliation run is immutable'); END"
            )
        )
        return
    if dialect != "postgresql":
        return
    op.execute(
        sa.text(
            "CREATE FUNCTION reject_payment_closure_fact_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'payment closure fact is immutable'; END $$"
        )
    )
    for table_name in _NO_TRUNCATE_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_truncate BEFORE TRUNCATE ON {table_name} "
                "FOR EACH STATEMENT EXECUTE FUNCTION reject_payment_closure_fact_mutation()"
            )
        )
    for table_name, columns in _DELIVERY_IDENTITY_COLUMNS.items():
        comparisons = []
        for column in columns:
            cast = "::jsonb" if column in {"request_payload", "payload_json"} else ""
            comparisons.append(f"NEW.{column}{cast} IS DISTINCT FROM OLD.{column}{cast}")
        changed = " OR ".join(comparisons)
        op.execute(
            sa.text(
                f"CREATE FUNCTION guard_{table_name}_identity() RETURNS trigger "
                f"LANGUAGE plpgsql AS $$ BEGIN IF {changed} THEN "
                "RAISE EXCEPTION 'payment delivery identity is immutable'; END IF; "
                "RETURN NEW; END $$"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_identity BEFORE UPDATE ON {table_name} "
                f"FOR EACH ROW EXECUTE FUNCTION guard_{table_name}_identity()"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION reject_payment_closure_fact_mutation()"
            )
        )
    for table_name in _APPEND_ONLY_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_closure_immutable BEFORE UPDATE OR DELETE "
                f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION "
                "reject_payment_closure_fact_mutation()"
            )
        )
    op.execute(
        sa.text(
            "CREATE FUNCTION guard_finance_reconciliation_run_finalization() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF OLD.status <> 'RUNNING' OR NEW.status = 'RUNNING' OR "
            "NEW.run_kind IS DISTINCT FROM OLD.run_kind OR "
            "NEW.period_start IS DISTINCT FROM OLD.period_start OR "
            "NEW.period_end IS DISTINCT FROM OLD.period_end OR "
            "NEW.provider IS DISTINCT FROM OLD.provider OR "
            "NEW.merchant_account IS DISTINCT FROM OLD.merchant_account OR "
            "NEW.source_watermarks::jsonb IS DISTINCT FROM OLD.source_watermarks::jsonb OR "
            "NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key OR "
            "NEW.started_at IS DISTINCT FROM OLD.started_at THEN "
            "RAISE EXCEPTION 'finance reconciliation conclusion is immutable'; END IF; "
            "RETURN NEW; END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_finance_reconciliation_run_finalize BEFORE UPDATE ON "
            "finance_reconciliation_runs FOR EACH ROW EXECUTE FUNCTION "
            "guard_finance_reconciliation_run_finalization()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_finance_reconciliation_run_no_delete BEFORE DELETE ON "
            "finance_reconciliation_runs FOR EACH ROW EXECUTE FUNCTION "
            "reject_payment_closure_fact_mutation()"
        )
    )


def _drop_immutable_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table_name in _DELIVERY_IDENTITY_COLUMNS:
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_identity"))
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete"))
        for table_name in _APPEND_ONLY_TABLES:
            for action in ("update", "delete"):
                op.execute(
                    sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_closure_no_{action}")
                )
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_finance_reconciliation_run_finalize"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_finance_reconciliation_run_no_delete"))
        return
    if dialect != "postgresql":
        return
    for table_name in _NO_TRUNCATE_TABLES:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate ON {table_name}"))
    for table_name in _DELIVERY_IDENTITY_COLUMNS:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_identity ON {table_name}"))
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete ON {table_name}"))
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS guard_{table_name}_identity()"))
    for table_name in _APPEND_ONLY_TABLES:
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_closure_immutable ON {table_name}"
            )
        )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_finance_reconciliation_run_finalize "
            "ON finance_reconciliation_runs"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_finance_reconciliation_run_no_delete "
            "ON finance_reconciliation_runs"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS guard_finance_reconciliation_run_finalization"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS reject_payment_closure_fact_mutation"))


def upgrade() -> None:
    _assert_empty_settlement_projection()
    _assert_no_unbound_automatic_orders()
    _repair_postgres_deferred_foreign_keys()
    _create_company_billing_cycle_overlap_guard()

    op.create_table(
        "payment_mandates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("personal_workspace_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("merchant_account", sa.String(120), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "ACTIVE", "REVOKED", "FAILED", name="paymentmandatestatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("provider_customer_reference", sa.String(240), nullable=True),
        sa.Column("provider_payment_method_reference", sa.String(240), nullable=True),
        sa.Column("provider_mandate_reference", sa.String(240), nullable=True),
        sa.Column("consent_version", sa.String(80), nullable=False),
        sa.Column("consent_sha256", sa.String(64), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["personal_workspace_id"], ["personal_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_mandate_key"),
        sa.UniqueConstraint(
            "provider", "merchant_account", "provider_mandate_reference",
            name="uq_payment_mandate_provider_reference",
        ),
        sa.CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_payment_mandate_scope",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND provider_customer_reference IS NOT NULL "
            "AND provider_payment_method_reference IS NOT NULL "
            "AND provider_mandate_reference IS NOT NULL AND consented_at IS NOT NULL "
            "AND verified_at IS NOT NULL AND revoked_at IS NULL) OR status <> 'ACTIVE'",
            name="ck_payment_mandate_active_evidence",
        ),
        *_sha256_constraints("consent_sha256", "ck_payment_mandate_consent_sha256"),
    )
    op.create_index("ix_payment_mandates_company_id", "payment_mandates", ["company_id"])
    op.create_index(
        "ix_payment_mandates_personal_workspace_id",
        "payment_mandates",
        ["personal_workspace_id"],
    )
    with op.batch_alter_table("payment_orders") as batch:
        batch.add_column(sa.Column("payment_mandate_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_payment_order_mandate",
            "payment_mandates",
            ["payment_mandate_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_payment_orders_payment_mandate_id", ["payment_mandate_id"])
        batch.create_check_constraint(
            "ck_payment_order_automatic_mandate",
            "(automatic = true AND payment_mandate_id IS NOT NULL "
            "AND provider_customer_reference IS NOT NULL) OR "
            "(automatic = false AND payment_mandate_id IS NULL)",
        )
    with op.batch_alter_table("auto_recharge_rules") as batch:
        batch.add_column(sa.Column("mandate_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_auto_recharge_rule_mandate", "payment_mandates", ["mandate_id"], ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_auto_recharge_rules_mandate_id", ["mandate_id"])
        batch.create_index("ix_auto_recharge_due", ["enabled", "next_check_at", "id"])

    op.create_table(
        "payment_provider_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "operation",
            sa.Enum(
                "CREATE_PAYMENT", "CREATE_REFUND", "QUERY_PAYMENT", "QUERY_REFUND",
                name="paymentprovidercommandoperation", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("refund_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("merchant_account", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "CLAIMED", "UNKNOWN", "SUCCEEDED", "FAILED", "DEAD",
                name="paymentprovidercommandstatus", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(80), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_resource_id", sa.String(160), nullable=True),
        sa.Column("response_sha256", sa.String(64), nullable=True),
        sa.Column("last_error_code", sa.String(120), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["refund_id"], ["payment_refunds.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_provider_command_key"),
        sa.UniqueConstraint("dedupe_key", name="uq_payment_provider_command_dedupe"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_payment_provider_command_attempts"),
        sa.CheckConstraint(
            "(operation IN ('CREATE_PAYMENT','QUERY_PAYMENT') AND order_id IS NOT NULL "
            "AND refund_id IS NULL) OR (operation IN ('CREATE_REFUND','QUERY_REFUND') "
            "AND order_id IS NOT NULL AND refund_id IS NOT NULL)",
            name="ck_payment_provider_command_target",
        ),
        sa.CheckConstraint(
            "(status = 'CLAIMED' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR status <> 'CLAIMED'",
            name="ck_payment_provider_command_lease",
        ),
        *_sha256_constraints("request_sha256", "ck_payment_provider_command_request_sha256"),
        *_sha256_constraints(
            "response_sha256", "ck_payment_provider_command_response_sha256", nullable=True
        ),
    )
    op.create_index(
        "ix_payment_provider_command_dispatch",
        "payment_provider_commands",
        ["status", "next_attempt_at", "created_at", "id"],
    )
    op.create_index("ix_payment_provider_commands_order_id", "payment_provider_commands", ["order_id"])
    op.create_index("ix_payment_provider_commands_refund_id", "payment_provider_commands", ["refund_id"])

    op.create_table(
        "payment_webhook_inbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("merchant_account", sa.String(120), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("signature_key_id", sa.String(120), nullable=False),
        sa.Column("signature_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED", "PROCESSING", "BLOCKED", "PROCESSED", "DEAD",
                name="paymentwebhookinboxstatus", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(80), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_on", sa.String(160), nullable=True),
        sa.Column("last_error_code", sa.String(120), nullable=True),
        sa.Column("processed_receipt_id", sa.String(36), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["processed_receipt_id"], ["payment_webhook_receipts.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "provider", "merchant_account", "provider_event_id",
            name="uq_payment_webhook_inbox_provider_event",
        ),
        sa.UniqueConstraint("processed_receipt_id"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_payment_webhook_inbox_attempts"),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR status <> 'PROCESSING'",
            name="ck_payment_webhook_inbox_lease",
        ),
        *_sha256_constraints("payload_sha256", "ck_payment_webhook_inbox_payload_sha256"),
    )
    op.create_index(
        "ix_payment_webhook_inbox_replay",
        "payment_webhook_inbox_events",
        ["status", "next_attempt_at", "received_at", "id"],
    )

    op.create_table(
        "payment_dispute_debt_recovery_allocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dispute_id", sa.String(36), nullable=False),
        sa.Column("recovery_payment_transaction_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("personal_workspace_id", sa.String(36), nullable=True),
        sa.Column("recovered_points", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dispute_id"], ["payment_disputes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["recovery_payment_transaction_id"], ["payment_transactions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["personal_workspace_id"], ["personal_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "dispute_id", "recovery_payment_transaction_id",
            name="uq_payment_dispute_debt_recovery_transaction",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_dispute_debt_recovery_key"),
        sa.CheckConstraint("recovered_points > 0", name="ck_payment_dispute_debt_recovery_points"),
        sa.CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_payment_dispute_debt_recovery_scope",
        ),
    )
    op.create_index(
        "ix_payment_dispute_debt_recovery_allocations_dispute_id",
        "payment_dispute_debt_recovery_allocations", ["dispute_id"],
    )
    op.create_index(
        "ix_dispute_debt_recovery_transaction",
        "payment_dispute_debt_recovery_allocations", ["recovery_payment_transaction_id"],
    )
    op.create_index(
        "ix_payment_dispute_debt_recovery_allocations_company_id",
        "payment_dispute_debt_recovery_allocations", ["company_id"],
    )
    op.create_index(
        "ix_dispute_debt_recovery_personal",
        "payment_dispute_debt_recovery_allocations", ["personal_workspace_id"],
    )
    op.create_table(
        "payment_dispute_debt_recovery_reversals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("allocation_id", sa.String(36), nullable=False),
        sa.Column("dispute_id", sa.String(36), nullable=False),
        sa.Column("restored_points", sa.BigInteger(), nullable=False),
        sa.Column("company_ledger_entry_id", sa.String(36), nullable=True),
        sa.Column("personal_ledger_entry_id", sa.String(36), nullable=True),
        sa.Column("company_point_lot_id", sa.String(36), nullable=True),
        sa.Column("personal_point_lot_id", sa.String(36), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["allocation_id"], ["payment_dispute_debt_recovery_allocations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["dispute_id"], ["payment_disputes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["company_ledger_entry_id"], ["company_point_ledger_entries.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["personal_ledger_entry_id"], ["personal_ledger_entries.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["company_point_lot_id"], ["company_point_lots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["personal_point_lot_id"], ["personal_point_lots.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("allocation_id", name="uq_payment_dispute_debt_reversal_allocation"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_dispute_debt_reversal_key"),
        sa.CheckConstraint("restored_points > 0", name="ck_payment_dispute_debt_reversal_points"),
        sa.CheckConstraint(
            "(company_ledger_entry_id IS NOT NULL AND personal_ledger_entry_id IS NULL "
            "AND company_point_lot_id IS NOT NULL AND personal_point_lot_id IS NULL) OR "
            "(company_ledger_entry_id IS NULL AND personal_ledger_entry_id IS NOT NULL "
            "AND company_point_lot_id IS NULL AND personal_point_lot_id IS NOT NULL)",
            name="ck_payment_dispute_debt_reversal_scope",
        ),
    )
    op.create_index(
        "ix_payment_dispute_debt_recovery_reversals_allocation_id",
        "payment_dispute_debt_recovery_reversals", ["allocation_id"],
    )
    op.create_index(
        "ix_payment_dispute_debt_recovery_reversals_dispute_id",
        "payment_dispute_debt_recovery_reversals", ["dispute_id"],
    )

    op.create_table(
        "payment_settlement_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("merchant_account", sa.String(120), nullable=False),
        sa.Column(
            "source_kind",
            sa.Enum("PSP_STATEMENT", "BANK_STATEMENT", name="paymentsettlementsourcekind", native_enum=False),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_document_id", sa.String(160), nullable=True),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("source_document_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_object_key", sa.String(512), nullable=False),
        sa.Column("source_object_version", sa.String(160), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("verification_method", sa.String(40), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("lines_sha256", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("gross_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("fee_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("net_total_cents", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "merchant_account", "source_kind", "source_document_sha256",
            name="uq_payment_settlement_batch_document",
        ),
        sa.UniqueConstraint(
            "provider", "merchant_account", "source_kind", "lines_sha256",
            name="uq_payment_settlement_batch_lines",
        ),
        sa.CheckConstraint("period_end > period_start", name="ck_payment_settlement_batch_period"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_payment_settlement_batch_currency"),
        sa.CheckConstraint(
            "line_count > 0 AND source_size_bytes > 0 AND fee_total_cents >= 0",
            name="ck_payment_settlement_batch_counts",
        ),
        sa.CheckConstraint(
            "length(source_document_bytes) = source_size_bytes "
            "AND source_size_bytes <= 52428800",
            name="ck_payment_settlement_source_bytes",
        ),
        sa.CheckConstraint(
            "net_total_cents = gross_total_cents - fee_total_cents",
            name="ck_payment_settlement_batch_totals",
        ),
        *_sha256_constraints(
            "source_document_sha256", "ck_payment_settlement_batch_document_sha256"
        ),
        *_sha256_constraints("lines_sha256", "ck_payment_settlement_batch_lines_sha256"),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_settlement_entries_no_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_settlement_entries_no_delete"))
    with op.batch_alter_table("payment_settlement_entries") as batch:
        batch.add_column(sa.Column("batch_id", sa.String(36), nullable=False))
        batch.add_column(sa.Column("related_provider_reference", sa.String(160), nullable=True))
        batch.create_foreign_key(
            "fk_payment_settlement_entry_batch", "payment_settlement_batches",
            ["batch_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_index("ix_payment_settlement_entries_batch_id", ["batch_id"])
        batch.create_check_constraint(
            "ck_payment_settlement_line_type",
            "line_type IN ('capture','refund','chargeback','dispute_reversal',"
            "'fee','payout','bank_deposit')",
        )
        batch.create_check_constraint(
            "ck_payment_settlement_amounts",
            "fee_amount_cents >= 0 AND net_amount_cents = gross_amount_cents - fee_amount_cents",
        )

    op.create_table(
        "provider_cost_statement_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("supplier", sa.String(120), nullable=False),
        sa.Column("supplier_account", sa.String(160), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_document_id", sa.String(160), nullable=True),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("source_document_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_object_key", sa.String(512), nullable=False),
        sa.Column("source_object_version", sa.String(160), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("verification_method", sa.String(40), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(80), nullable=False),
        sa.Column("lines_sha256", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("total_cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "supplier", "supplier_account", "source_document_sha256",
            name="uq_provider_cost_batch_document",
        ),
        sa.UniqueConstraint(
            "supplier", "supplier_account", "lines_sha256",
            name="uq_provider_cost_batch_lines",
        ),
        sa.CheckConstraint("period_end > period_start", name="ck_provider_cost_batch_period"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_provider_cost_batch_currency"),
        sa.CheckConstraint(
            "line_count > 0 AND source_size_bytes > 0 AND total_cost_cents >= 0",
            name="ck_provider_cost_batch_totals",
        ),
        sa.CheckConstraint(
            "length(source_document_bytes) = source_size_bytes "
            "AND source_size_bytes <= 52428800",
            name="ck_provider_cost_statement_source_bytes",
        ),
        *_sha256_constraints("source_document_sha256", "ck_provider_cost_batch_document_sha256"),
        *_sha256_constraints("lines_sha256", "ck_provider_cost_batch_lines_sha256"),
    )
    op.create_table(
        "provider_cost_statement_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("supplier", sa.String(120), nullable=False),
        sa.Column("supplier_account", sa.String(160), nullable=False),
        sa.Column("provider_line_id", sa.String(160), nullable=False),
        sa.Column("provider_job_reference", sa.String(160), nullable=False),
        sa.Column("channel_key", sa.String(120), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["provider_cost_statement_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "supplier", "supplier_account", "provider_line_id",
            name="uq_provider_cost_statement_line",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_provider_cost_statement_amount"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_provider_cost_statement_currency"),
    )
    op.create_index(
        "ix_provider_cost_statement_lines_batch_id", "provider_cost_statement_lines", ["batch_id"]
    )
    op.create_index(
        "ix_provider_cost_statement_lines_task_id", "provider_cost_statement_lines", ["task_id"]
    )

    op.create_table(
        "finance_reconciliation_run_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("payment_settlement_batch_id", sa.String(36), nullable=True),
        sa.Column("provider_cost_batch_id", sa.String(36), nullable=True),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["finance_reconciliation_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_settlement_batch_id"], ["payment_settlement_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_cost_batch_id"], ["provider_cost_statement_batches.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "run_id", "payment_settlement_batch_id",
            name="uq_finance_reconciliation_run_payment_batch",
        ),
        sa.UniqueConstraint(
            "run_id", "provider_cost_batch_id",
            name="uq_finance_reconciliation_run_cost_batch",
        ),
        sa.CheckConstraint(
            "(source_kind = 'payment_settlement' AND payment_settlement_batch_id IS NOT NULL "
            "AND provider_cost_batch_id IS NULL) OR (source_kind = 'provider_cost' "
            "AND payment_settlement_batch_id IS NULL AND provider_cost_batch_id IS NOT NULL)",
            name="ck_finance_reconciliation_run_source_kind",
        ),
        *_sha256_constraints("document_sha256", "ck_finance_reconciliation_run_source_sha256"),
    )
    op.create_index(
        "ix_finance_reconciliation_run_sources_run_id",
        "finance_reconciliation_run_sources", ["run_id"],
    )

    with op.batch_alter_table("company_billing_accounts") as batch:
        batch.add_column(sa.Column("billing_hold_reason", sa.String(120), nullable=True))
        batch.add_column(sa.Column("billing_hold_since", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("dunning_level", sa.Integer(), server_default="0", nullable=False))
        batch.create_check_constraint("ck_company_dunning_level", "dunning_level >= 0")
        batch.create_check_constraint(
            "ck_company_billing_hold_evidence",
            "(billing_hold = false AND billing_hold_since IS NULL "
            "AND billing_hold_reason IS NULL AND dunning_level = 0) "
            "OR billing_hold = true",
        )
    op.create_table(
        "enterprise_dunning_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("RUNNING", "COMPLETED", "FAILED", name="enterprisedunningrunstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("overdue_count", sa.Integer(), nullable=False),
        sa.Column("hold_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_enterprise_dunning_run_key"),
        sa.CheckConstraint(
            "scanned_count >= 0 AND overdue_count >= 0 AND hold_count >= 0",
            name="ck_enterprise_dunning_run_counts",
        ),
        *_sha256_constraints("intent_sha256", "ck_enterprise_dunning_intent_sha256"),
    )
    op.create_index("ix_enterprise_dunning_runs_company_id", "enterprise_dunning_runs", ["company_id"])
    op.create_table(
        "enterprise_dunning_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("invoice_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["enterprise_dunning_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["company_invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "invoice_id", "action", "stage", name="uq_enterprise_dunning_invoice_action"
        ),
        sa.CheckConstraint("stage >= 1", name="ck_enterprise_dunning_action_stage"),
        sa.CheckConstraint(
            "action IN ('mark_overdue','apply_hold','clear_hold')",
            name="ck_enterprise_dunning_action_kind",
        ),
    )
    op.create_index("ix_enterprise_dunning_actions_run_id", "enterprise_dunning_actions", ["run_id"])
    op.create_index(
        "ix_enterprise_dunning_actions_invoice_id", "enterprise_dunning_actions", ["invoice_id"]
    )
    op.create_index(
        "ix_enterprise_dunning_actions_company_id", "enterprise_dunning_actions", ["company_id"]
    )

    with op.batch_alter_table("payment_orders") as batch:
        batch.drop_constraint("ck_payment_order_amount_totals", type_="check")
        batch.create_check_constraint(
            "ck_payment_order_amount_totals",
            "captured_amount_cents >= 0 AND refunded_amount_cents >= 0 "
            "AND disputed_amount_cents >= 0 AND fee_amount_cents >= 0 "
            "AND captured_amount_cents <= amount_cents "
            "AND refunded_amount_cents + disputed_amount_cents <= captured_amount_cents",
        )
    with op.batch_alter_table("payment_disputes") as batch:
        batch.drop_constraint("ck_payment_dispute_points", type_="check")
        batch.create_check_constraint(
            "ck_payment_dispute_points",
            "points >= 0 AND recovered_available_points >= 0 AND debt_points >= 0 "
            "AND recovered_available_points + debt_points = points",
        )
    with op.batch_alter_table("company_invoices") as batch:
        batch.drop_constraint("ck_company_invoice_totals_nonnegative", type_="check")
        batch.create_check_constraint(
            "ck_company_invoice_totals_nonnegative",
            "subtotal_cents >= 0 AND credit_cents >= 0 AND tax_cents >= 0 "
            "AND total_cents >= 0 AND paid_cents >= 0 AND paid_cents <= total_cents",
        )

    _create_immutable_guards()
    if (
        op.get_bind().dialect.name == "postgresql"
        and protected_platform_runtime_requested_v14()
    ):
        _apply_acl()


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in _NEW_TABLES:
        count = int(bind.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar_one())
        if count:
            raise RuntimeError(f"0049 downgrade blocked by financial facts in {table_name}")
    account_state = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM company_billing_accounts WHERE "
                "billing_hold_reason IS NOT NULL OR billing_hold_since IS NOT NULL "
                "OR dunning_level <> 0"
            )
        ).scalar_one()
    )
    if account_state:
        raise RuntimeError("0049 downgrade blocked by enterprise dunning state")
    _drop_immutable_guards()
    _drop_company_billing_cycle_overlap_guard()
    _drop_repaired_postgres_deferred_foreign_keys()

    with op.batch_alter_table("company_invoices") as batch:
        batch.drop_constraint("ck_company_invoice_totals_nonnegative", type_="check")
        batch.create_check_constraint(
            "ck_company_invoice_totals_nonnegative",
            "subtotal_cents >= 0 AND credit_cents >= 0 AND tax_cents >= 0 "
            "AND total_cents >= 0 AND paid_cents >= 0",
        )
    with op.batch_alter_table("payment_disputes") as batch:
        batch.drop_constraint("ck_payment_dispute_points", type_="check")
        batch.create_check_constraint("ck_payment_dispute_points", "points >= 0")
    with op.batch_alter_table("payment_orders") as batch:
        batch.drop_constraint("ck_payment_order_amount_totals", type_="check")
        batch.create_check_constraint(
            "ck_payment_order_amount_totals",
            "captured_amount_cents >= 0 AND refunded_amount_cents >= 0 "
            "AND disputed_amount_cents >= 0 AND fee_amount_cents >= 0",
        )

    op.drop_table("enterprise_dunning_actions")
    op.drop_table("enterprise_dunning_runs")
    with op.batch_alter_table("company_billing_accounts") as batch:
        batch.drop_constraint("ck_company_billing_hold_evidence", type_="check")
        batch.drop_constraint("ck_company_dunning_level", type_="check")
        batch.drop_column("dunning_level")
        batch.drop_column("billing_hold_since")
        batch.drop_column("billing_hold_reason")
    op.drop_table("finance_reconciliation_run_sources")
    op.drop_table("provider_cost_statement_lines")
    op.drop_table("provider_cost_statement_batches")
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_settlement_entries_no_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_settlement_entries_no_delete"))
    with op.batch_alter_table("payment_settlement_entries") as batch:
        batch.drop_constraint("ck_payment_settlement_amounts", type_="check")
        batch.drop_constraint("ck_payment_settlement_line_type", type_="check")
        batch.drop_index("ix_payment_settlement_entries_batch_id")
        batch.drop_constraint("fk_payment_settlement_entry_batch", type_="foreignkey")
        batch.drop_column("related_provider_reference")
        batch.drop_column("batch_id")
    op.drop_table("payment_settlement_batches")
    op.drop_table("payment_dispute_debt_recovery_reversals")
    op.drop_table("payment_dispute_debt_recovery_allocations")
    op.drop_table("payment_webhook_inbox_events")
    op.drop_table("payment_provider_commands")
    with op.batch_alter_table("auto_recharge_rules") as batch:
        batch.drop_index("ix_auto_recharge_due")
        batch.drop_index("ix_auto_recharge_rules_mandate_id")
        batch.drop_constraint("fk_auto_recharge_rule_mandate", type_="foreignkey")
        batch.drop_column("mandate_id")
        batch.drop_column("next_check_at")
    with op.batch_alter_table("payment_orders") as batch:
        batch.drop_constraint("ck_payment_order_automatic_mandate", type_="check")
        batch.drop_index("ix_payment_orders_payment_mandate_id")
        batch.drop_constraint("fk_payment_order_mandate", type_="foreignkey")
        batch.drop_column("payment_mandate_id")
    op.drop_table("payment_mandates")
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_payment_settlement_entries_no_{action.lower()} "
                    f"BEFORE {action} ON payment_settlement_entries BEGIN SELECT "
                    "RAISE(ABORT, 'commercial billing fact is immutable'); END"
                )
            )
