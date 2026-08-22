"""Add isolated TOC personal workspaces, points billing, and task ownership.

Revision ID: 0034_personal_workspaces
Revises: 0033_relay_backend_affinity

Personal workspaces are first-class tenants.  They are not synthetic companies,
and their points wallet, retail prices, task idempotency, ledger, artifacts, and
Relay ownership are structurally separate from company money and reporting.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0034_personal_workspaces"
down_revision: str | None = "0033_relay_backend_affinity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_personal_ledger_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_personal_ledger_entry_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'personal ledger entries are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_personal_ledger_entries_immutable
            BEFORE UPDATE OR DELETE ON personal_ledger_entries
            FOR EACH ROW EXECUTE FUNCTION reject_personal_ledger_entry_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_personal_ledger_entries_no_truncate
            BEFORE TRUNCATE ON personal_ledger_entries
            FOR EACH STATEMENT EXECUTE FUNCTION reject_personal_ledger_entry_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_personal_ledger_entries_no_update
            BEFORE UPDATE ON personal_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'personal ledger entries are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_personal_ledger_entries_no_delete
            BEFORE DELETE ON personal_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'personal ledger entries are immutable');
            END
            """
        )


def _drop_personal_ledger_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_personal_ledger_entries_no_truncate "
            "ON personal_ledger_entries"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_personal_ledger_entries_immutable "
            "ON personal_ledger_entries"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_personal_ledger_entry_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_personal_ledger_entries_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_personal_ledger_entries_no_delete")


def _create_personal_download_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_personal_download_record_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'personal download records are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_personal_download_records_immutable
            BEFORE UPDATE OR DELETE ON personal_download_records
            FOR EACH ROW EXECUTE FUNCTION reject_personal_download_record_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_personal_download_records_no_truncate
            BEFORE TRUNCATE ON personal_download_records
            FOR EACH STATEMENT EXECUTE FUNCTION reject_personal_download_record_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_personal_download_records_no_update "
            "BEFORE UPDATE ON personal_download_records BEGIN "
            "SELECT RAISE(ABORT, 'personal download records are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_personal_download_records_no_delete "
            "BEFORE DELETE ON personal_download_records BEGIN "
            "SELECT RAISE(ABORT, 'personal download records are immutable'); END"
        )


def _drop_personal_download_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_personal_download_records_no_truncate "
            "ON personal_download_records"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_personal_download_records_immutable "
            "ON personal_download_records"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS reject_personal_download_record_mutation()"
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_personal_download_records_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_personal_download_records_no_delete")


