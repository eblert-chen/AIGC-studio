"""Append immutable commercial approval revisions without losing history.

Revision ID: 0055_commercial_plan_revisions
Revises: 0054_input_asset_media_metadata
"""
from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0055_commercial_plan_revisions"
down_revision: str | None = "0054_input_asset_media_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN = "model_commercial_release_plans"
EXECUTION = "model_commercial_release_executions"


def _capture_sqlite_triggers() -> tuple[str, ...]:
    if op.get_bind().dialect.name != "sqlite":
        return ()
    rows = tuple(op.get_bind().execute(sa.text(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
        "AND sql IS NOT NULL ORDER BY name"
    )))
    for name, _ in rows:
        op.execute(sa.text('DROP TRIGGER "' + name.replace('"', '""') + '"'))
    return tuple(sql for _, sql in rows)


def _restore_sqlite_triggers(statements: tuple[str, ...], *, repair_evidence: bool = False) -> None:
    for statement in statements:
        if repair_evidence and any(name in statement for name in (
            "trg_model_commercial_plan_route_identity_insert",
            "trg_model_commercial_execution_receipt_insert",
            "trg_model_commercial_execution_receipt_update",
        )):
            # 0051 used bare identifiers in trigger WHEN expressions. SQLite
            # requires NEW.column; repair only these exact historical guards.
            statement = re.sub(
                r"(?<![.\w])(approved_route_identity_sha256|approved_route_identity|publication_receipt_sha256|publication_receipt|released_route_identity_sha256)\b",
                r"NEW.\1", statement,
            )
        op.execute(sa.text(statement))


