"""Permit sealed Director Shot Package schema v2 records.

Revision ID: 0059_director_shot_schema_v2
Revises: 0058_personal_limits

Revision 0053 deliberately remains frozen.  This forward migration widens only
the immutable package schema discriminator; it does not relax scope, digest,
asset, or task-binding constraints.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0059_director_shot_schema_v2"
down_revision: str | None = "0058_personal_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "director_shot_packages"
_CONSTRAINT = "ck_director_shot_package_schema_version"


def _capture_sqlite_triggers() -> tuple[str, ...]:
    return tuple(
        str(sql)
        for sql in op.get_bind()
        .execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name=:table_name AND sql IS NOT NULL ORDER BY name"
            ),
            {"table_name": _TABLE},
        )
        .scalars()
    )


def _drop_sqlite_triggers() -> None:
    names = tuple(
        str(name)
        for name in op.get_bind()
        .execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name=:table_name ORDER BY name"
            ),
            {"table_name": _TABLE},
        )
        .scalars()
    )
    for name in names:
        op.execute(sa.text(f'DROP TRIGGER "{name}"'))


def _restore_sqlite_triggers(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(sa.text(statement))


def _replace_schema_constraint(expression: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        # Alembic recreates the table to alter a SQLite CHECK. Preserve the
        # immutable UPDATE/DELETE guards which SQLite otherwise drops with the
        # old table.
        triggers = _capture_sqlite_triggers()
        _drop_sqlite_triggers()
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_CONSTRAINT, type_="check")
            batch.create_check_constraint(_CONSTRAINT, expression)
        _restore_sqlite_triggers(triggers)
        return
    if dialect == "postgresql":
        op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
        op.create_check_constraint(_CONSTRAINT, _TABLE, expression)
        return
    raise RuntimeError("0059 supports only SQLite and PostgreSQL")


def upgrade() -> None:
    _replace_schema_constraint("schema_version IN (1, 2)")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # The lock must precede the data check. Otherwise a concurrent v2 seal
        # could commit between the check and replacement constraint and either
        # be silently invalidated or race the downgrade.
        connection.execute(
            sa.text(
                "LOCK TABLE director_shot_packages IN ACCESS EXCLUSIVE MODE"
            )
        )
    has_v2 = connection.scalar(
        sa.text(
            "SELECT 1 FROM director_shot_packages "
            "WHERE schema_version = 2 LIMIT 1"
        )
    )
    if has_v2 is not None:
        raise RuntimeError(
            "0059 downgrade is blocked while director shot schema v2 packages exist"
        )
    _replace_schema_constraint("schema_version = 1")
