"""Persist immutable task-level Relay backend affinity.

Revision ID: 0033_relay_backend_affinity
Revises: 0032_publisher_oauth

Historical policy is intentionally deterministic: every row created before
this migration belongs to ``legacy-default-v1`` at ``generations.v1``. The
deployment must keep that registry entry available until those tasks no longer
need status, artifact, download, callback, or reconciliation access.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0033_relay_backend_affinity"
down_revision: str | None = "0032_publisher_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_BACKEND_ID = "legacy-default-v1"
LEGACY_CONTRACT_REVISION = "generations.v1"


def _add_affinity_columns(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("relay_backend_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("relay_contract_revision", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            f"UPDATE {table_name} "
            "SET relay_backend_id = :backend_id, "
            "relay_contract_revision = :contract_revision "
            "WHERE relay_backend_id IS NULL OR relay_contract_revision IS NULL"
        ).bindparams(
            backend_id=LEGACY_BACKEND_ID,
            contract_revision=LEGACY_CONTRACT_REVISION,
        )
    )


def upgrade() -> None:
    _add_affinity_columns("generation_tasks")
    _add_affinity_columns("relay_submission_outbox")

    with op.batch_alter_table("generation_tasks") as batch:
        batch.alter_column(
            "relay_backend_id",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default=LEGACY_BACKEND_ID,
        )
        batch.alter_column(
            "relay_contract_revision",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default=LEGACY_CONTRACT_REVISION,
        )
        batch.create_check_constraint(
            "ck_task_relay_backend_id_nonempty",
            "length(relay_backend_id) > 0",
        )
        batch.create_check_constraint(
            "ck_task_relay_contract_revision_nonempty",
            "length(relay_contract_revision) > 0",
        )

    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.alter_column(
            "relay_backend_id",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default=LEGACY_BACKEND_ID,
        )
        batch.alter_column(
            "relay_contract_revision",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default=LEGACY_CONTRACT_REVISION,
        )
        batch.create_check_constraint(
            "ck_relay_outbox_backend_id_nonempty",
            "length(relay_backend_id) > 0",
        )
        batch.create_check_constraint(
            "ck_relay_outbox_contract_revision_nonempty",
            "length(relay_contract_revision) > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.drop_constraint(
            "ck_relay_outbox_contract_revision_nonempty", type_="check"
        )
        batch.drop_constraint("ck_relay_outbox_backend_id_nonempty", type_="check")
        batch.drop_column("relay_contract_revision")
        batch.drop_column("relay_backend_id")

    with op.batch_alter_table("generation_tasks") as batch:
        batch.drop_constraint("ck_task_relay_contract_revision_nonempty", type_="check")
        batch.drop_constraint("ck_task_relay_backend_id_nonempty", type_="check")
        batch.drop_column("relay_contract_revision")
        batch.drop_column("relay_backend_id")
