from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

DATABASE_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


def _revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'ledger_entries'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        }


def _cost_trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'channel_cost_entries'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        }


def _cost_check_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'channel_cost_entries'::regclass "
                    "AND contype = 'c'"
                )
            )
        }


def _artifact_audit_trigger_names(engine, table_name: str) -> set[str]:
    assert table_name in {
        "task_artifacts",
        "download_records",
        "download_completions",
    }
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    f"WHERE tgrelid = '{table_name}'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        }


def _insert_postgres_download_binding(
    connection,
    *,
    record_id: str,
    source_sha256: str,
    gateway_ticket_url_sha256: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    has_gateway = gateway_ticket_url_sha256 is not None
    gateway_expires_at = now + timedelta(minutes=2)
    connection.execute(
        text(
            "INSERT INTO download_records "
            "(id, company_id, task_id, asset_id, requested_by_user_id, "
            "expires_seconds, expires_at, request_id, "
            "storage_binding_version, storage_provider, "
            "storage_endpoint_host, storage_bucket, storage_object_key, "
            "source_url_sha256, relay_issued_at, relay_expires_at, "
            "gateway_registration_request_id, gateway_ticket_id, "
            "gateway_ticket_url_sha256, gateway_issued_at, "
            "gateway_expires_at, gateway_transfer_reference, created_at) "
            "VALUES (:id, 'missing-company', 'missing-task', 'artifact-1', "
            "'missing-user', :expires_seconds, :expires_at, :request_id, "
            "1, 'huawei_obs', 'obs.cn-north-4.myhuaweicloud.com', "
            "'relay-output-private', 'outputs/task/artifact-1.mp4', "
            ":source_sha256, :relay_issued_at, :relay_expires_at, "
            ":gateway_registration_request_id, :gateway_ticket_id, "
            ":gateway_ticket_url_sha256, :gateway_issued_at, "
            ":gateway_expires_at, :gateway_transfer_reference, :created_at)"
        ),
        {
            "id": record_id,
            "expires_seconds": 120 if has_gateway else 300,
            "expires_at": (
                gateway_expires_at if has_gateway else now + timedelta(minutes=5)
            ),
            "request_id": f"request-{record_id}",
            "source_sha256": source_sha256,
            "relay_issued_at": now,
            "relay_expires_at": now + timedelta(minutes=5),
            "gateway_registration_request_id": (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{record_id}:registration"))
                if has_gateway
                else None
            ),
            "gateway_ticket_id": (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{record_id}:ticket"))
                if has_gateway
                else None
            ),
            "gateway_ticket_url_sha256": gateway_ticket_url_sha256,
            "gateway_issued_at": now if has_gateway else None,
            "gateway_expires_at": gateway_expires_at if has_gateway else None,
            "gateway_transfer_reference": (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{record_id}:transfer"))
                if has_gateway
                else None
            ),
            "created_at": now,
        },
    )


def _insert_postgres_gateway_attempt(
    connection,
    *,
    attempt_id: str,
    hash_overrides: dict[str, str | None] | None = None,
    status: str = "PENDING",
) -> None:
    now = datetime.now(timezone.utc)
    hashes: dict[str, str | None] = {
        "artifact_sha256": "a" * 64,
        "source_url_sha256": "b" * 64,
        "body_sha256": "c" * 64,
        "response_sha256": None,
        "gateway_ticket_url_sha256": None,
        "reconciliation_ack_sha256": None,
    }
    hashes.update(hash_overrides or {})
    connection.execute(
        text(
            "INSERT INTO download_gateway_registration_attempts ("
            "id, company_id, task_id, asset_id, requested_by_user_id, "
            "platform_request_id, registration_request_id, "
            "download_record_id, transfer_reference, "
            "expected_size_bytes, artifact_sha256, storage_provider, "
            "storage_endpoint_host, storage_bucket, storage_object_key, "
            "source_url_sha256, relay_issued_at, relay_expires_at, "
            "body_sha256, response_sha256, gateway_ticket_url_sha256, "
            "reconciliation_ack_sha256, "
            "status, attempt_count, ticket_replay_count, created_at, "
            "updated_at) VALUES ("
            ":id, 'missing-company', 'missing-task', 'artifact-1', "
            "'missing-user', :platform_request_id, "
            ":registration_request_id, :download_record_id, "
            ":transfer_reference, 1, :artifact_sha256, 'huawei_obs', "
            "'obs.cn-north-4.myhuaweicloud.com', "
            "'relay-output-private', 'outputs/task/artifact-1.mp4', "
            ":source_url_sha256, :relay_issued_at, :relay_expires_at, "
            ":body_sha256, :response_sha256, "
            ":gateway_ticket_url_sha256, :reconciliation_ack_sha256, "
            ":status, 0, 0, :created_at, "
            ":updated_at)"
        ),
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{attempt_id}:id")),
            "platform_request_id": f"platform-{attempt_id}",
            "registration_request_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{attempt_id}:registration")
            ),
            "download_record_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{attempt_id}:record")
            ),
            "transfer_reference": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{attempt_id}:transfer")
            ),
            "relay_issued_at": now,
            "relay_expires_at": now + timedelta(minutes=5),
            "status": status,
            "created_at": now,
            "updated_at": now,
            **hashes,
        },
    )


