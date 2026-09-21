"""Bind tasks, provider costs, and commercial releases to provider accounts.

Revision ID: 0051_provider_account_evidence
Revises: 0050_model_commercial_release
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0051_provider_account_evidence"
down_revision: str | None = "0050_model_commercial_release"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_IDENTITY_COLUMNS = (
    sa.Column(
        "provider_identity_status",
        sa.String(24),
        nullable=False,
        server_default="legacy_unknown",
    ),
    sa.Column("provider_name", sa.String(160), nullable=True),
    sa.Column("provider_account_id", sa.String(160), nullable=True),
    sa.Column("provider_channel_id", sa.Integer(), nullable=True),
    sa.Column("provider_route_id", sa.BigInteger(), nullable=True),
    sa.Column("provider_key_index", sa.Integer(), nullable=True),
    sa.Column("provider_key_fingerprint", sa.String(64), nullable=True),
    sa.Column("provider_credential_version", sa.String(36), nullable=True),
    sa.Column("routing_release_sha256", sa.String(71), nullable=True),
)


def _identity_check(
    *, schema_column: str, route_column: str, qualifier: str = ""
) -> str:
    def column(name: str) -> str:
        return f"{qualifier}{name}"

    schema = column(schema_column)
    route = column(route_column)
    empty = (
        f"{column('provider_name')} IS NULL AND "
        f"{column('provider_account_id')} IS NULL AND "
        f"{column('provider_channel_id')} IS NULL AND "
        f"{column('provider_route_id')} IS NULL AND "
        f"{column('provider_key_index')} IS NULL AND "
        f"{column('provider_key_fingerprint')} IS NULL AND "
        f"{column('provider_credential_version')} IS NULL AND "
        f"{column('routing_release_sha256')} IS NULL"
    )
    bound = (
        f"{schema} = 2 AND {route} > 0 AND "
        f"length({column('provider_name')}) BETWEEN 1 AND 160 AND "
        f"trim({column('provider_name')}) = {column('provider_name')} AND "
        f"length({column('provider_account_id')}) BETWEEN 1 AND 160 AND "
        f"trim({column('provider_account_id')}) = {column('provider_account_id')} AND "
        f"{column('provider_channel_id')} > 0 AND "
        f"{column('provider_route_id')} = {route} AND "
        f"{column('provider_key_index')} >= 0 AND "
        f"length({column('provider_key_fingerprint')}) = 64 AND "
        f"lower({column('provider_key_fingerprint')}) = "
        f"{column('provider_key_fingerprint')} AND "
        f"{column('provider_key_fingerprint')} NOT GLOB '*[^0-9a-f]*' AND "
        f"length({column('provider_credential_version')}) = 36 AND "
        f"lower({column('provider_credential_version')}) = "
        f"{column('provider_credential_version')} AND "
        f"substr({column('provider_credential_version')}, 9, 1) = '-' AND "
        f"substr({column('provider_credential_version')}, 14, 1) = '-' AND "
        f"substr({column('provider_credential_version')}, 15, 1) GLOB '[1-5]' AND "
        f"substr({column('provider_credential_version')}, 19, 1) = '-' AND "
        f"substr({column('provider_credential_version')}, 20, 1) GLOB '[89ab]' AND "
        f"substr({column('provider_credential_version')}, 24, 1) = '-' AND "
        f"replace({column('provider_credential_version')}, '-', '') "
        "NOT GLOB '*[^0-9a-f]*' AND "
        f"length({column('routing_release_sha256')}) = 71 AND "
        f"substr({column('routing_release_sha256')}, 1, 7) = 'sha256:' AND "
        f"lower(substr({column('routing_release_sha256')}, 8)) = "
        f"substr({column('routing_release_sha256')}, 8) AND "
        f"substr({column('routing_release_sha256')}, 8) "
        "NOT GLOB '*[^0-9a-f]*'"
    )
    return (
        f"{column('provider_identity_status')} IN "
        "('unassigned','bound','legacy_unknown') AND "
        f"(({column('provider_identity_status')} = 'bound' AND ({bound})) OR "
        f"({column('provider_identity_status')} IN "
        f"('unassigned','legacy_unknown') AND ({empty})))"
    )


def _postgres_identity_check(*, schema_column: str, route_column: str) -> str:
    empty = (
        "provider_name IS NULL AND provider_account_id IS NULL AND "
        "provider_channel_id IS NULL AND provider_route_id IS NULL AND "
        "provider_key_index IS NULL AND provider_key_fingerprint IS NULL AND "
        "provider_credential_version IS NULL AND routing_release_sha256 IS NULL"
    )
    bound = (
        f"{schema_column} = 2 AND {route_column} IS NOT NULL AND "
        "provider_name IS NOT NULL AND provider_account_id IS NOT NULL AND "
        "provider_channel_id > 0 AND provider_route_id = " + route_column + " AND "
        "provider_key_index >= 0 AND provider_key_fingerprint ~ '^[0-9a-f]{64}$' AND "
        "provider_credential_version ~ "
        "'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' AND "
        "routing_release_sha256 ~ '^sha256:[0-9a-f]{64}$'"
    )
    return (
        "provider_identity_status IN ('unassigned','bound','legacy_unknown') AND "
        f"((provider_identity_status = 'bound' AND ({bound})) OR "
        f"(provider_identity_status IN ('unassigned','legacy_unknown') AND ({empty})))"
    )


def _capture_sqlite_triggers(table: str) -> tuple[str, ...]:
    return tuple(
        str(sql)
        for sql in op.get_bind().execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name=:table_name AND sql IS NOT NULL ORDER BY name"
            ),
            {"table_name": table},
        ).scalars()
    )


def _drop_sqlite_triggers(table: str) -> None:
    names = tuple(
        str(name)
        for name in op.get_bind().execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name=:table_name ORDER BY name"
            ),
            {"table_name": table},
        ).scalars()
    )
    for name in names:
        op.execute(sa.text(f'DROP TRIGGER "{name}"'))


def _restore_sqlite_triggers(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(sa.text(statement))


def _sqlite_validation_trigger(table: str, check: str) -> None:
    for action in ("INSERT", "UPDATE"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_provider_identity_{action.lower()} "
                f"BEFORE {action} ON {table} WHEN COALESCE(({check}), 0) = 0 "
                "BEGIN SELECT RAISE(ABORT, 'provider account evidence is invalid'); END"
            )
        )


def _add_shared_columns() -> None:
    op.add_column(
        "generation_tasks",
        sa.Column("provider_route_evidence", sa.JSON(), nullable=True),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("provider_route_evidence_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "model_commercial_release_plans",
        sa.Column("approved_route_identity", sa.JSON(), nullable=True),
    )
    op.add_column(
        "model_commercial_release_plans",
        sa.Column("approved_route_identity_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "model_commercial_release_executions",
        sa.Column("released_route_identity_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "model_commercial_release_executions",
        sa.Column("publication_receipt", sa.JSON(), nullable=True),
    )
    op.add_column(
        "model_commercial_release_executions",
        sa.Column("publication_receipt_sha256", sa.String(64), nullable=True),
    )


def _upgrade_sqlite() -> None:
    stage_triggers = _capture_sqlite_triggers("relay_task_stage_events")
    _drop_sqlite_triggers("relay_task_stage_events")
    with op.batch_alter_table(
        "relay_task_stage_events", recreate="always"
    ) as batch:
        batch.drop_constraint("ck_relay_task_stage_schema_v1", type_="check")
        batch.create_check_constraint(
            "ck_relay_task_stage_schema", "schema_version IN (1, 2)"
        )
        for column in _IDENTITY_COLUMNS:
            batch.add_column(column.copy())
    op.execute(
        "UPDATE relay_task_stage_events SET provider_identity_status = "
        "CASE WHEN route_id IS NULL THEN 'unassigned' ELSE 'legacy_unknown' END"
    )
    _restore_sqlite_triggers(stage_triggers)
    _sqlite_validation_trigger(
        "relay_task_stage_events",
        _identity_check(
            schema_column="schema_version",
            route_column="route_id",
            qualifier="NEW.",
        )
        + " AND (NEW.provider_identity_status <> 'unassigned' "
        "OR NEW.route_id IS NULL)",
    )

    cost_triggers = _capture_sqlite_triggers("channel_cost_entries")
    _drop_sqlite_triggers("channel_cost_entries")
    for column in (
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("route_id", sa.BigInteger(), nullable=True),
        *_IDENTITY_COLUMNS,
    ):
        op.add_column("channel_cost_entries", column.copy())
    op.execute(
        "UPDATE channel_cost_entries SET provider_identity_status = "
        "CASE WHEN source = 'PLATFORM_ADMIN' THEN 'unassigned' "
        "ELSE 'legacy_unknown' END"
    )
    _restore_sqlite_triggers(cost_triggers)
    _sqlite_validation_trigger(
        "channel_cost_entries",
        "NEW.schema_version IN (1,2) AND "
        + _identity_check(
            schema_column="schema_version",
            route_column="route_id",
            qualifier="NEW.",
        ),
    )

    digest = (
        "length({column}) = 64 AND lower({column}) = {column} "
        "AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    for action in ("INSERT", "UPDATE"):
        op.execute(
            sa.text(
            f"CREATE TRIGGER trg_generation_task_provider_route_validate_{action.lower()} "
            f"BEFORE {action} ON generation_tasks WHEN COALESCE(("
            "(NEW.provider_route_evidence IS NULL AND "
            "NEW.provider_route_evidence_sha256 IS NULL) OR "
            "(NEW.provider_route_evidence IS NOT NULL AND "
            "NEW.provider_route_evidence_sha256 IS NOT NULL AND "
            + digest.format(column="NEW.provider_route_evidence_sha256")
            + ")), 0) = 0 BEGIN SELECT RAISE(ABORT, "
            "'task provider route evidence is invalid'); END"
            )
        )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_generation_task_provider_route_immutable "
            "BEFORE UPDATE ON generation_tasks "
            "WHEN OLD.provider_route_evidence_sha256 IS NOT NULL AND ("
            "NEW.provider_route_evidence_sha256 IS NULL OR "
            "NEW.provider_route_evidence_sha256 <> OLD.provider_route_evidence_sha256 OR "
            "NEW.provider_route_evidence <> OLD.provider_route_evidence) "
            "BEGIN SELECT RAISE(ABORT, 'task provider route evidence is immutable'); END"
        )
    )


def _upgrade_postgresql() -> None:
    op.execute(
        "DROP TRIGGER trg_relay_task_stage_events_immutable "
        "ON relay_task_stage_events"
    )
    op.drop_constraint(
        "ck_relay_task_stage_schema_v1",
        "relay_task_stage_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_relay_task_stage_schema",
        "relay_task_stage_events",
        "schema_version IN (1, 2)",
    )
    for column in _IDENTITY_COLUMNS:
        op.add_column("relay_task_stage_events", column.copy())
    op.execute(
        "UPDATE relay_task_stage_events SET provider_identity_status = "
        "CASE WHEN route_id IS NULL THEN 'unassigned' ELSE 'legacy_unknown' END"
    )
    op.alter_column(
        "relay_task_stage_events", "provider_identity_status", nullable=False
    )
    op.create_check_constraint(
        "ck_relay_task_stage_provider_identity",
        "relay_task_stage_events",
        _postgres_identity_check(
            schema_column="schema_version", route_column="route_id"
        ) + " AND (provider_identity_status <> 'unassigned' OR route_id IS NULL)",
    )
    op.execute(
        "CREATE TRIGGER trg_relay_task_stage_events_immutable BEFORE UPDATE OR DELETE "
        "ON relay_task_stage_events FOR EACH ROW EXECUTE FUNCTION "
        "reject_relay_telemetry_mutation()"
    )

    op.execute(
        "DROP TRIGGER trg_channel_cost_entries_immutable ON channel_cost_entries"
    )
    op.add_column(
        "channel_cost_entries",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "channel_cost_entries", sa.Column("route_id", sa.BigInteger(), nullable=True)
    )
    for column in _IDENTITY_COLUMNS:
        op.add_column("channel_cost_entries", column.copy())
    op.execute(
        "UPDATE channel_cost_entries SET provider_identity_status = "
        "CASE WHEN source = 'PLATFORM_ADMIN' THEN 'unassigned' "
        "ELSE 'legacy_unknown' END"
    )
    op.alter_column(
        "channel_cost_entries", "provider_identity_status", nullable=False
    )
    op.create_check_constraint(
        "ck_channel_cost_provider_identity",
        "channel_cost_entries",
        "schema_version IN (1,2) AND "
        + _postgres_identity_check(
            schema_column="schema_version", route_column="route_id"
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_channel_cost_entries_immutable BEFORE UPDATE OR DELETE "
        "ON channel_cost_entries FOR EACH ROW EXECUTE FUNCTION "
        "reject_channel_cost_entry_mutation()"
    )

    op.create_check_constraint(
        "ck_task_provider_route_evidence_complete",
        "generation_tasks",
        "(provider_route_evidence IS NULL AND provider_route_evidence_sha256 IS NULL) OR "
        "(provider_route_evidence IS NOT NULL AND "
        "provider_route_evidence_sha256 ~ '^[0-9a-f]{64}$')",
    )
    op.execute(
        "CREATE FUNCTION reject_task_provider_route_evidence_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF OLD.provider_route_evidence_sha256 IS NOT NULL AND ("
        "NEW.provider_route_evidence_sha256 IS DISTINCT FROM OLD.provider_route_evidence_sha256 "
        "OR NEW.provider_route_evidence IS DISTINCT FROM OLD.provider_route_evidence) "
        "THEN RAISE EXCEPTION 'task provider route evidence is immutable'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_generation_task_provider_route_immutable "
        "BEFORE UPDATE ON generation_tasks FOR EACH ROW EXECUTE FUNCTION "
        "reject_task_provider_route_evidence_mutation()"
    )


def _sha256_hex_check(*, column: str, dialect: str) -> str:
    if dialect == "sqlite":
        return (
            f"length({column}) = 64 AND lower({column}) = {column} "
            f"AND {column} NOT GLOB '*[^0-9a-f]*'"
        )
    if dialect == "postgresql":
        return f"{column} ~ '^[0-9a-f]{{64}}$'"
    raise RuntimeError("0051 shared validation supports only SQLite and PostgreSQL")


def _create_shared_validation() -> None:
    dialect = op.get_bind().dialect.name

    plan_check = (
        "(approved_route_identity IS NULL AND approved_route_identity_sha256 IS NULL) OR "
        "(approved_route_identity IS NOT NULL AND approved_route_identity_sha256 IS NOT NULL AND "
        + _sha256_hex_check(
            column="approved_route_identity_sha256", dialect=dialect
        )
        + ")"
    )
    receipt_check = (
        "(publication_receipt IS NULL AND publication_receipt_sha256 IS NULL AND "
        "released_route_identity_sha256 IS NULL) OR "
        "(publication_receipt IS NOT NULL AND publication_receipt_sha256 IS NOT NULL AND "
        "released_route_identity_sha256 IS NOT NULL AND "
        + _sha256_hex_check(column="publication_receipt_sha256", dialect=dialect)
        + " AND "
        + _sha256_hex_check(
            column="released_route_identity_sha256", dialect=dialect
        )
        + ")"
    )
    if dialect == "postgresql":
        op.create_check_constraint(
            "ck_model_commercial_plan_route_identity",
            "model_commercial_release_plans",
            plan_check,
        )
        op.create_check_constraint(
            "ck_model_commercial_execution_receipt",
            "model_commercial_release_executions",
            receipt_check,
        )
    else:
        for table, name, check in (
            (
                "model_commercial_release_plans",
                "trg_model_commercial_plan_route_identity_insert",
                plan_check,
            ),
            (
                "model_commercial_release_executions",
                "trg_model_commercial_execution_receipt_insert",
                receipt_check,
            ),
        ):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER {name} BEFORE INSERT ON {table} "
                    f"WHEN COALESCE(({check}), 0) = 0 BEGIN SELECT RAISE(ABORT, "
                    "'commercial provider evidence is invalid'); END"
                )
            )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_model_commercial_execution_receipt_update "
                "BEFORE UPDATE ON model_commercial_release_executions "
                f"WHEN COALESCE(({receipt_check}), 0) = 0 BEGIN SELECT RAISE(ABORT, "
                "'commercial provider evidence is invalid'); END"
            )
        )
    # Once materialized, the execution receipt is append-only even though the
    # surrounding retry state machine remains mutable.
    if dialect == "sqlite":
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_model_commercial_execution_receipt_immutable "
                "BEFORE UPDATE ON model_commercial_release_executions "
                "WHEN OLD.publication_receipt_sha256 IS NOT NULL AND ("
                "NEW.publication_receipt_sha256 IS NULL OR "
                "NEW.publication_receipt_sha256 <> OLD.publication_receipt_sha256 OR "
                "NEW.publication_receipt <> OLD.publication_receipt OR "
                "NEW.released_route_identity_sha256 <> OLD.released_route_identity_sha256) "
                "BEGIN SELECT RAISE(ABORT, 'commercial publication receipt is immutable'); END"
            )
        )
    else:
        op.execute(
            "CREATE FUNCTION reject_commercial_receipt_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN IF OLD.publication_receipt_sha256 IS NOT NULL "
            "AND (NEW.publication_receipt_sha256 IS DISTINCT FROM OLD.publication_receipt_sha256 "
            "OR NEW.publication_receipt IS DISTINCT FROM OLD.publication_receipt "
            "OR NEW.released_route_identity_sha256 IS DISTINCT FROM OLD.released_route_identity_sha256) "
            "THEN RAISE EXCEPTION 'commercial publication receipt is immutable'; END IF; "
            "RETURN NEW; END $$"
        )
        op.execute(
            "CREATE TRIGGER trg_model_commercial_execution_receipt_immutable BEFORE UPDATE "
            "ON model_commercial_release_executions FOR EACH ROW EXECUTE FUNCTION "
            "reject_commercial_receipt_mutation()"
        )


def upgrade() -> None:
    _add_shared_columns()
    if op.get_bind().dialect.name == "sqlite":
        _upgrade_sqlite()
    elif op.get_bind().dialect.name == "postgresql":
        _upgrade_postgresql()
    else:
        raise RuntimeError("0051 supports only SQLite and PostgreSQL")
    _create_shared_validation()
    op.create_index(
        "ix_relay_task_stage_provider_account",
        "relay_task_stage_events",
        ["provider_name", "provider_account_id", "occurred_at"],
    )
    op.create_index(
        "ix_channel_cost_provider_account",
        "channel_cost_entries",
        ["provider_name", "provider_account_id", "occurred_at"],
    )


def downgrade() -> None:
    protected_counts = (
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM relay_task_stage_events "
                "WHERE schema_version = 2 OR provider_identity_status = 'bound'"
            )
        ),
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM channel_cost_entries "
                "WHERE schema_version = 2 OR provider_identity_status = 'bound'"
            )
        ),
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM generation_tasks "
                "WHERE provider_route_evidence_sha256 IS NOT NULL"
            )
        ),
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM model_commercial_release_plans "
                "WHERE approved_route_identity_sha256 IS NOT NULL"
            )
        ),
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM model_commercial_release_executions "
                "WHERE publication_receipt_sha256 IS NOT NULL"
            )
        ),
    )
    if any(int(value or 0) for value in protected_counts):
        raise RuntimeError(
            "0051 downgrade blocked by immutable provider account evidence"
        )

    op.drop_index(
        "ix_channel_cost_provider_account", table_name="channel_cost_entries"
    )
    op.drop_index(
        "ix_relay_task_stage_provider_account",
        table_name="relay_task_stage_events",
    )
    dialect = op.get_bind().dialect.name
    identity_names = tuple(column.name for column in _IDENTITY_COLUMNS)
    if dialect == "sqlite":
        for name in (
            "trg_relay_task_stage_events_provider_identity_insert",
            "trg_relay_task_stage_events_provider_identity_update",
            "trg_channel_cost_entries_provider_identity_insert",
            "trg_channel_cost_entries_provider_identity_update",
            "trg_generation_task_provider_route_validate_insert",
            "trg_generation_task_provider_route_validate_update",
            "trg_generation_task_provider_route_immutable",
            "trg_model_commercial_plan_route_identity_insert",
            "trg_model_commercial_execution_receipt_insert",
            "trg_model_commercial_execution_receipt_update",
            "trg_model_commercial_execution_receipt_immutable",
        ):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))

        stage_triggers = _capture_sqlite_triggers("relay_task_stage_events")
        _drop_sqlite_triggers("relay_task_stage_events")
        with op.batch_alter_table(
            "relay_task_stage_events", recreate="always"
        ) as batch:
            batch.drop_constraint("ck_relay_task_stage_schema", type_="check")
            batch.create_check_constraint(
                "ck_relay_task_stage_schema_v1", "schema_version = 1"
            )
            for name in identity_names:
                batch.drop_column(name)
        _restore_sqlite_triggers(stage_triggers)

        cost_triggers = _capture_sqlite_triggers("channel_cost_entries")
        _drop_sqlite_triggers("channel_cost_entries")
        with op.batch_alter_table(
            "channel_cost_entries", recreate="always"
        ) as batch:
            for name in ("schema_version", "route_id", *identity_names):
                batch.drop_column(name)
        _restore_sqlite_triggers(cost_triggers)

        # These two columns are not part of a table-level CHECK or FK.  Use
        # SQLite's native DROP COLUMN so the many append-only billing triggers
        # on other tables that reference generation_tasks never see the table
        # disappear during a batch-table rename.
        op.drop_column("generation_tasks", "provider_route_evidence_sha256")
        op.drop_column("generation_tasks", "provider_route_evidence")

        op.drop_column(
            "model_commercial_release_plans", "approved_route_identity_sha256"
        )
        op.drop_column(
            "model_commercial_release_plans", "approved_route_identity"
        )
        op.drop_column(
            "model_commercial_release_executions", "publication_receipt_sha256"
        )
        op.drop_column(
            "model_commercial_release_executions", "publication_receipt"
        )
        op.drop_column(
            "model_commercial_release_executions",
            "released_route_identity_sha256",
        )
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER trg_generation_task_provider_route_immutable "
            "ON generation_tasks"
        )
        op.execute("DROP FUNCTION reject_task_provider_route_evidence_mutation()")
        op.execute(
            "DROP TRIGGER trg_model_commercial_execution_receipt_immutable "
            "ON model_commercial_release_executions"
        )
        op.execute("DROP FUNCTION reject_commercial_receipt_mutation()")
        op.drop_constraint(
            "ck_model_commercial_execution_receipt",
            "model_commercial_release_executions",
            type_="check",
        )
        op.drop_constraint(
            "ck_model_commercial_plan_route_identity",
            "model_commercial_release_plans",
            type_="check",
        )
        op.drop_constraint(
            "ck_task_provider_route_evidence_complete",
            "generation_tasks",
            type_="check",
        )
        op.drop_constraint(
            "ck_channel_cost_provider_identity",
            "channel_cost_entries",
            type_="check",
        )
        op.drop_constraint(
            "ck_relay_task_stage_provider_identity",
            "relay_task_stage_events",
            type_="check",
        )
        op.drop_constraint(
            "ck_relay_task_stage_schema",
            "relay_task_stage_events",
            type_="check",
        )
        op.create_check_constraint(
            "ck_relay_task_stage_schema_v1",
            "relay_task_stage_events",
            "schema_version = 1",
        )
        for table, names in (
            ("relay_task_stage_events", identity_names),
            (
                "channel_cost_entries",
                ("schema_version", "route_id", *identity_names),
            ),
            (
                "generation_tasks",
                ("provider_route_evidence", "provider_route_evidence_sha256"),
            ),
            (
                "model_commercial_release_plans",
                ("approved_route_identity", "approved_route_identity_sha256"),
            ),
            (
                "model_commercial_release_executions",
                (
                    "released_route_identity_sha256",
                    "publication_receipt",
                    "publication_receipt_sha256",
                ),
            ),
        ):
            for name in reversed(names):
                op.drop_column(table, name)
    else:
        raise RuntimeError("0051 supports only SQLite and PostgreSQL")
