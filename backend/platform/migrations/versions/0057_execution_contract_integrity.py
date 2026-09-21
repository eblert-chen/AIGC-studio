"""Freeze admitted quote evidence and private execution contracts.

Revision ID: 0057_execution_integrity
Revises: 0056_billing_integrity_guards

No historical quote, contract, or qualification evidence is rewritten.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# Alembic's version_num is VARCHAR(32); retain a bounded revision identifier.
revision: str = "0057_execution_integrity"
down_revision: str | None = "0056_billing_integrity_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sqlite_json_changed(column: str, path: str | None = None) -> str:
    # json() preserves object key order; raw text comparison would reject a
    # harmless serialization change. Compare typed JSON trees instead. Array
    # positions, explicit null vs missing, booleans and duplicate multiplicity
    # remain distinct. Container values themselves contain irrelevant raw JSON.
    argument = f", '{path}'" if path is not None else ""
    def rows(prefix: str) -> str:
        return ("SELECT fullkey, type, atom, count(*) FROM "
                f"json_tree({prefix}.{column}{argument}) "
                "GROUP BY fullkey, type, atom")
    old, new = rows("OLD"), rows("NEW")
    return f"EXISTS ({old} EXCEPT {new}) OR EXISTS ({new} EXCEPT {old})"


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text(
            "CREATE FUNCTION reject_execution_fact_removal() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'execution contract facts are durable'; END $$"
        ))
    guards = (
        ("generation_tasks", "pricing_snapshot", None,
         "trg_task_pricing_snapshot_immutable", "task pricing snapshot is immutable"),
        ("relay_submission_outbox", "relay_payload", "$.execution_contract",
         "trg_outbox_execution_contract_immutable", "outbox execution contract is immutable"),
    )
    for table, column, path, trigger, message in guards:
        if dialect == "sqlite":
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE OF {column} ON {table} "
                f"WHEN {_sqlite_json_changed(column, path)} "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            ))
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger}_delete BEFORE DELETE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'execution contract facts are durable'); END"
            ))
        elif dialect == "postgresql":
            projection = f"{column}::jsonb"
            if path is not None:
                projection += " -> 'execution_contract'"
            function = f"guard_{trigger.removeprefix('trg_')}"
            op.execute(sa.text(
                f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN IF (NEW.{projection}) IS DISTINCT FROM (OLD.{projection}) "
                f"THEN RAISE EXCEPTION '{message}'; END IF; RETURN NEW; END $$"
            ))
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE OF {column} ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            ))
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger}_delete BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_execution_fact_removal()"
            ))
            # API/worker ACLs already deny TRUNCATE; a statement trigger also
            # prevents an accidental owner-level truncate from erasing proof.
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger}_truncate BEFORE TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_fact_removal()"
            ))
        else:
            raise RuntimeError("execution contract integrity requires SQLite or PostgreSQL")


def downgrade() -> None:
    raise RuntimeError(
        "0057 downgrade is blocked: admitted quote and execution evidence must remain immutable"
    )