def test_postgres_billing_migration_upgrade_downgrade_and_guards(
    monkeypatch,
):
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    schema_name = f"billing_migration_{uuid.uuid4().hex}"
    assert schema_name.startswith("billing_migration_")
    administration_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')

    schema_url = (
        make_url(DATABASE_URL)
        .update_query_dict({"options": f"-csearch_path={schema_name}"})
        .render_as_string(hide_password=False)
    )
    migration_engine = create_engine(schema_url, pool_pre_ping=True)
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    monkeypatch.setenv("DATABASE_URL", schema_url)

    try:
        command.upgrade(config, "0022_download_storage_binding")
        assert _revision(migration_engine) == "0022_download_storage_binding"
        assert not inspect(migration_engine).has_table(
            "download_gateway_registration_attempts"
        )
        command.upgrade(config, "head")
        assert _revision(migration_engine) == "0040_showcase_management"
        for table_name in ("generation_tasks", "relay_submission_outbox"):
            affinity_columns = {
                column["name"]: column
                for column in inspect(migration_engine).get_columns(table_name)
                if column["name"] in {"relay_backend_id", "relay_contract_revision"}
            }
            assert set(affinity_columns) == {
                "relay_backend_id",
                "relay_contract_revision",
            }
            assert all(
                column["nullable"] is False for column in affinity_columns.values()
            )
        assert "ix_ledger_kind_created" in {
            item["name"]
            for item in inspect(migration_engine).get_indexes("ledger_entries")
        }
        assert _trigger_names(migration_engine) == {
            "trg_ledger_entries_immutable",
            "trg_ledger_entries_no_truncate",
        }
        assert _cost_trigger_names(migration_engine) == {
            "trg_channel_cost_entries_immutable",
            "trg_channel_cost_entries_no_truncate",
        }
        assert {
            "ck_channel_cost_amount_range",
            "ck_channel_cost_relay_evidence_complete",
            "ck_channel_cost_relay_event_id_format",
            "ck_channel_cost_relay_payload_sha256",
        } <= _cost_check_names(migration_engine)
        for table_name in (
            "task_artifacts",
            "download_records",
            "download_completions",
        ):
            assert _artifact_audit_trigger_names(migration_engine, table_name) == {
                f"trg_{table_name}_immutable",
                f"trg_{table_name}_no_truncate",
            }
            with pytest.raises(
                DBAPIError,
                match="artifact and download audit records are immutable",
            ):
                with migration_engine.begin() as connection:
                    connection.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))

        with migration_engine.connect() as connection:
            validated = connection.scalar(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid = 'download_completions'::regclass "
                    "AND conname = 'ck_download_completion_verified_source'"
                )
            )
        assert validated is False

        with migration_engine.connect() as connection:
            storage_binding_validated = connection.scalar(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid = 'download_records'::regclass "
                    "AND conname = 'ck_download_storage_binding_complete'"
                )
            )
            artifact_size_validated = connection.scalar(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid = 'task_artifacts'::regclass "
                    "AND conname = 'ck_task_artifact_size_positive'"
                )
            )
            download_columns = {
                column["name"]
                for column in inspect(migration_engine).get_columns("download_records")
            }
        assert storage_binding_validated is False
        assert artifact_size_validated is False
        assert "source_url" not in download_columns
        attempt_table = "download_gateway_registration_attempts"
        assert inspect(migration_engine).has_table(attempt_table)
        attempt_columns = {
            column["name"]: column
            for column in inspect(migration_engine).get_columns(attempt_table)
        }
        assert {
            "request_ciphertext",
            "request_nonce",
            "response_ciphertext",
            "response_nonce",
            "status",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "ticket_replay_count",
            "reconciliation_ack_sha256",
            "reconciled_at",
        } <= set(attempt_columns)
        assert attempt_columns["ticket_replay_count"]["type"].python_type is int
        assert {
            item["name"]
            for item in inspect(migration_engine).get_indexes(attempt_table)
        } >= {
            "ix_download_gateway_attempt_dispatch",
            "ix_download_gateway_attempt_company_created",
            "ix_download_gateway_registration_attempts_company_id",
            "ix_download_gateway_registration_attempts_task_id",
            "ix_download_gateway_registration_attempts_requested_by_user_id",
        }
        assert {
            item["name"]
            for item in inspect(migration_engine).get_unique_constraints(attempt_table)
        } == {
            "uq_download_gateway_attempt_request",
            "uq_download_gateway_attempt_registration",
            "uq_download_gateway_attempt_record",
            "uq_download_gateway_attempt_transfer",
        }
        assert {
            item["name"]
            for item in inspect(migration_engine).get_check_constraints(attempt_table)
        } == {
            "ck_download_gateway_attempt_count_nonnegative",
            "ck_download_gateway_attempt_size_positive",
            "ck_download_gateway_attempt_replay_count",
            "ck_download_gateway_attempt_lease_complete",
            "ck_download_gateway_attempt_artifact_sha_hex",
            "ck_download_gateway_attempt_source_url_sha_hex",
            "ck_download_gateway_attempt_body_sha_shape",
            "ck_download_gateway_attempt_response_sha_hex",
            "ck_download_gateway_attempt_ticket_url_sha_hex",
            "ck_download_gateway_attempt_reconciliation_ack_sha_hex",
        }
        assert {
            (tuple(item["constrained_columns"]), item["referred_table"])
            for item in inspect(migration_engine).get_foreign_keys(attempt_table)
        } == {
            (("company_id",), "companies"),
            (("task_id",), "generation_tasks"),
            (("requested_by_user_id",), "users"),
        }

        with migration_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            _insert_postgres_gateway_attempt(
                connection,
                attempt_id="pg-valid-nullable-attempt-hashes",
                status="RECONCILED_EXPIRED",
            )
        for field_name in (
            "artifact_sha256",
            "source_url_sha256",
            "body_sha256",
            "response_sha256",
            "gateway_ticket_url_sha256",
            "reconciliation_ack_sha256",
        ):
            for label, invalid_hash in (
                ("uppercase", "A" * 64),
                ("nonhex", "g" * 64),
            ):
                constraint_name = {
                    "artifact_sha256": ("ck_download_gateway_attempt_artifact_sha_hex"),
                    "source_url_sha256": (
                        "ck_download_gateway_attempt_source_url_sha_hex"
                    ),
                    "body_sha256": "ck_download_gateway_attempt_body_sha_shape",
                    "response_sha256": ("ck_download_gateway_attempt_response_sha_hex"),
                    "gateway_ticket_url_sha256": (
                        "ck_download_gateway_attempt_ticket_url_sha_hex"
                    ),
                    "reconciliation_ack_sha256": (
                        "ck_download_gateway_attempt_reconciliation_ack_sha_hex"
                    ),
                }[field_name]
                with pytest.raises(DBAPIError, match=constraint_name):
                    with migration_engine.begin() as connection:
                        connection.execute(
                            text("SET LOCAL session_replication_role = replica")
                        )
                        _insert_postgres_gateway_attempt(
                            connection,
                            attempt_id=f"pg-invalid-{field_name}-{label}",
                            hash_overrides={field_name: invalid_hash},
                        )

        with pytest.raises(
            DBAPIError,
            match="ck_task_artifact_size_positive",
        ):
            with migration_engine.begin() as connection:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(
                    text(
                        "INSERT INTO task_artifacts "
                        "(id, company_id, task_id, asset_id, position, "
                        "media_type, content_type, size_bytes, sha256, "
                        "created_at) VALUES "
                        "('pg-empty-artifact', 'missing-company', "
                        "'missing-task', 'empty-artifact', 0, 'video', "
                        f"'video/mp4', 0, '{('0' * 64)}', now())"
                    )
                )

        with migration_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    "INSERT INTO task_artifacts "
                    "(id, company_id, task_id, asset_id, position, "
                    "media_type, content_type, size_bytes, sha256, "
                    "created_at) VALUES "
                    "('pg-positive-artifact', 'missing-company', "
                    "'missing-task', 'positive-artifact', 1, 'video', "
                    f"'video/mp4', 1, '{('1' * 64)}', now())"
                )
            )

        with pytest.raises(
            DBAPIError,
            match="ck_download_storage_binding_complete",
        ):
            with migration_engine.begin() as connection:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(
                    text(
                        "INSERT INTO download_records "
                        "(id, company_id, task_id, asset_id, "
                        "requested_by_user_id, expires_seconds, expires_at, "
                        "request_id, storage_provider, created_at) VALUES "
                        "('pg-partial-storage-binding', 'missing-company', "
                        "'missing-task', 'artifact-1', 'missing-user', 300, "
                        "now() + interval '5 minutes', "
                        "'pg-partial-storage-request', 'huawei_obs', now())"
                    )
                )

        for record_id, source_sha256, gateway_sha256, constraint_name in (
            (
                "pg-uppercase-source-sha",
                "A" * 64,
                None,
                "ck_download_source_url_sha256_hex",
            ),
            (
                "pg-nonhex-source-sha",
                "g" * 64,
                None,
                "ck_download_source_url_sha256_hex",
            ),
            (
                "pg-uppercase-gateway-sha",
                "a" * 64,
                "B" * 64,
                "ck_download_gateway_ticket_url_sha256_hex",
            ),
            (
                "pg-nonhex-gateway-sha",
                "a" * 64,
                "z" * 64,
                "ck_download_gateway_ticket_url_sha256_hex",
            ),
        ):
            with pytest.raises(
                DBAPIError,
                match=constraint_name,
            ):
                with migration_engine.begin() as connection:
                    connection.execute(
                        text("SET LOCAL session_replication_role = replica")
                    )
                    _insert_postgres_download_binding(
                        connection,
                        record_id=record_id,
                        source_sha256=source_sha256,
                        gateway_ticket_url_sha256=gateway_sha256,
                    )

        with migration_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    "INSERT INTO download_records "
                    "(id, company_id, task_id, asset_id, "
                    "requested_by_user_id, expires_seconds, expires_at, "
                    "request_id, storage_binding_version, storage_provider, "
                    "storage_endpoint_host, storage_bucket, "
                    "storage_object_key, source_url_sha256, relay_issued_at, "
                    "relay_expires_at, gateway_registration_request_id, "
                    "gateway_ticket_id, gateway_ticket_url_sha256, "
                    "gateway_issued_at, gateway_expires_at, "
                    "gateway_transfer_reference, created_at) VALUES "
                    "('pg-complete-storage-binding', 'missing-company', "
                    "'missing-task', 'artifact-1', 'missing-user', 120, "
                    "now() + interval '2 minutes', "
                    "'pg-complete-storage-request', 1, 'huawei_obs', "
                    "'obs.cn-north-4.myhuaweicloud.com', "
                    "'relay-output-private', 'outputs/task/artifact-1.mp4', "
                    f"'{('a' * 64)}', now(), now() + interval '5 minutes', "
                    "'11111111-1111-4111-8111-111111111111', "
                    "'22222222-2222-4222-8222-222222222222', "
                    f"'{('b' * 64)}', now(), now() + interval '2 minutes', "
                    "'33333333-3333-4333-8333-333333333333', now())"
                )
            )

        with pytest.raises(
            DBAPIError,
            match="uq_download_gateway_registration_request",
        ):
            with migration_engine.begin() as connection:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(
                    text(
                        "INSERT INTO download_records "
                        "(id, company_id, task_id, asset_id, "
                        "requested_by_user_id, expires_seconds, expires_at, "
                        "request_id, storage_binding_version, storage_provider, "
                        "storage_endpoint_host, storage_bucket, "
                        "storage_object_key, source_url_sha256, relay_issued_at, "
                        "relay_expires_at, gateway_registration_request_id, "
                        "gateway_ticket_id, gateway_ticket_url_sha256, "
                        "gateway_issued_at, gateway_expires_at, "
                        "gateway_transfer_reference, created_at) VALUES "
                        "('pg-duplicate-storage-binding', 'missing-company', "
                        "'missing-task', 'artifact-2', 'missing-user', 120, "
                        "now() + interval '2 minutes', "
                        "'pg-duplicate-storage-request', 1, 'huawei_obs', "
                        "'obs.cn-north-4.myhuaweicloud.com', "
                        "'relay-output-private', 'outputs/task/artifact-2.mp4', "
                        f"'{('c' * 64)}', now(), now() + interval '5 minutes', "
                        "'11111111-1111-4111-8111-111111111111', "
                        "'44444444-4444-4444-8444-444444444444', "
                        f"'{('d' * 64)}', now(), now() + interval '2 minutes', "
                        "'55555555-5555-4555-8555-555555555555', now())"
                    )
                )

        with pytest.raises(
            DBAPIError,
            match="ck_download_completion_verified_source",
        ):
            with migration_engine.begin() as connection:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(
                    text(
                        "INSERT INTO download_completions "
                        "(id, download_record_id, external_event_id, source, "
                        "bytes_sent, completed_at, created_at) VALUES "
                        "('pg-new-unsigned-completion', 'missing-parent', "
                        "'pg-new-unsigned-event', 'EDGE_GATEWAY', 4096, "
                        "now(), now())"
                    )
                )

        with migration_engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    "INSERT INTO download_completions "
                    "(id, download_record_id, external_event_id, source, "
                    "bytes_sent, completed_at, verification_version, "
                    "artifact_sha256, expected_size_bytes, http_status, "
                    "transfer_scope, source_evidence, signed_event_id, "
                    "signed_event_timestamp, signed_payload_sha256, "
                    "verified_at, created_at) VALUES "
                    "('pg-verified-completion', 'missing-parent', "
                    "'pg-verified-event', 'EDGE_GATEWAY', 4096, now(), 1, "
                    f"'{('a' * 64)}', 4096, 200, 'full_body', "
                    '\'{"gateway_request_id":"pg-request",'
                    '"gateway_transfer_reference":"pg-transfer"}\'::json, '
                    "'77777777-7777-4777-8777-777777777777', now(), "
                    f"'{('b' * 64)}', now(), now())"
                )
            )

        for statement in (
            "UPDATE download_completions SET bytes_sent = 1 "
            "WHERE id = 'pg-verified-completion'",
            "DELETE FROM download_completions " "WHERE id = 'pg-verified-completion'",
        ):
            with pytest.raises(
                DBAPIError,
                match="artifact and download audit records are immutable",
            ):
                with migration_engine.begin() as connection:
                    connection.execute(text(statement))

        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies "
                    "(id, name, status, created_at, updated_at) VALUES "
                    "('pg-billing-migration-company', 'PG Billing Migration', "
                    "'ACTIVE', now(), now())"
                )
            )

        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO channel_cost_entries "
                    "(id, amount_cents, idempotency_key, channel_key, "
                    "channel_type, occurred_at, external_reference, "
                    "company_id, task_id, relay_job_id, relay_event_id, "
                    "relay_event_timestamp, relay_payload_sha256, note, "
                    "source, recorded_by_user_id, created_at) VALUES "
                    "('pg-signed-cost', 50, 'pg-signed-cost-key', "
                    "'official.demo', 'OFFICIAL', now(), "
                    "'pg-signed-cost-external', NULL, NULL, NULL, "
                    "'11111111-1111-4111-8111-111111111111', now(), "
                    f"'{('a' * 64)}', '', 'RELAY', NULL, now())"
                )
            )

        invalid_evidence_statements = (
            (
                "ck_channel_cost_relay_evidence_complete",
                "'22222222-2222-4222-8222-222222222222', NULL, NULL",
            ),
            (
                "ck_channel_cost_relay_event_id_format",
                f"'gggggggg-gggg-4ggg-8ggg-gggggggggggg', now(), '{('b' * 64)}'",
            ),
            (
                "ck_channel_cost_relay_payload_sha256",
                f"'33333333-3333-4333-8333-333333333333', now(), '{('C' * 64)}'",
            ),
        )
        for index, (constraint_name, evidence_values) in enumerate(
            invalid_evidence_statements,
            start=1,
        ):
            with pytest.raises(DBAPIError, match=constraint_name):
                with migration_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO channel_cost_entries "
                            "(id, amount_cents, idempotency_key, channel_key, "
                            "channel_type, occurred_at, external_reference, "
                            "company_id, task_id, relay_job_id, relay_event_id, "
                            "relay_event_timestamp, relay_payload_sha256, note, "
                            "source, recorded_by_user_id, created_at) VALUES "
                            f"('pg-invalid-evidence-{index}', 50, "
                            f"'pg-invalid-evidence-key-{index}', "
                            "'official.demo', 'OFFICIAL', now(), "
                            f"'pg-invalid-evidence-external-{index}', NULL, "
                            f"NULL, NULL, {evidence_values}, '', 'RELAY', "
                            "NULL, now())"
                        )
                    )

        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO channel_cost_entries "
                    "(id, amount_cents, idempotency_key, channel_key, "
                    "channel_type, occurred_at, external_reference, "
                    "company_id, task_id, relay_job_id, note, source, "
                    "recorded_by_user_id, created_at) VALUES "
                    "('pg-channel-cost', 125, 'pg-channel-cost-key', "
                    "'official.demo', 'OFFICIAL', now(), "
                    "'pg-channel-cost-external', NULL, NULL, NULL, "
                    "'immutable', 'RELAY', NULL, now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO ledger_entries "
                    "(id, company_id, kind, amount_cents, "
                    "available_delta_cents, reserved_delta_cents, "
                    "idempotency_key, task_id, note, created_at) VALUES "
                    "('pg-billing-ledger', 'pg-billing-migration-company', "
                    "'RECHARGE', 500, 500, 0, 'pg-billing-key', NULL, "
                    "'immutable', now())"
                )
            )

        for statement in (
            "UPDATE ledger_entries SET note = 'tampered'",
            "DELETE FROM ledger_entries",
            "TRUNCATE TABLE ledger_entries CASCADE",
        ):
            with pytest.raises(DBAPIError, match="ledger entries are immutable"):
                with migration_engine.begin() as connection:
                    connection.execute(text(statement))
        with migration_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM ledger_entries "
                        "WHERE id = 'pg-billing-ledger'"
                    )
                )
                == 1
            )

        for statement in (
            "UPDATE channel_cost_entries SET amount_cents = 126",
            "DELETE FROM channel_cost_entries",
            "TRUNCATE TABLE channel_cost_entries CASCADE",
        ):
            with pytest.raises(DBAPIError, match="channel cost entries are immutable"):
                with migration_engine.begin() as connection:
                    connection.execute(text(statement))
        with migration_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM channel_cost_entries "
                        "WHERE id = 'pg-channel-cost'"
                    )
                )
                == 1
            )

        command.downgrade(config, "0022_download_storage_binding")
        assert _revision(migration_engine) == "0022_download_storage_binding"
        assert not inspect(migration_engine).has_table(
            "download_gateway_registration_attempts"
        )
        command.upgrade(config, "head")
        assert _revision(migration_engine) == "0040_showcase_management"
        assert inspect(migration_engine).has_table(
            "download_gateway_registration_attempts"
        )

        command.downgrade(config, "0013_billing_invariants")
        assert _revision(migration_engine) == "0013_billing_invariants"
        assert not inspect(migration_engine).has_table("channel_cost_entries")
        assert not inspect(migration_engine).has_table(
            "download_gateway_registration_attempts"
        )
        assert "ix_ledger_kind_created" not in {
            item["name"]
            for item in inspect(migration_engine).get_indexes("ledger_entries")
        }
        assert _trigger_names(migration_engine) == {"trg_ledger_entries_immutable"}

        command.upgrade(config, "head")
        assert _revision(migration_engine) == "0040_showcase_management"
        assert inspect(migration_engine).has_table(
            "download_gateway_registration_attempts"
        )
        assert "trg_ledger_entries_no_truncate" in _trigger_names(migration_engine)
        assert _cost_trigger_names(migration_engine) == {
            "trg_channel_cost_entries_immutable",
            "trg_channel_cost_entries_no_truncate",
        }
        for table_name in (
            "task_artifacts",
            "download_records",
            "download_completions",
        ):
            assert f"trg_{table_name}_no_truncate" in (
                _artifact_audit_trigger_names(migration_engine, table_name)
            )
    finally:
        migration_engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()
