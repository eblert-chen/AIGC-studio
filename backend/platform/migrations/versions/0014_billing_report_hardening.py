"""Harden global billing reports and PostgreSQL ledger immutability.

Revision ID: 0014_billing_report_hardening
Revises: 0013_billing_invariants
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0014_billing_report_hardening"
down_revision: str | None = "0013_billing_invariants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ledger_kind_created",
        "ledger_entries",
        ["kind", "created_at", "id"],
        unique=False,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE TRIGGER trg_ledger_entries_no_truncate
            BEFORE TRUNCATE ON ledger_entries
            FOR EACH STATEMENT EXECUTE FUNCTION reject_ledger_entry_mutation()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_ledger_entries_no_truncate "
            "ON ledger_entries"
        )
    op.drop_index("ix_ledger_kind_created", table_name="ledger_entries")