def _create_guards() -> None:
    # The unique predecessor constraint prevents forks. This check binds every
    # successor to the same candidate and the immediately preceding revision.
    valid_predecessor = (
        "EXISTS (SELECT 1 FROM model_commercial_release_plans p "
        "JOIN model_commercial_release_executions e ON e.plan_id=p.id "
        "WHERE p.id=NEW.supersedes_plan_id AND p.model_id=NEW.model_id "
        "AND p.candidate_revision=NEW.candidate_revision "
        "AND p.revision + 1=NEW.revision AND e.state <> 'released')"
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(sa.text(
            f"CREATE TRIGGER trg_commercial_plan_revision_insert BEFORE INSERT ON {PLAN} "
            f"WHEN NEW.revision > 1 AND NOT ({valid_predecessor}) "
            "BEGIN SELECT RAISE(ABORT, 'commercial plan predecessor mismatch'); END"
        ))
        op.execute(sa.text(
            f"CREATE TRIGGER trg_commercial_execution_superseded BEFORE UPDATE ON {EXECUTION} "
            "WHEN OLD.state='superseded' AND NEW.state<>'superseded' "
            "BEGIN SELECT RAISE(ABORT, 'superseded commercial plan cannot execute'); END"
        ))
    elif dialect == "postgresql":
        op.execute(sa.text(
            "CREATE FUNCTION guard_commercial_plan_revision() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN IF NEW.revision > 1 AND NOT ("
            + valid_predecessor + ") THEN RAISE EXCEPTION "
            "'commercial plan predecessor mismatch'; END IF; RETURN NEW; END $$"
        ))
        op.execute(sa.text(
            f"CREATE TRIGGER trg_commercial_plan_revision_insert BEFORE INSERT ON {PLAN} "
            "FOR EACH ROW EXECUTE FUNCTION guard_commercial_plan_revision()"
        ))
        op.execute(sa.text(
            "CREATE FUNCTION guard_commercial_execution_superseded() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN IF OLD.state='superseded' AND "
            "NEW.state<>'superseded' THEN RAISE EXCEPTION "
            "'superseded commercial plan cannot execute'; END IF; RETURN NEW; END $$"
        ))
        op.execute(sa.text(
            f"CREATE TRIGGER trg_commercial_execution_superseded BEFORE UPDATE ON {EXECUTION} "
            "FOR EACH ROW EXECUTE FUNCTION guard_commercial_execution_superseded()"
        ))


def _receipt_immutability_function(*, repaired: bool) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    cast = "::jsonb" if repaired else ""
    # PostgreSQL JSON has no equality operator; retain immutable semantics via
    # JSONB comparison, without changing the stored historical JSON bytes.
    op.execute(sa.text(
        "CREATE OR REPLACE FUNCTION reject_commercial_receipt_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN IF OLD.publication_receipt_sha256 IS NOT NULL "
        "AND (NEW.publication_receipt_sha256 IS DISTINCT FROM OLD.publication_receipt_sha256 "
        f"OR NEW.publication_receipt{cast} IS DISTINCT FROM OLD.publication_receipt{cast} "
        "OR NEW.released_route_identity_sha256 IS DISTINCT FROM OLD.released_route_identity_sha256) "
        "THEN RAISE EXCEPTION 'commercial publication receipt is immutable'; END IF; "
        "RETURN NEW; END $$"
    ))


def upgrade() -> None:
    saved = _capture_sqlite_triggers()
    with op.batch_alter_table(PLAN) as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("supersedes_plan_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.drop_constraint("uq_model_commercial_plan_candidate", type_="unique")
        batch.create_unique_constraint("uq_model_commercial_plan_revision", ["model_id", "candidate_revision", "revision"])
        batch.create_unique_constraint("uq_model_commercial_plan_successor", ["supersedes_plan_id"])
        batch.create_foreign_key("fk_commercial_plan_predecessor", PLAN, ["supersedes_plan_id"], ["id"], ondelete="RESTRICT")
        batch.create_check_constraint("ck_model_commercial_plan_revision", "(revision = 1 AND supersedes_plan_id IS NULL) OR (revision > 1 AND supersedes_plan_id IS NOT NULL)")
        digest = (
            "request_fingerprint ~ '^[0-9a-f]{64}$'"
            if op.get_bind().dialect.name == "postgresql" else
            "length(request_fingerprint) = 64 AND lower(request_fingerprint) = request_fingerprint AND request_fingerprint NOT GLOB '*[^0-9a-f]*'"
        )
        batch.create_check_constraint("ck_model_commercial_plan_request_sha", digest)
    with op.batch_alter_table(EXECUTION) as batch:
        batch.drop_constraint("ck_model_commercial_execution_state", type_="check")
        batch.create_check_constraint("ck_model_commercial_execution_state", "state IN ('approved', 'blocked', 'released', 'superseded')")
    _restore_sqlite_triggers(saved, repair_evidence=True)
    _create_guards()
    _receipt_immutability_function(repaired=True)


def downgrade() -> None:
    used = op.get_bind().scalar(sa.text(
        f"SELECT count(*) FROM {PLAN} WHERE revision <> 1 OR request_fingerprint IS NOT NULL"
    ))
    if used:
        raise RuntimeError("0055 downgrade blocked by durable commercial approval revisions")
    for name, table in (("trg_commercial_plan_revision_insert", PLAN), ("trg_commercial_execution_superseded", EXECUTION)):
        suffix = f" ON {table}" if op.get_bind().dialect.name == "postgresql" else ""
        op.execute(sa.text(f"DROP TRIGGER {name}{suffix}"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP FUNCTION guard_commercial_plan_revision()"))
        op.execute(sa.text("DROP FUNCTION guard_commercial_execution_superseded()"))
        _receipt_immutability_function(repaired=False)
    saved = _capture_sqlite_triggers()
    with op.batch_alter_table(EXECUTION) as batch:
        batch.drop_constraint("ck_model_commercial_execution_state", type_="check")
        batch.create_check_constraint("ck_model_commercial_execution_state", "state IN ('approved', 'blocked', 'released')")
    with op.batch_alter_table(PLAN) as batch:
        batch.drop_constraint("fk_commercial_plan_predecessor", type_="foreignkey")
        batch.drop_constraint("ck_model_commercial_plan_revision", type_="check")
        batch.drop_constraint("ck_model_commercial_plan_request_sha", type_="check")
        batch.drop_constraint("uq_model_commercial_plan_revision", type_="unique")
        batch.drop_constraint("uq_model_commercial_plan_successor", type_="unique")
        batch.create_unique_constraint("uq_model_commercial_plan_candidate", ["model_id", "candidate_revision"])
        batch.drop_column("request_fingerprint")
        batch.drop_column("supersedes_plan_id")
        batch.drop_column("revision")
    _restore_sqlite_triggers(saved)
