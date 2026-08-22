"""Add concurrency-safe task request idempotency.

Revision ID: 0005_task_idempotency
Revises: 0004_task_artifacts
Create Date: 2026-07-29
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_task_idempotency"
down_revision: str | None = "0004_task_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fingerprint(model_id: str, request_payload: object) -> str:
    canonical_request = json.dumps(
        {
            "model_id": model_id,
            "request_payload": request_payload,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
        )

    bind = op.get_bind()
    tasks = sa.table(
        "generation_tasks",
        sa.column("id", sa.String(length=36)),
        sa.column("model_id", sa.String(length=36)),
        sa.column("request_payload", sa.JSON()),
        sa.column("idempotency_key", sa.String(length=120)),
        sa.column("request_fingerprint", sa.String(length=64)),
    )
    ledger = sa.table(
        "ledger_entries",
        sa.column("task_id", sa.String(length=36)),
        sa.column("kind", sa.String(length=20)),
        sa.column("idempotency_key", sa.String(length=120)),
    )

    reserve_keys = {
        row.task_id: row.idempotency_key
        for row in bind.execute(
            sa.select(ledger.c.task_id, ledger.c.idempotency_key).where(
                ledger.c.kind == "RESERVE",
                ledger.c.task_id.is_not(None),
            )
        )
    }
    for row in bind.execute(
        sa.select(tasks.c.id, tasks.c.model_id, tasks.c.request_payload)
    ).mappings():
        request_payload = row["request_payload"]
        if isinstance(request_payload, str):
            request_payload = json.loads(request_payload)
        bind.execute(
            tasks.update()
            .where(tasks.c.id == row["id"])
            .values(
                idempotency_key=reserve_keys.get(
                    row["id"], f"legacy-task:{row['id']}"
                ),
                request_fingerprint=_fingerprint(
                    row["model_id"], request_payload or {}
                ),
            )
        )

    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.alter_column(
            "idempotency_key",
            existing_type=sa.String(length=120),
            nullable=False,
        )
        batch_op.alter_column(
            "request_fingerprint",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_task_company_idempotency",
            ["company_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_tasks") as batch_op:
        batch_op.drop_constraint(
            "uq_task_company_idempotency", type_="unique"
        )
        batch_op.drop_column("request_fingerprint")
        batch_op.drop_column("idempotency_key")
