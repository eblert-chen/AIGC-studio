"""Open one transition in the released commercial execution guard.

Revision ID: 0061_released_exec_block
Revises: 0060_commercial_release_batches

0056 made a released execution absolutely immutable: any change of ``state`` or
``released_at`` raised. That is correct for the publication record itself, but
it also blocks the one transition that *stops customer service*: reconciling a
released model whose live Relay catalog has drifted cannot mark the execution
blocked, so the write raises ProgrammingError and aborts the whole pass -
stranding every other plan behind a single stale model.

Drift on a released model must stop service: the test
``test_worker_blocks_released_route_drift_and_never_auto_reactivates`` asserts
the execution turns blocked while keeping its publication receipt, the model
goes inactive, and every company and personal grant is disabled. That is a
safety behaviour, not a convenience.

This revision therefore permits exactly ``released -> blocked`` and nothing
else. The publication timestamp stays untouchable, so the released decision
remains auditable history instead of being silently rewritten.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0061_released_exec_block"
down_revision: str | None = "0060_commercial_release_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Only a released row is constrained, and exactly one transition is allowed:
# released -> blocked stops customer service. Everything else on a released row
# - any other state change, and any timestamp edit on a row that stays released
# - still raises. released_at is cleared as part of the block because
# ck_model_commercial_execution_terminal requires it to be NULL for every
# non-released state; the publication receipt (the actual released decision) is
# left untouched by ModelCommercialReleaseService._block.
_POSTGRES_GUARD = (
    "CREATE OR REPLACE FUNCTION guard_commercial_execution_released() "
    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
    "IF OLD.state = 'released' THEN "
    "IF NEW.state = 'blocked' THEN RETURN NEW; END IF; "
    "IF NEW.state IS DISTINCT FROM OLD.state "
    "OR NEW.released_at IS DISTINCT FROM OLD.released_at THEN "
    "RAISE EXCEPTION 'released commercial execution is immutable'; "
    "END IF; "
    "END IF; "
    "RETURN NEW; END $$"
)

# 0056 shape, restored verbatim for downgrade.
_POSTGRES_GUARD_LEGACY = (
    "CREATE OR REPLACE FUNCTION guard_commercial_execution_released() "
    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.state='released' AND "
    "(NEW.state IS DISTINCT FROM OLD.state OR NEW.released_at IS DISTINCT FROM "
    "OLD.released_at) THEN RAISE EXCEPTION "
    "'released commercial execution is immutable'; END IF; RETURN NEW; END $$"
)

_SQLITE_GUARD = (
    "CREATE TRIGGER trg_commercial_execution_released BEFORE UPDATE ON "
    "model_commercial_release_executions "
    "WHEN OLD.state='released' AND NEW.state<>'blocked' AND "
    "(NEW.state IS NOT OLD.state OR NEW.released_at IS NOT OLD.released_at) "
    "BEGIN SELECT RAISE(ABORT, 'released commercial execution is immutable'); END"
)

_SQLITE_GUARD_LEGACY = (
    "CREATE TRIGGER trg_commercial_execution_released BEFORE UPDATE ON "
    "model_commercial_release_executions "
    "WHEN OLD.state='released' AND (NEW.state IS NOT OLD.state OR "
    "NEW.released_at IS NOT OLD.released_at) "
    "BEGIN SELECT RAISE(ABORT, 'released commercial execution is immutable'); END"
)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text(_POSTGRES_GUARD))
    elif dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER trg_commercial_execution_released"))
        op.execute(sa.text(_SQLITE_GUARD))


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text(_POSTGRES_GUARD_LEGACY))
    elif dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER trg_commercial_execution_released"))
        op.execute(sa.text(_SQLITE_GUARD_LEGACY))
