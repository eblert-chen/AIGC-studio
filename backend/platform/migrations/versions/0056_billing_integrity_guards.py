"""Repair task route-evidence immutability on PostgreSQL without rewriting facts.

Revision ID: 0056_billing_integrity_guards
Revises: 0055_commercial_plan_revisions
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0056_billing_integrity_guards"
down_revision: str | None = "0055_commercial_plan_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_postgres_provider_checks() -> None:
    # Frozen SQL, not live ORM imports: 0051 used combined constraints while the
    # model contract names their components. Preserve its stronger fingerprint,
    # UUID and prefixed-digest regexes. IS TRUE additionally rejects incomplete
    # bound identities which previously passed CHECK as SQL UNKNOWN.
    identity_complete = "(" + (
        "(provider_identity_status = 'bound' AND schema_version = 2 "
        "AND route_id > 0 AND provider_name IS NOT NULL "
        "AND provider_account_id IS NOT NULL AND provider_channel_id > 0 "
        "AND provider_route_id = route_id AND provider_key_index >= 0 "
        "AND provider_key_fingerprint ~ '^[0-9a-f]{64}$' "
        "AND provider_credential_version ~ "
        "'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' "
        "AND routing_release_sha256 ~ '^sha256:[0-9a-f]{64}$') OR "
        "(provider_identity_status IN ('unassigned', 'legacy_unknown') "
        "AND provider_name IS NULL AND provider_account_id IS NULL "
        "AND provider_channel_id IS NULL AND provider_route_id IS NULL "
        "AND provider_key_index IS NULL AND provider_key_fingerprint IS NULL "
        "AND provider_credential_version IS NULL AND routing_release_sha256 IS NULL)"
    ) + ") IS TRUE"
    status = "provider_identity_status IN ('unassigned', 'bound', 'legacy_unknown')"
    checks = (
        ("channel_cost_entries", "ck_channel_cost_schema", "schema_version IN (1, 2)"),
        ("channel_cost_entries", "ck_channel_cost_provider_identity_status", status),
        ("channel_cost_entries", "ck_channel_cost_provider_identity_complete", identity_complete),
        ("relay_task_stage_events", "ck_relay_task_stage_provider_identity_status", status),
        ("relay_task_stage_events", "ck_relay_task_stage_provider_identity_complete", identity_complete),
        ("relay_task_stage_events", "ck_relay_task_stage_unassigned_route",
         "provider_identity_status <> 'unassigned' OR route_id IS NULL"),
        ("generation_tasks", "ck_task_provider_route_evidence_complete",
         "(provider_route_evidence IS NULL AND provider_route_evidence_sha256 IS NULL) OR "
         "(provider_route_evidence IS NOT NULL AND provider_route_evidence_sha256 IS NOT NULL)"),
        ("generation_tasks", "ck_task_provider_route_evidence_sha",
         "provider_route_evidence_sha256 ~ '^[0-9a-f]{64}$'"),
        ("model_commercial_release_plans", "ck_model_commercial_plan_route_identity_complete",
         "(approved_route_identity IS NULL AND approved_route_identity_sha256 IS NULL) OR "
         "(approved_route_identity IS NOT NULL AND approved_route_identity_sha256 IS NOT NULL)"),
        ("model_commercial_release_plans", "ck_model_commercial_plan_route_identity_sha",
         "approved_route_identity_sha256 ~ '^[0-9a-f]{64}$'"),
        ("model_commercial_release_executions", "ck_model_commercial_execution_receipt_complete",
         "(publication_receipt IS NULL AND publication_receipt_sha256 IS NULL "
         "AND released_route_identity_sha256 IS NULL) OR "
         "(publication_receipt IS NOT NULL AND publication_receipt_sha256 IS NOT NULL "
         "AND released_route_identity_sha256 IS NOT NULL)"),
        ("model_commercial_release_executions", "ck_model_commercial_execution_receipt_sha",
         "publication_receipt_sha256 ~ '^[0-9a-f]{64}$'"),
        ("model_commercial_release_executions", "ck_model_commercial_execution_route_sha",
         "released_route_identity_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    # Add all replacements and validate historical rows before removing any old
    # protection. PostgreSQL's transactional DDL rolls the whole upgrade back
    # if legacy facts violate a new guard; no evidence is repaired or deleted.
    for table, name, expression in checks:
        op.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name}_0056 "
            f"CHECK ({expression}) NOT VALID"
        ))
    for table, name, _ in checks:
        op.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}_0056"))
    for table, name in (
        ("channel_cost_entries", "ck_channel_cost_provider_identity"),
        ("relay_task_stage_events", "ck_relay_task_stage_provider_identity"),
        ("generation_tasks", "ck_task_provider_route_evidence_complete"),
        ("model_commercial_release_plans", "ck_model_commercial_plan_route_identity"),
        ("model_commercial_release_executions", "ck_model_commercial_execution_receipt"),
    ):
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT {name}"))
    for table, name, _ in checks:
        op.execute(sa.text(f"ALTER TABLE {table} RENAME CONSTRAINT {name}_0056 TO {name}"))


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    valid_predecessor = (
        "EXISTS (SELECT 1 FROM model_commercial_release_plans p "
        "JOIN model_commercial_release_executions e ON e.plan_id=p.id "
        "WHERE p.id=NEW.supersedes_plan_id AND p.model_id=NEW.model_id "
        "AND p.candidate_revision=NEW.candidate_revision "
        "AND p.revision + 1=NEW.revision AND e.state IN ('approved','blocked','released'))"
    )
    # A released plan remains immutable history, but may have exactly one
    # explicitly linked successor. Recovery never resets its released state.
    if dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER trg_commercial_plan_revision_insert"))
        op.execute(sa.text(
            "CREATE TRIGGER trg_commercial_plan_revision_insert BEFORE INSERT ON model_commercial_release_plans "
            f"WHEN NEW.revision > 1 AND NOT ({valid_predecessor}) "
            "BEGIN SELECT RAISE(ABORT, 'commercial plan predecessor mismatch'); END"
        ))
        op.execute(sa.text(
            "CREATE TRIGGER trg_commercial_execution_released BEFORE UPDATE ON model_commercial_release_executions "
            "WHEN OLD.state='released' AND (NEW.state IS NOT OLD.state OR NEW.released_at IS NOT OLD.released_at) "
            "BEGIN SELECT RAISE(ABORT, 'released commercial execution is immutable'); END"
        ))
    elif dialect == "postgresql":
        op.execute(sa.text(
            "CREATE OR REPLACE FUNCTION guard_commercial_plan_revision() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN IF NEW.revision > 1 AND NOT ("
            + valid_predecessor + ") THEN RAISE EXCEPTION "
            "'commercial plan predecessor mismatch'; END IF; RETURN NEW; END $$"
        ))
        op.execute(sa.text(
            "CREATE FUNCTION guard_commercial_execution_released() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN IF OLD.state='released' AND "
            "(NEW.state IS DISTINCT FROM OLD.state OR NEW.released_at IS DISTINCT FROM OLD.released_at) "
            "THEN RAISE EXCEPTION 'released commercial execution is immutable'; END IF; RETURN NEW; END $$"
        ))
        op.execute(sa.text(
            "CREATE TRIGGER trg_commercial_execution_released BEFORE UPDATE ON model_commercial_release_executions "
            "FOR EACH ROW EXECUTE FUNCTION guard_commercial_execution_released()"
        ))
    if op.get_bind().dialect.name == "postgresql":
        _normalize_postgres_provider_checks()
        op.execute(sa.text(
            "CREATE OR REPLACE FUNCTION reject_task_provider_route_evidence_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF OLD.provider_route_evidence_sha256 IS NOT NULL AND ("
            "NEW.provider_route_evidence_sha256 IS DISTINCT FROM OLD.provider_route_evidence_sha256 "
            "OR NEW.provider_route_evidence::jsonb IS DISTINCT FROM OLD.provider_route_evidence::jsonb) "
            "THEN RAISE EXCEPTION 'task provider route evidence is immutable'; END IF; "
            "RETURN NEW; END $$"
        ))


def downgrade() -> None:
    raise RuntimeError(
        "0056 downgrade is blocked: restoring the invalid JSON comparison would "
        "break task updates and remove the verified billing recovery boundary"
    )
