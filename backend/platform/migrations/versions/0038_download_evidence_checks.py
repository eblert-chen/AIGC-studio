"""Align and strengthen durable download-evidence constraints.

Revision ID: 0038_download_evidence_checks
Revises: 0037_production_auth_lifecycle

The PostgreSQL path preserves the 0021 NOT VALID compatibility boundary for
historical unsigned completion rows, gives that check its canonical metadata
name, and installs explicit SHA-256 checks. SQLite normalizes the equivalent
metadata while restoring every immutable or insert-time guard lost by table
rebuild. Nonpositive historical artifact evidence fails closed before DDL.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v3 as policy_v3
from platform_api.database_privileges_behavior_v3 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v3,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0038_download_evidence_checks"
down_revision: str | None = "0037_production_auth_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMPLETION_OLD = "ck_download_completion_new_rows_verified"
_COMPLETION_NEW = "ck_download_completion_verified_source"
_DOWNLOAD_SOURCE = "ck_download_source_url_sha256_hex"
_DOWNLOAD_GATEWAY = "ck_download_gateway_ticket_url_sha256_hex"
_PERSONAL_OLD = "ck_personal_download_source_url_sha_shape"
_PERSONAL_NEW = "ck_personal_download_source_url_sha_hex"

_CHANNEL_EVENT_ID = "ck_channel_cost_relay_event_id_format"
_CHANNEL_EVIDENCE = "ck_channel_cost_relay_evidence_complete"
_CHANNEL_PAYLOAD = "ck_channel_cost_relay_payload_sha256"
_COMPLETION_ARTIFACT = "ck_download_completion_artifact_sha256"
_COMPLETION_EVIDENCE = "ck_download_completion_verified_evidence_complete"
_COMPLETION_PAYLOAD = "ck_download_completion_payload_sha256"
_COMPLETION_SIGNED_EVENT = "ck_download_completion_signed_event_id"
_TASK_ARTIFACT_POSITIVE = "ck_task_artifact_size_positive"
_DOWNLOAD_BINDING = "ck_download_storage_binding_complete"

_CHANNEL_AMOUNT = "ck_channel_cost_amount_range"
_COMPLETION_BYTES = "ck_download_completion_bytes_nonnegative"
_DOWNLOAD_EXPIRY = "ck_download_expiry_positive"
_TASK_ARTIFACT_MEDIA_TYPE = "ck_task_artifact_media_type"
_TASK_ARTIFACT_POSITION = "ck_task_artifact_position_nonnegative"
_TASK_ARTIFACT_SCOPE = "ck_task_artifact_scope"
_TASK_ARTIFACT_NONNEGATIVE = "ck_task_artifact_size_nonnegative"
_PERSONAL_EXPIRY = "ck_personal_download_expiry_positive"
_PERSONAL_STORAGE_PROVIDER = "ck_personal_download_storage_provider"

_SQLITE_TRIGGER_NAMES = {
    "channel_cost_entries": frozenset(
        {
            "trg_channel_cost_entries_no_delete",
            "trg_channel_cost_entries_no_update",
            "trg_channel_cost_personal_workspace_fk",
        }
    ),
    "download_completions": frozenset(
        {
            "trg_download_completions_no_delete",
            "trg_download_completions_no_update",
            "trg_download_completions_verified_insert",
        }
    ),
    "download_records": frozenset(
        {
            "trg_download_records_no_delete",
            "trg_download_records_no_update",
            "trg_download_records_storage_binding_insert",
        }
    ),
    "task_artifacts": frozenset(
        {
            "trg_task_artifacts_no_delete",
            "trg_task_artifacts_no_update",
            "trg_task_artifacts_size_positive_insert",
        }
    ),
    "personal_download_records": frozenset(
        {
            "trg_personal_download_records_no_delete",
            "trg_personal_download_records_no_update",
        }
    ),
}

_HEX_REMAINDER = (
    "replace(replace(replace(replace(replace(replace("
    "replace(replace(replace(replace(replace(replace(replace("
    "replace(replace(replace({column}, '0', ''), '1', ''), '2', ''), "
    "'3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), "
    "'9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), "
    "'f', '')"
)

_CHANNEL_EVIDENCE_CHECK = (
    "(relay_event_id IS NULL "
    "AND relay_event_timestamp IS NULL "
    "AND relay_payload_sha256 IS NULL) "
    "OR (relay_event_id IS NOT NULL "
    "AND relay_event_timestamp IS NOT NULL "
    "AND relay_payload_sha256 IS NOT NULL)"
)

_COMPLETION_EVIDENCE_CHECK = (
    "(verification_version IS NULL "
    "AND artifact_sha256 IS NULL "
    "AND expected_size_bytes IS NULL "
    "AND http_status IS NULL "
    "AND transfer_scope IS NULL "
    "AND source_evidence IS NULL "
    "AND signed_event_id IS NULL "
    "AND signed_event_timestamp IS NULL "
    "AND signed_payload_sha256 IS NULL "
    "AND verified_at IS NULL) OR "
    "(verification_version = 1 "
    "AND artifact_sha256 IS NOT NULL "
    "AND expected_size_bytes IS NOT NULL "
    "AND expected_size_bytes = bytes_sent "
    "AND http_status = 200 "
    "AND transfer_scope = 'full_body' "
    "AND source_evidence IS NOT NULL "
    "AND signed_event_id IS NOT NULL "
    "AND signed_event_timestamp IS NOT NULL "
    "AND signed_payload_sha256 IS NOT NULL "
    "AND verified_at IS NOT NULL)"
)

_COMPLETION_VERIFIED_SOURCE_CHECK = (
    "verification_version IS NULL OR "
    "source IN ('EDGE_GATEWAY', 'OBS_ACCESS_LOG')"
)

_DOWNLOAD_BINDING_CHECK = (
    "(storage_binding_version IS NULL "
    "AND storage_provider IS NULL "
    "AND storage_endpoint_host IS NULL "
    "AND storage_bucket IS NULL "
    "AND storage_object_key IS NULL "
    "AND storage_version_id IS NULL "
    "AND source_url_sha256 IS NULL "
    "AND relay_issued_at IS NULL "
    "AND relay_expires_at IS NULL "
    "AND gateway_registration_request_id IS NULL "
    "AND gateway_ticket_id IS NULL "
    "AND gateway_ticket_url_sha256 IS NULL "
    "AND gateway_issued_at IS NULL "
    "AND gateway_expires_at IS NULL "
    "AND gateway_transfer_reference IS NULL) OR "
    "(storage_binding_version = 1 "
    "AND storage_provider IS NOT NULL "
    "AND storage_provider = 'huawei_obs' "
    "AND storage_endpoint_host IS NOT NULL "
    "AND storage_bucket IS NOT NULL "
    "AND storage_object_key IS NOT NULL "
    "AND source_url_sha256 IS NOT NULL "
    "AND length(source_url_sha256) = 64 "
    "AND lower(source_url_sha256) = source_url_sha256 "
    "AND relay_issued_at IS NOT NULL "
    "AND relay_expires_at IS NOT NULL "
    "AND relay_expires_at > relay_issued_at "
    "AND ((gateway_registration_request_id IS NULL "
    "AND gateway_ticket_id IS NULL "
    "AND gateway_ticket_url_sha256 IS NULL "
    "AND gateway_issued_at IS NULL "
    "AND gateway_expires_at IS NULL "
    "AND gateway_transfer_reference IS NULL) OR "
    "(gateway_registration_request_id IS NOT NULL "
    "AND gateway_ticket_id IS NOT NULL "
    "AND gateway_ticket_url_sha256 IS NOT NULL "
    "AND length(gateway_ticket_url_sha256) = 64 "
    "AND lower(gateway_ticket_url_sha256) = gateway_ticket_url_sha256 "
    "AND gateway_issued_at IS NOT NULL "
    "AND gateway_expires_at IS NOT NULL "
    "AND gateway_expires_at > gateway_issued_at "
    "AND expires_at = gateway_expires_at "
    "AND gateway_transfer_reference IS NOT NULL)))"
)

_CHANNEL_AMOUNT_CHECK = (
    "amount_cents >= -9000000000000000 "
    "AND amount_cents <= 9000000000000000"
)
_COMPLETION_BYTES_CHECK = "bytes_sent >= 0"
_DOWNLOAD_EXPIRY_CHECK = "expires_seconds > 0"
_TASK_ARTIFACT_MEDIA_TYPE_CHECK = "media_type IN ('image', 'video')"
_TASK_ARTIFACT_POSITION_CHECK = "position >= 0"
_TASK_ARTIFACT_SCOPE_CHECK = (
    "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
    "(company_id IS NULL AND personal_workspace_id IS NOT NULL)"
)
_TASK_ARTIFACT_NONNEGATIVE_CHECK = "size_bytes >= 0"
_PERSONAL_EXPIRY_CHECK = "expires_seconds > 0"
_PERSONAL_STORAGE_PROVIDER_CHECK = "storage_provider = 'huawei_obs'"
_PERSONAL_OLD_CHECK = (
    "length(source_url_sha256) = 64 "
    "AND lower(source_url_sha256) = source_url_sha256"
)
_PERSONAL_NEW_CHECK = (
    _PERSONAL_OLD_CHECK
    + " AND source_url_sha256 NOT GLOB '*[^0-9a-f]*'"
)


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def _sqlite_hex_check(column: str) -> str:
    return (
        f"{column} IS NULL OR (length({column}) = 64 "
        f"AND lower({column}) = {column} "
        f"AND {_HEX_REMAINDER.format(column=column)} = '')"
    )


def _sqlite_uuid_check(
    column: str,
    *,
    remove_hyphen_first: bool = False,
) -> str:
    remainder = _HEX_REMAINDER.format(column=column)
    if remove_hyphen_first:
        remainder = remainder.replace(
            f"{column}, '0'",
            f"replace({column}, '-', ''), '0'",
            1,
        )
    else:
        remainder = f"replace({remainder}, '-', '')"
    return (
        f"{column} IS NULL OR (length({column}) = 36 "
        f"AND substr({column}, 9, 1) = '-' "
        f"AND substr({column}, 14, 1) = '-' "
        f"AND substr({column}, 19, 1) = '-' "
        f"AND substr({column}, 24, 1) = '-' "
        f"AND lower({column}) = {column} "
        f"AND {remainder} = '')"
    )


def _sqlite_digest_check(column: str) -> str:
    return (
        f"{column} IS NULL OR (length({column}) = 64 "
        f"AND lower({column}) = {column} "
        f"AND {column} NOT GLOB '*[^0-9a-f]*')"
    )


def _capture_sqlite_triggers(
    connection: sa.Connection,
) -> dict[str, tuple[tuple[str, str], ...]]:
    result: dict[str, tuple[tuple[str, str], ...]] = {}
    for table_name, expected_names in _SQLITE_TRIGGER_NAMES.items():
        rows = connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = :table_name "
                "ORDER BY name"
            ),
            {"table_name": table_name},
        ).all()
        actual_names = frozenset(str(row.name) for row in rows)
        if actual_names != expected_names or any(row.sql is None for row in rows):
            raise RuntimeError(
                f"SQLite trigger inventory is invalid for {table_name}"
            )
        result[table_name] = tuple(
            (str(row.name), str(row.sql)) for row in rows
        )
    return result


def _restore_sqlite_triggers(
    table_name: str,
    triggers: tuple[tuple[str, str], ...],
) -> None:
    for trigger_name, statement in triggers:
        _execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')
        _execute(statement)
    connection = op.get_bind()
    actual_names = frozenset(
        str(name)
        for name in connection.scalars(
            sa.text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = :table_name"
            ),
            {"table_name": table_name},
        )
    )
    if actual_names != _SQLITE_TRIGGER_NAMES[table_name]:
        raise RuntimeError(
            f"SQLite trigger restoration failed for {table_name}"
        )


def _rebuild_sqlite_with_frozen_checks(
    table_name: str,
    *,
    table_checks: tuple[tuple[str, str], ...],
    column_checks: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> None:
    connection = op.get_bind()
    reflected = sa.Table(
        table_name,
        sa.MetaData(),
        autoload_with=connection,
        resolve_fks=False,
    )
    # SQLAlchemy 2.0.36 can reflect adjacent legacy column checks as one
    # concatenated constraint, while newer releases split them. Discard every
    # reflected check and rebuild the complete frozen set, independent of that
    # version-specific parser shape. All non-check constraints and indexes stay
    # on the reflected copy.
    for constraint in tuple(reflected.constraints):
        if isinstance(constraint, sa.CheckConstraint):
            reflected.constraints.remove(constraint)
    for column in reflected.columns:
        for constraint in tuple(column.constraints):
            if isinstance(constraint, sa.CheckConstraint):
                column.constraints.remove(constraint)
    for constraint_name, expression in table_checks:
        reflected.append_constraint(
            sa.CheckConstraint(expression, name=constraint_name)
        )
    for column_name, checks in (column_checks or {}).items():
        for constraint_name, expression in checks:
            constraint = sa.CheckConstraint(
                expression,
                name=constraint_name,
            )
            # Revisions 0019 and 0021 created these as column constraints.
            # Preserve that physical placement so their historical SQLite
            # downgrades can drop the columns without a surviving table check.
            constraint._set_parent(reflected.c[column_name])
    with op.batch_alter_table(
        table_name,
        recreate="always",
        copy_from=reflected,
    ):
        pass


def _validate_upgrade_inventory() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        invalid = connection.scalar(
            sa.text(
                "SELECT 1 FROM download_records WHERE "
                "(source_url_sha256 IS NOT NULL AND "
                "source_url_sha256 !~ '^[0-9a-f]{64}$') OR "
                "(gateway_ticket_url_sha256 IS NOT NULL AND "
                "gateway_ticket_url_sha256 !~ '^[0-9a-f]{64}$') "
                "LIMIT 1"
            )
        )
        invalid_personal = connection.scalar(
            sa.text(
                "SELECT 1 FROM personal_download_records "
                "WHERE source_url_sha256 !~ '^[0-9a-f]{64}$' LIMIT 1"
            )
        )
    else:
        invalid = connection.scalar(
            sa.text(
                "SELECT 1 FROM download_records WHERE "
                "(source_url_sha256 IS NOT NULL AND "
                "(length(source_url_sha256) != 64 "
                "OR lower(source_url_sha256) != source_url_sha256 "
                "OR source_url_sha256 GLOB '*[^0-9a-f]*')) OR "
                "(gateway_ticket_url_sha256 IS NOT NULL AND "
                "(length(gateway_ticket_url_sha256) != 64 "
                "OR lower(gateway_ticket_url_sha256) != gateway_ticket_url_sha256 "
                "OR gateway_ticket_url_sha256 GLOB '*[^0-9a-f]*')) "
                "LIMIT 1"
            )
        )
        invalid_personal = connection.scalar(
            sa.text(
                "SELECT 1 FROM personal_download_records WHERE "
                "length(source_url_sha256) != 64 "
                "OR lower(source_url_sha256) != source_url_sha256 "
                "OR source_url_sha256 GLOB '*[^0-9a-f]*' LIMIT 1"
            )
        )
        invalid_task_artifact = connection.scalar(
            sa.text(
                "SELECT 1 FROM task_artifacts "
                "WHERE size_bytes <= 0 LIMIT 1"
            )
        )
        if invalid_task_artifact is not None:
            raise RuntimeError(
                "task artifact size inventory is invalid; "
                "repair it from trusted storage evidence before migration"
            )
        invalid_binding = connection.scalar(
            sa.text(
                "SELECT 1 FROM download_records WHERE NOT ("
                + _DOWNLOAD_BINDING_CHECK
                + ") LIMIT 1"
            )
        )
        invalid_completion_source = connection.scalar(
            sa.text(
                "SELECT 1 FROM download_completions WHERE NOT ("
                + _COMPLETION_VERIFIED_SOURCE_CHECK
                + ") LIMIT 1"
            )
        )
        if invalid_binding is not None or invalid_completion_source is not None:
            raise RuntimeError(
                "SQLite download evidence inventory is invalid"
            )
    if invalid is not None or invalid_personal is not None:
        raise RuntimeError("download evidence SHA-256 inventory is invalid")


def _upgrade_postgresql() -> None:
    _execute(
        "ALTER TABLE download_completions RENAME CONSTRAINT "
        f"{_COMPLETION_OLD} TO {_COMPLETION_NEW}"
    )
    _execute(
        "ALTER TABLE download_records ADD CONSTRAINT "
        f"{_DOWNLOAD_SOURCE} CHECK (source_url_sha256 IS NULL OR "
        "source_url_sha256 ~ '^[0-9a-f]{64}$') NOT VALID"
    )
    _execute(
        "ALTER TABLE download_records VALIDATE CONSTRAINT "
        f"{_DOWNLOAD_SOURCE}"
    )
    _execute(
        "ALTER TABLE download_records ADD CONSTRAINT "
        f"{_DOWNLOAD_GATEWAY} CHECK (gateway_ticket_url_sha256 IS NULL OR "
        "gateway_ticket_url_sha256 ~ '^[0-9a-f]{64}$') NOT VALID"
    )
    _execute(
        "ALTER TABLE download_records VALIDATE CONSTRAINT "
        f"{_DOWNLOAD_GATEWAY}"
    )
    _execute(
        "ALTER TABLE personal_download_records ADD CONSTRAINT "
        f"{_PERSONAL_NEW} CHECK ("
        "source_url_sha256 ~ '^[0-9a-f]{64}$') NOT VALID"
    )
    _execute(
        "ALTER TABLE personal_download_records VALIDATE CONSTRAINT "
        f"{_PERSONAL_NEW}"
    )
    _execute(
        "ALTER TABLE personal_download_records DROP CONSTRAINT "
        f"{_PERSONAL_OLD}"
    )


def _upgrade_sqlite() -> None:
    triggers = _capture_sqlite_triggers(op.get_bind())

    _rebuild_sqlite_with_frozen_checks(
        "channel_cost_entries",
        table_checks=(
            (_CHANNEL_AMOUNT, _CHANNEL_AMOUNT_CHECK),
            (_CHANNEL_EVENT_ID, _sqlite_uuid_check("relay_event_id")),
            (_CHANNEL_EVIDENCE, _CHANNEL_EVIDENCE_CHECK),
            (_CHANNEL_PAYLOAD, _sqlite_hex_check("relay_payload_sha256")),
        ),
    )
    _restore_sqlite_triggers(
        "channel_cost_entries",
        triggers["channel_cost_entries"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "download_completions",
        table_checks=(
            (_COMPLETION_BYTES, _COMPLETION_BYTES_CHECK),
            (_COMPLETION_ARTIFACT, _sqlite_hex_check("artifact_sha256")),
            (_COMPLETION_EVIDENCE, _COMPLETION_EVIDENCE_CHECK),
            (
                _COMPLETION_PAYLOAD,
                _sqlite_hex_check("signed_payload_sha256"),
            ),
            (
                _COMPLETION_SIGNED_EVENT,
                _sqlite_uuid_check("signed_event_id"),
            ),
            (_COMPLETION_NEW, _COMPLETION_VERIFIED_SOURCE_CHECK),
        ),
    )
    _restore_sqlite_triggers(
        "download_completions",
        triggers["download_completions"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "download_records",
        table_checks=(
            (_DOWNLOAD_EXPIRY, _DOWNLOAD_EXPIRY_CHECK),
            (_DOWNLOAD_BINDING, _DOWNLOAD_BINDING_CHECK),
            (_DOWNLOAD_SOURCE, _sqlite_digest_check("source_url_sha256")),
            (
                _DOWNLOAD_GATEWAY,
                _sqlite_digest_check("gateway_ticket_url_sha256"),
            ),
        ),
    )
    _restore_sqlite_triggers(
        "download_records",
        triggers["download_records"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "task_artifacts",
        table_checks=(
            (_TASK_ARTIFACT_MEDIA_TYPE, _TASK_ARTIFACT_MEDIA_TYPE_CHECK),
            (_TASK_ARTIFACT_POSITION, _TASK_ARTIFACT_POSITION_CHECK),
            (_TASK_ARTIFACT_SCOPE, _TASK_ARTIFACT_SCOPE_CHECK),
            (_TASK_ARTIFACT_NONNEGATIVE, _TASK_ARTIFACT_NONNEGATIVE_CHECK),
            (_TASK_ARTIFACT_POSITIVE, "size_bytes > 0"),
        ),
    )
    _restore_sqlite_triggers(
        "task_artifacts",
        triggers["task_artifacts"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "personal_download_records",
        table_checks=(
            (_PERSONAL_EXPIRY, _PERSONAL_EXPIRY_CHECK),
            (_PERSONAL_STORAGE_PROVIDER, _PERSONAL_STORAGE_PROVIDER_CHECK),
            (_PERSONAL_NEW, _PERSONAL_NEW_CHECK),
        ),
    )
    _restore_sqlite_triggers(
        "personal_download_records",
        triggers["personal_download_records"],
    )


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v3()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v3)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v3,
        )

    _validate_upgrade_inventory()
    if connection.dialect.name == "postgresql":
        _upgrade_postgresql()
    elif connection.dialect.name == "sqlite":
        _upgrade_sqlite()

    if protected_postgres:
        evidence = collect_platform_database_evidence(connection, policy=policy_v3)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v3,
        )


def _downgrade_postgresql() -> None:
    _execute(
        "ALTER TABLE personal_download_records ADD CONSTRAINT "
        f"{_PERSONAL_OLD} CHECK (length(source_url_sha256) = 64 "
        "AND lower(source_url_sha256) = source_url_sha256)"
    )
    _execute(
        "ALTER TABLE personal_download_records DROP CONSTRAINT "
        f"{_PERSONAL_NEW}"
    )
    _execute(
        "ALTER TABLE download_records DROP CONSTRAINT "
        f"{_DOWNLOAD_GATEWAY}"
    )
    _execute(
        "ALTER TABLE download_records DROP CONSTRAINT "
        f"{_DOWNLOAD_SOURCE}"
    )
    _execute(
        "ALTER TABLE download_completions RENAME CONSTRAINT "
        f"{_COMPLETION_NEW} TO {_COMPLETION_OLD}"
    )


def _downgrade_sqlite() -> None:
    triggers = _capture_sqlite_triggers(op.get_bind())

    _rebuild_sqlite_with_frozen_checks(
        "channel_cost_entries",
        table_checks=((_CHANNEL_AMOUNT, _CHANNEL_AMOUNT_CHECK),),
        column_checks={
            "relay_event_id": (
                (_CHANNEL_EVENT_ID, _sqlite_uuid_check("relay_event_id")),
            ),
            "relay_payload_sha256": (
                (_CHANNEL_PAYLOAD, _sqlite_hex_check("relay_payload_sha256")),
                (_CHANNEL_EVIDENCE, _CHANNEL_EVIDENCE_CHECK),
            ),
        },
    )
    _restore_sqlite_triggers(
        "channel_cost_entries",
        triggers["channel_cost_entries"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "download_completions",
        table_checks=(
            (_COMPLETION_BYTES, _COMPLETION_BYTES_CHECK),
        ),
        column_checks={
            "artifact_sha256": (
                (_COMPLETION_ARTIFACT, _sqlite_hex_check("artifact_sha256")),
            ),
            "signed_event_id": (
                (
                    _COMPLETION_SIGNED_EVENT,
                    _sqlite_uuid_check(
                        "signed_event_id",
                        remove_hyphen_first=True,
                    ),
                ),
            ),
            "signed_payload_sha256": (
                (
                    _COMPLETION_PAYLOAD,
                    _sqlite_hex_check("signed_payload_sha256"),
                ),
            ),
            "verified_at": (
                (_COMPLETION_EVIDENCE, _COMPLETION_EVIDENCE_CHECK),
            ),
        },
    )
    _restore_sqlite_triggers(
        "download_completions",
        triggers["download_completions"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "download_records",
        table_checks=((_DOWNLOAD_EXPIRY, _DOWNLOAD_EXPIRY_CHECK),),
    )
    _restore_sqlite_triggers(
        "download_records",
        triggers["download_records"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "task_artifacts",
        table_checks=(
            (_TASK_ARTIFACT_MEDIA_TYPE, _TASK_ARTIFACT_MEDIA_TYPE_CHECK),
            (_TASK_ARTIFACT_POSITION, _TASK_ARTIFACT_POSITION_CHECK),
            (_TASK_ARTIFACT_SCOPE, _TASK_ARTIFACT_SCOPE_CHECK),
            (_TASK_ARTIFACT_NONNEGATIVE, _TASK_ARTIFACT_NONNEGATIVE_CHECK),
        ),
    )
    _restore_sqlite_triggers(
        "task_artifacts",
        triggers["task_artifacts"],
    )

    _rebuild_sqlite_with_frozen_checks(
        "personal_download_records",
        table_checks=(
            (_PERSONAL_EXPIRY, _PERSONAL_EXPIRY_CHECK),
            (_PERSONAL_STORAGE_PROVIDER, _PERSONAL_STORAGE_PROVIDER_CHECK),
            (_PERSONAL_OLD, _PERSONAL_OLD_CHECK),
        ),
    )
    _restore_sqlite_triggers(
        "personal_download_records",
        triggers["personal_download_records"],
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _downgrade_postgresql()
    elif dialect == "sqlite":
        _downgrade_sqlite()
