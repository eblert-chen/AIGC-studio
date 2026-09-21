"""Allow private generation inputs to belong to personal workspaces.

Revision ID: 0052_personal_input_assets
Revises: 0051_provider_account_evidence
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0052_personal_input_assets"
down_revision: str | None = "0051_provider_account_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _capture_and_drop_sqlite_triggers() -> tuple[str, ...]:
    if op.get_bind().dialect.name != "sqlite":
        return ()
    connection = op.get_bind()
    statements = tuple(
        str(sql)
        for sql in connection.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND sql IS NOT NULL ORDER BY name"
            )
        ).scalars()
    )
    names = tuple(
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "ORDER BY name"
            )
        ).scalars()
    )
    for name in names:
        connection.execute(sa.text(f'DROP TRIGGER "{name}"'))
    return statements


def _restore_sqlite_triggers(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(sa.text(statement))


def upgrade() -> None:
    sqlite_triggers = _capture_and_drop_sqlite_triggers()
    with op.batch_alter_table("input_assets") as batch:
        batch.add_column(
            sa.Column(
                "personal_workspace_id",
                sa.String(length=36),
                nullable=True,
            )
        )
        batch.alter_column(
            "company_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch.create_foreign_key(
            "fk_input_assets_personal_workspace",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_personal_input_asset_uploader_idempotency",
            [
                "personal_workspace_id",
                "uploaded_by_user_id",
                "idempotency_key",
            ],
        )
        batch.create_index(
            "ix_input_assets_personal_workspace_id",
            ["personal_workspace_id"],
            unique=False,
        )
        batch.create_index(
            "ix_input_asset_personal_status_created",
            ["personal_workspace_id", "status", "created_at"],
            unique=False,
        )
        batch.create_check_constraint(
            "ck_input_asset_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
        )
    _restore_sqlite_triggers(sqlite_triggers)


def downgrade() -> None:
    personal_count = int(
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM input_assets "
                "WHERE personal_workspace_id IS NOT NULL"
            )
        )
        or 0
    )
    if personal_count:
        raise RuntimeError(
            "Cannot downgrade while personal input assets exist"
        )
    sqlite_triggers = _capture_and_drop_sqlite_triggers()
    with op.batch_alter_table("input_assets") as batch:
        batch.drop_constraint("ck_input_asset_scope", type_="check")
        batch.drop_index("ix_input_asset_personal_status_created")
        batch.drop_index("ix_input_assets_personal_workspace_id")
        batch.drop_constraint(
            "uq_personal_input_asset_uploader_idempotency",
            type_="unique",
        )
        batch.drop_constraint(
            "fk_input_assets_personal_workspace",
            type_="foreignkey",
        )
        batch.alter_column(
            "company_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch.drop_column("personal_workspace_id")
    _restore_sqlite_triggers(sqlite_triggers)