def _restore_sqlite_task_artifact_guards() -> None:
    """Restore guards dropped by SQLite batch table reconstruction.

    Alembic implements ``batch_alter_table`` on SQLite by copying into a new
    table. SQLite does not carry triggers to that replacement table, so every
    migration that reconstructs ``task_artifacts`` must recreate the guards
    introduced by revisions 0016 and 0022 before the migration commits.
    """

    if op.get_bind().dialect.name != "sqlite":
        return
    statements = (
        "DROP TRIGGER IF EXISTS trg_task_artifacts_no_update",
        "DROP TRIGGER IF EXISTS trg_task_artifacts_no_delete",
        "DROP TRIGGER IF EXISTS trg_task_artifacts_size_positive_insert",
        "CREATE TRIGGER trg_task_artifacts_no_update "
        "BEFORE UPDATE ON task_artifacts BEGIN "
        "SELECT RAISE(ABORT, 'artifact and download audit records are immutable'); END",
        "CREATE TRIGGER trg_task_artifacts_no_delete "
        "BEFORE DELETE ON task_artifacts BEGIN "
        "SELECT RAISE(ABORT, 'artifact and download audit records are immutable'); END",
        "CREATE TRIGGER trg_task_artifacts_size_positive_insert "
        "BEFORE INSERT ON task_artifacts WHEN NEW.size_bytes <= 0 "
        "BEGIN SELECT RAISE(ABORT, 'task artifact size must be positive'); END",
    )
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    op.create_table(
        "personal_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_personal_workspace_user"),
    )
    op.create_index(
        "ix_personal_workspaces_user_id",
        "personal_workspaces",
        ["user_id"],
        unique=True,
    )
    op.create_table(
        "personal_wallet_accounts",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("available_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_points", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "available_points >= 0", name="ck_personal_wallet_available_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_points >= 0", name="ck_personal_wallet_reserved_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["personal_workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "personal_retail_model_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("price_per_second_points", sa.BigInteger(), nullable=True),
        sa.Column("price_per_item_points", sa.BigInteger(), nullable=True),
        sa.Column("config_override", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "price_per_second_points IS NULL OR price_per_second_points > 0",
            name="ck_personal_retail_second_price_positive",
        ),
        sa.CheckConstraint(
            "price_per_item_points IS NULL OR price_per_item_points > 0",
            name="ck_personal_retail_item_price_positive",
        ),
        sa.CheckConstraint(
            "(price_per_second_points IS NOT NULL AND price_per_item_points IS NULL) "
            "OR (price_per_second_points IS NULL AND price_per_item_points IS NOT NULL)",
            name="ck_personal_retail_exactly_one_price",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", name="uq_personal_retail_model_grant"),
    )
    op.create_index(
        "ix_personal_retail_model_grants_model_id",
        "personal_retail_model_grants",
        ["model_id"],
    )

    connection = op.get_bind()
    users = list(connection.execute(sa.text("SELECT id FROM users")).scalars())
    now = sa.func.now()
    for user_id in users:
        workspace_id = str(uuid.uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO personal_workspaces "
                "(id, user_id, active, created_at, updated_at) "
                "VALUES (:id, :user_id, :active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": workspace_id, "user_id": user_id, "active": True},
        )
        connection.execute(
            sa.text(
                "INSERT INTO personal_wallet_accounts "
                "(workspace_id, available_points, reserved_points, created_at, updated_at) "
                "VALUES (:workspace_id, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"workspace_id": workspace_id},
        )
    del now

    # Do not use SQLite batch reflection for this legacy table. Revision 0019
    # contains dialect-specific checks whose SQL text is not safely reflected by
    # every SQLAlchemy/SQLite combination; rebuilding the table also drops its
    # append-only triggers. A nullable additive column plus an insert-time FK
    # guard preserves the table and all historical evidence in place.
    if connection.dialect.name == "sqlite":
        op.add_column(
            "channel_cost_entries",
            sa.Column("personal_workspace_id", sa.String(length=36), nullable=True),
        )
        op.create_index(
            "ix_channel_cost_personal_occurred",
            "channel_cost_entries",
            ["personal_workspace_id", "occurred_at"],
        )
        op.execute(
            "CREATE TRIGGER trg_channel_cost_personal_workspace_fk "
            "BEFORE INSERT ON channel_cost_entries "
            "WHEN NEW.personal_workspace_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM personal_workspaces "
            "WHERE id = NEW.personal_workspace_id) BEGIN "
            "SELECT RAISE(ABORT, 'invalid personal workspace'); END"
        )
    else:
        op.add_column(
            "channel_cost_entries",
            sa.Column("personal_workspace_id", sa.String(length=36), nullable=True),
        )
        op.create_foreign_key(
            "fk_channel_cost_personal_workspace",
            "channel_cost_entries",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            "ix_channel_cost_personal_occurred",
            "channel_cost_entries",
            ["personal_workspace_id", "occurred_at"],
        )

    with op.batch_alter_table("generation_tasks") as batch:
        batch.add_column(sa.Column("personal_workspace_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("quote_points", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column(
                "reserved_points",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(sa.Column("actual_cost_points", sa.BigInteger(), nullable=True))
        batch.create_foreign_key(
            "fk_generation_task_personal_workspace",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column(
            "company_id", existing_type=sa.String(length=36), nullable=True
        )
        batch.alter_column(
            "quote_cents", existing_type=sa.BigInteger(), nullable=True
        )
        batch.drop_constraint("ck_task_quote_positive", type_="check")
        batch.create_check_constraint(
            "ck_task_scope_quote",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND quote_cents > 0 AND quote_points IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND quote_cents IS NULL AND quote_points > 0)",
        )
        batch.create_check_constraint(
            "ck_task_points_reserved_nonnegative", "reserved_points >= 0"
        )
        batch.create_check_constraint(
            "ck_task_actual_points_nonnegative",
            "actual_cost_points IS NULL OR actual_cost_points >= 0",
        )
        batch.create_unique_constraint(
            "uq_task_personal_idempotency",
            ["personal_workspace_id", "idempotency_key"],
        )
        batch.create_index(
            "ix_generation_task_personal_created",
            ["personal_workspace_id", "created_at"],
        )

    with op.batch_alter_table("task_artifacts") as batch:
        batch.add_column(sa.Column("personal_workspace_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_task_artifact_personal_workspace",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column(
            "company_id", existing_type=sa.String(length=36), nullable=True
        )
        batch.create_check_constraint(
            "ck_task_artifact_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
        )
        batch.create_index(
            "ix_task_artifact_personal_created",
            ["personal_workspace_id", "created_at"],
        )
    _restore_sqlite_task_artifact_guards()

    op.create_table(
        "personal_download_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=160), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_seconds", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("storage_provider", sa.String(length=32), nullable=False),
        sa.Column("storage_endpoint_host", sa.String(length=253), nullable=False),
        sa.Column("storage_bucket", sa.String(length=63), nullable=False),
        sa.Column("storage_object_key", sa.String(length=1024), nullable=False),
        sa.Column("storage_version_id", sa.String(length=256), nullable=True),
        sa.Column("source_url_sha256", sa.String(length=64), nullable=False),
        sa.Column("relay_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("relay_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_seconds > 0", name="ck_personal_download_expiry_positive"
        ),
        sa.CheckConstraint(
            "storage_provider = 'huawei_obs'",
            name="ck_personal_download_storage_provider",
        ),
        sa.CheckConstraint(
            "length(source_url_sha256) = 64 "
            "AND lower(source_url_sha256) = source_url_sha256",
            name="ck_personal_download_source_url_sha_shape",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["personal_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_personal_download_records_workspace_id",
        "personal_download_records",
        ["workspace_id"],
    )
    op.create_index(
        "ix_personal_download_records_requested_by_user_id",
        "personal_download_records",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_personal_download_records_request_id",
        "personal_download_records",
        ["request_id"],
    )
    op.create_index(
        "ix_personal_download_workspace_created",
        "personal_download_records",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_personal_download_task_created",
        "personal_download_records",
        ["task_id", "created_at"],
    )
    _create_personal_download_guards()

    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.add_column(sa.Column("personal_workspace_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_relay_outbox_personal_workspace",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column(
            "company_id", existing_type=sa.String(length=36), nullable=True
        )
        batch.create_check_constraint(
            "ck_relay_outbox_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
        )
        batch.create_index(
            "ix_relay_submission_outbox_personal_workspace_id",
            ["personal_workspace_id"],
        )

    with op.batch_alter_table("relay_callback_events") as batch:
        batch.add_column(sa.Column("personal_workspace_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_relay_callback_personal_workspace",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column(
            "company_id", existing_type=sa.String(length=36), nullable=True
        )
        batch.create_check_constraint(
            "ck_relay_callback_event_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
        )
        batch.create_index(
            "ix_relay_callback_event_personal_received",
            ["personal_workspace_id", "received_at"],
        )

    op.create_table(
        "personal_ledger_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount_points", sa.BigInteger(), nullable=False),
        sa.Column("available_delta_points", sa.BigInteger(), nullable=False),
        sa.Column("reserved_delta_points", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_points >= 0", name="ck_personal_ledger_amount_nonnegative"
        ),
        sa.CheckConstraint(
            "kind IN ('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE')",
            name="ck_personal_ledger_kind",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["personal_workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_personal_ledger_idempotency"
        ),
    )
    op.create_index(
        "ix_personal_ledger_entries_workspace_id",
        "personal_ledger_entries",
        ["workspace_id"],
    )
    op.create_index(
        "ix_personal_ledger_entries_task_id",
        "personal_ledger_entries",
        ["task_id"],
    )
    op.create_index(
        "ix_personal_ledger_workspace_created",
        "personal_ledger_entries",
        ["workspace_id", "created_at"],
    )
    _create_personal_ledger_guards()

    with op.batch_alter_table("task_timeout_events") as batch:
        batch.add_column(
            sa.Column("personal_workspace_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "released_points",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column("personal_ledger_entry_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_task_timeout_personal_workspace",
            "personal_workspaces",
            ["personal_workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_task_timeout_personal_ledger",
            "personal_ledger_entries",
            ["personal_ledger_entry_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column(
            "company_id", existing_type=sa.String(length=36), nullable=True
        )
        batch.create_unique_constraint(
            "uq_task_timeout_personal_ledger_entry",
            ["personal_ledger_entry_id"],
        )
        batch.create_check_constraint(
            "ck_task_timeout_points_nonnegative", "released_points >= 0"
        )
        batch.create_check_constraint(
            "ck_task_timeout_scope",
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND released_points = 0 AND personal_ledger_entry_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND released_cents = 0 AND ledger_entry_id IS NULL)",
        )
        batch.create_index(
            "ix_task_timeout_event_personal_created",
            ["personal_workspace_id", "created_at"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    personal_tasks = int(
        connection.scalar(
            sa.text(
                "SELECT count(*) FROM generation_tasks "
                "WHERE personal_workspace_id IS NOT NULL"
            )
        )
        or 0
    )
    if personal_tasks:
        raise RuntimeError("cannot downgrade while personal generation tasks exist")

    with op.batch_alter_table("task_timeout_events") as batch:
        batch.drop_index("ix_task_timeout_event_personal_created")
        batch.drop_constraint("ck_task_timeout_scope", type_="check")
        batch.drop_constraint("ck_task_timeout_points_nonnegative", type_="check")
        batch.drop_constraint(
            "uq_task_timeout_personal_ledger_entry", type_="unique"
        )
        batch.drop_constraint(
            "fk_task_timeout_personal_ledger", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_task_timeout_personal_workspace", type_="foreignkey"
        )
        batch.alter_column(
            "company_id", existing_type=sa.String(length=36), nullable=False
        )
        batch.drop_column("personal_ledger_entry_id")
        batch.drop_column("released_points")
        batch.drop_column("personal_workspace_id")

    _drop_personal_ledger_guards()
    op.drop_table("personal_ledger_entries")

    with op.batch_alter_table("relay_callback_events") as batch:
        batch.drop_index("ix_relay_callback_event_personal_received")
        batch.drop_constraint("ck_relay_callback_event_scope", type_="check")
        batch.drop_constraint("fk_relay_callback_personal_workspace", type_="foreignkey")
        batch.alter_column("company_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_column("personal_workspace_id")

    with op.batch_alter_table("relay_submission_outbox") as batch:
        batch.drop_index("ix_relay_submission_outbox_personal_workspace_id")
        batch.drop_constraint("ck_relay_outbox_scope", type_="check")
        batch.drop_constraint("fk_relay_outbox_personal_workspace", type_="foreignkey")
        batch.alter_column("company_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_column("personal_workspace_id")

    with op.batch_alter_table("task_artifacts") as batch:
        batch.drop_index("ix_task_artifact_personal_created")
        batch.drop_constraint("ck_task_artifact_scope", type_="check")
        batch.drop_constraint("fk_task_artifact_personal_workspace", type_="foreignkey")
        batch.alter_column("company_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_column("personal_workspace_id")
    _restore_sqlite_task_artifact_guards()

    _drop_personal_download_guards()
    op.drop_table("personal_download_records")

    with op.batch_alter_table("generation_tasks") as batch:
        batch.drop_index("ix_generation_task_personal_created")
        batch.drop_constraint("uq_task_personal_idempotency", type_="unique")
        batch.drop_constraint("ck_task_actual_points_nonnegative", type_="check")
        batch.drop_constraint("ck_task_points_reserved_nonnegative", type_="check")
        batch.drop_constraint("ck_task_scope_quote", type_="check")
        batch.drop_constraint("fk_generation_task_personal_workspace", type_="foreignkey")
        batch.alter_column("company_id", existing_type=sa.String(length=36), nullable=False)
        batch.alter_column("quote_cents", existing_type=sa.BigInteger(), nullable=False)
        batch.create_check_constraint("ck_task_quote_positive", "quote_cents > 0")
        batch.drop_column("actual_cost_points")
        batch.drop_column("reserved_points")
        batch.drop_column("quote_points")
        batch.drop_column("personal_workspace_id")

    if connection.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_personal_workspace_fk")
        op.drop_index(
            "ix_channel_cost_personal_occurred",
            table_name="channel_cost_entries",
        )
        op.drop_column("channel_cost_entries", "personal_workspace_id")
    else:
        op.drop_index(
            "ix_channel_cost_personal_occurred",
            table_name="channel_cost_entries",
        )
        op.drop_constraint(
            "fk_channel_cost_personal_workspace",
            "channel_cost_entries",
            type_="foreignkey",
        )
        op.drop_column("channel_cost_entries", "personal_workspace_id")

    op.drop_index(
        "ix_personal_retail_model_grants_model_id",
        table_name="personal_retail_model_grants",
    )
    op.drop_table("personal_retail_model_grants")
    op.drop_table("personal_wallet_accounts")
    op.drop_index("ix_personal_workspaces_user_id", table_name="personal_workspaces")
    op.drop_table("personal_workspaces")
