"""Add the commercial release batch journal and plan binding.

Revision ID: 0060_commercial_release_batches
Revises: 0059_director_shot_schema_v2

Adds ``model_commercial_release_batches`` — an all-or-none journal that groups
several immutable commercial release plans so one activation transaction can
release them together — plus a nullable ``batch_id`` binding on the immutable
plan table.

``batch_id`` is nullable and only ever written at plan INSERT time, because
``model_commercial_release_plans`` rejects UPDATE and DELETE.  Every plan
created before this revision therefore keeps working untouched.
"""
from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v25 as policy_v25


revision: str = "0060_commercial_release_batches"
down_revision: str | None = "0059_director_shot_schema_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_TABLES = ("model_commercial_release_batches",)
_PLAN_BATCH_FK = "fk_model_commercial_plan_batch"
_PLAN_BATCH_INDEX = "ix_model_commercial_plan_batch"


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
        for process, role in policy_v25.DATABASE_ROLE_BY_PROCESS.items()
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
    for process, privileges_by_table in policy_v25.PRIVILEGES_BY_PROCESS.items():
        role = _quote(policy_v25.DATABASE_ROLE_BY_PROCESS[process])
        for table_name in _TABLES:
            privileges = privileges_by_table.get(table_name)
            if privileges:
                op.execute(
                    sa.text(
                        f"GRANT {', '.join(sorted(privileges))} ON TABLE public."
                        f"{_quote(table_name)} TO {role}"
                    )
                )


def upgrade() -> None:
    op.create_table(
        "model_commercial_release_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("model_count", sa.Integer(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("catalog_revision", sa.String(71), nullable=False),
        sa.Column("approved_by_user_id", sa.String(36), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_by_user_id", sa.String(36), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "last_failure_code", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column("last_failure_summary", sa.JSON(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_model_commercial_batch_idempotency",
        ),
        sa.CheckConstraint(
            "state IN ('approved', 'released', 'abandoned')",
            name="ck_model_commercial_batch_state",
        ),
        sa.CheckConstraint(
            "model_count >= 1", name="ck_model_commercial_batch_size"
        ),
        sa.CheckConstraint(
            "length(catalog_revision) > 0",
            name="ck_model_commercial_batch_catalog_revision",
        ),
        sa.CheckConstraint(
            "(state = 'released' AND result_payload IS NOT NULL "
            "AND released_at IS NOT NULL) OR "
            "(state IN ('approved', 'abandoned') AND result_payload IS NULL "
            "AND released_at IS NULL)",
            name="ck_model_commercial_batch_result_shape",
        ),
        sa.CheckConstraint(
            "(state = 'released' AND activated_by_user_id IS NOT NULL) OR "
            "(state IN ('approved', 'abandoned') AND activated_by_user_id IS NULL)",
            name="ck_model_commercial_batch_activator_shape",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_model_commercial_batch_attempts"
        ),
        *_sha256_constraints(
            "request_sha256", "ck_model_commercial_batch_request_sha256"
        ),
    )
    op.create_index(
        "ix_model_commercial_batch_state_created",
        "model_commercial_release_batches",
        ["state", "created_at", "id"],
    )
    op.create_index(
        "ix_model_commercial_batch_actor_created",
        "model_commercial_release_batches",
        ["approved_by_user_id", "created_at"],
    )
    _apply_acl()

    with op.batch_alter_table("model_commercial_release_plans") as batch:
        batch.add_column(sa.Column("batch_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            _PLAN_BATCH_FK,
            "model_commercial_release_batches",
            ["batch_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        _PLAN_BATCH_INDEX,
        "model_commercial_release_plans",
        ["batch_id", "model_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Serialize the safety checks with concurrent plan INSERTs.  A plan
        # could otherwise bind itself to a batch after the count and have that
        # binding silently discarded by the following column drop.
        connection.execute(
            sa.text(
                "LOCK TABLE model_commercial_release_plans, "
                "model_commercial_release_batches IN ACCESS EXCLUSIVE MODE"
            )
        )
    bound_plans = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM model_commercial_release_plans "
                "WHERE batch_id IS NOT NULL"
            )
        )
        or 0
    )
    if bound_plans:
        raise RuntimeError(
            "0060 downgrade is blocked while plans are bound to a release batch"
        )
    batches = int(
        connection.scalar(
            sa.text("SELECT count(*) FROM model_commercial_release_batches")
        )
        or 0
    )
    if batches:
        raise RuntimeError(
            "0060 downgrade is blocked while commercial release batches exist"
        )

    op.drop_index(_PLAN_BATCH_INDEX, table_name="model_commercial_release_plans")
    with op.batch_alter_table("model_commercial_release_plans") as batch:
        batch.drop_constraint(_PLAN_BATCH_FK, type_="foreignkey")
        batch.drop_column("batch_id")

    op.drop_index(
        "ix_model_commercial_batch_actor_created",
        table_name="model_commercial_release_batches",
    )
    op.drop_index(
        "ix_model_commercial_batch_state_created",
        table_name="model_commercial_release_batches",
    )
    op.drop_table("model_commercial_release_batches")
