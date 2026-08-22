from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def _insert_download_record(
    engine,
    *,
    record_id: str,
    now: datetime,
    binding: bool = False,
    gateway: bool = False,
    partial_provider_only: bool = False,
    partial_gateway_ticket_only: bool = False,
    gateway_registration_request_id: str = (
        "11111111-1111-4111-8111-111111111111"
    ),
    source_sha256: str = "a" * 64,
    gateway_ticket_url_sha256: str = "b" * 64,
) -> None:
    relay_expires_at = now + timedelta(seconds=300)
    gateway_expires_at = now + timedelta(seconds=120)
    bound = binding or partial_gateway_ticket_only
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO download_records ("
                "id, company_id, task_id, asset_id, requested_by_user_id, "
                "expires_seconds, expires_at, request_id, "
                "storage_binding_version, storage_provider, "
                "storage_endpoint_host, storage_bucket, storage_object_key, "
                "storage_version_id, source_url_sha256, relay_issued_at, "
                "relay_expires_at, gateway_registration_request_id, "
                "gateway_ticket_id, gateway_ticket_url_sha256, "
                "gateway_issued_at, gateway_expires_at, "
                "gateway_transfer_reference, created_at"
                ") VALUES ("
                ":id, 'missing-company', 'missing-task', 'artifact-1', "
                "'missing-user', :expires_seconds, :expires_at, :request_id, "
                ":binding_version, :provider, :endpoint_host, :bucket, "
                ":object_key, NULL, :source_sha, :relay_issued_at, "
                ":relay_expires_at, :gateway_registration_request_id, "
                ":gateway_ticket_id, :gateway_ticket_url_sha256, "
                ":gateway_issued_at, :gateway_expires_at, "
                ":gateway_transfer_reference, :created_at)"
            ),
            {
                "id": record_id,
                "expires_seconds": 120 if gateway else 300,
                "expires_at": gateway_expires_at if gateway else relay_expires_at,
                "request_id": f"request-{record_id}",
                "binding_version": 1 if bound else None,
                "provider": (
                    "huawei_obs"
                    if bound or partial_provider_only
                    else None
                ),
                "endpoint_host": (
                    "obs.cn-north-4.myhuaweicloud.com" if bound else None
                ),
                "bucket": "relay-output-private" if bound else None,
                "object_key": "outputs/task/artifact-1.mp4" if bound else None,
                "source_sha": source_sha256 if bound else None,
                "relay_issued_at": now if bound else None,
                "relay_expires_at": relay_expires_at if bound else None,
                "gateway_registration_request_id": (
                    gateway_registration_request_id if gateway else None
                ),
                "gateway_ticket_id": (
                    "22222222-2222-4222-8222-222222222222"
                    if gateway or partial_gateway_ticket_only
                    else None
                ),
                "gateway_ticket_url_sha256": (
                    gateway_ticket_url_sha256 if gateway else None
                ),
                "gateway_issued_at": now if gateway else None,
                "gateway_expires_at": gateway_expires_at if gateway else None,
                "gateway_transfer_reference": (
                    "33333333-3333-4333-8333-333333333333"
                    if gateway
                    else None
                ),
                "created_at": now,
            },
        )


def _insert_gateway_attempt(
    engine,
    *,
    attempt_id: str,
    now: datetime,
    hash_overrides: dict[str, str | None] | None = None,
    status: str = "PENDING",
) -> None:
    hashes: dict[str, str | None] = {
        "artifact_sha256": "a" * 64,
        "source_url_sha256": "b" * 64,
        "body_sha256": "c" * 64,
        "response_sha256": None,
        "gateway_ticket_url_sha256": None,
        "reconciliation_ack_sha256": None,
    }
    hashes.update(hash_overrides or {})
    with engine.begin() as connection:
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
                ":status, 0, 0, "
                ":created_at, :updated_at)"
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
                "created_at": now,
                "updated_at": now,
                "status": status,
                **hashes,
            },
        )


def test_0022_preserves_history_and_rejects_partial_storage_bindings(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "download-storage-binding-0022.db"
    config = _config(project_root, database_path)
    command.upgrade(config, "0021_download_completion_proof")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO download_records ("
                "id, company_id, task_id, asset_id, requested_by_user_id, "
                "expires_seconds, expires_at, request_id, created_at"
                ") VALUES ("
                "'historical-download', 'missing-company', 'missing-task', "
                "'artifact-1', 'missing-user', 300, :expires_at, "
                "'historical-request', :created_at)"
            ),
            {
                "expires_at": now + timedelta(seconds=300),
                "created_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO task_artifacts ("
                "id, company_id, task_id, asset_id, position, media_type, "
                "content_type, size_bytes, sha256, created_at"
                ") VALUES ("
                "'historical-positive-artifact', 'missing-company', "
                "'missing-task', 'historical-artifact', 0, 'video', "
                "'video/mp4', 1, :sha256, :created_at)"
            ),
            {"sha256": "0" * 64, "created_at": now},
        )
    engine.dispose()

    command.upgrade(config, "0022_download_storage_binding")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0022_download_storage_binding"
        assert "download_gateway_registration_attempts" not in inspect(
            engine
        ).get_table_names()
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0040_showcase_management"
        historical = connection.execute(
            text(
                "SELECT storage_binding_version, storage_provider, "
                "gateway_ticket_id FROM download_records "
                "WHERE id = 'historical-download'"
            )
        ).one()
        assert historical == (None, None, None)
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("download_records")
        }
        assert {
            "storage_binding_version",
            "storage_endpoint_host",
            "storage_bucket",
            "storage_object_key",
            "source_url_sha256",
            "relay_issued_at",
            "relay_expires_at",
            "gateway_registration_request_id",
            "gateway_ticket_id",
            "gateway_ticket_url_sha256",
            "gateway_issued_at",
            "gateway_expires_at",
            "gateway_transfer_reference",
        } <= columns
        assert "source_url" not in columns
        attempt_columns = {
            column["name"]: column
            for column in inspect(engine).get_columns(
                "download_gateway_registration_attempts"
            )
        }
        assert set(attempt_columns) == {
            "id",
            "company_id",
            "task_id",
            "asset_id",
            "requested_by_user_id",
            "platform_request_id",
            "registration_request_id",
            "download_record_id",
            "transfer_reference",
            "expected_size_bytes",
            "artifact_sha256",
            "storage_provider",
            "storage_endpoint_host",
            "storage_bucket",
            "storage_object_key",
            "source_url_sha256",
            "relay_issued_at",
            "relay_expires_at",
            "body_sha256",
            "request_ciphertext",
            "request_nonce",
            "response_sha256",
            "response_ciphertext",
            "response_nonce",
            "gateway_ticket_id",
            "gateway_ticket_url_sha256",
            "gateway_issued_at",
            "gateway_expires_at",
            "gateway_expires_seconds",
            "status",
            "attempt_count",
            "next_attempt_at",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "last_error_code",
            "ticket_replay_count",
            "ticket_replayed_at",
            "response_destroy_after",
            "reconciliation_ack_sha256",
            "reconciled_at",
            "registered_at",
            "attached_at",
            "dead_at",
            "created_at",
            "updated_at",
        }
        assert attempt_columns["ticket_replay_count"]["type"].python_type is int
        attempt_indexes = {
            item["name"]
            for item in inspect(engine).get_indexes(
                "download_gateway_registration_attempts"
            )
        }
        assert {
            "ix_download_gateway_attempt_dispatch",
            "ix_download_gateway_attempt_company_created",
            "ix_download_gateway_registration_attempts_company_id",
            "ix_download_gateway_registration_attempts_task_id",
            "ix_download_gateway_registration_attempts_requested_by_user_id",
        } <= attempt_indexes
        assert {
            item["name"]
            for item in inspect(engine).get_unique_constraints(
                "download_gateway_registration_attempts"
            )
        } == {
            "uq_download_gateway_attempt_request",
            "uq_download_gateway_attempt_registration",
            "uq_download_gateway_attempt_record",
            "uq_download_gateway_attempt_transfer",
        }
        assert {
            item["name"]
            for item in inspect(engine).get_check_constraints(
                "download_gateway_registration_attempts"
            )
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
            for item in inspect(engine).get_foreign_keys(
                "download_gateway_registration_attempts"
            )
        } == {
            (("company_id",), "companies"),
            (("task_id",), "generation_tasks"),
            (("requested_by_user_id",), "users"),
        }
        assert connection.scalar(
            text(
                "SELECT size_bytes FROM task_artifacts "
                "WHERE id = 'historical-positive-artifact'"
            )
        ) == 1

    with pytest.raises(
        IntegrityError,
        match="task artifact size must be positive",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO task_artifacts ("
                    "id, company_id, task_id, asset_id, position, media_type, "
                    "content_type, size_bytes, sha256, created_at"
                    ") VALUES ("
                    "'new-empty-artifact', 'missing-company', 'missing-task', "
                    "'new-empty-artifact', 1, 'video', 'video/mp4', 0, "
                    ":sha256, :created_at)"
                ),
                {"sha256": "1" * 64, "created_at": now},
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO task_artifacts ("
                "id, company_id, task_id, asset_id, position, media_type, "
                "content_type, size_bytes, sha256, created_at"
                ") VALUES ("
                "'new-positive-artifact', 'missing-company', 'missing-task', "
                "'new-positive-artifact', 2, 'video', 'video/mp4', 1, "
                ":sha256, :created_at)"
            ),
            {"sha256": "2" * 64, "created_at": now},
        )

    with pytest.raises(
        IntegrityError,
        match="download storage binding is incomplete",
    ):
        _insert_download_record(
            engine,
            record_id="partial-provider",
            now=now,
            partial_provider_only=True,
        )
    with pytest.raises(
        IntegrityError,
        match="download storage binding is incomplete",
    ):
        _insert_download_record(
            engine,
            record_id="partial-gateway",
            now=now,
            partial_gateway_ticket_only=True,
        )

    for record_id, source_sha256 in (
        ("uppercase-source-sha", "A" * 64),
        ("nonhex-source-sha", "g" * 64),
    ):
        with pytest.raises(
            IntegrityError,
            match="download storage binding is incomplete",
        ):
            _insert_download_record(
                engine,
                record_id=record_id,
                now=now,
                binding=True,
                source_sha256=source_sha256,
            )
    for record_id, gateway_ticket_url_sha256 in (
        ("uppercase-gateway-sha", "B" * 64),
        ("nonhex-gateway-sha", "z" * 64),
    ):
        with pytest.raises(
            IntegrityError,
            match="download storage binding is incomplete",
        ):
            _insert_download_record(
                engine,
                record_id=record_id,
                now=now,
                binding=True,
                gateway=True,
                gateway_ticket_url_sha256=gateway_ticket_url_sha256,
            )

    _insert_gateway_attempt(
        engine,
        attempt_id="valid-nullable-hashes",
        now=now,
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
            with pytest.raises(IntegrityError):
                _insert_gateway_attempt(
                    engine,
                    attempt_id=f"invalid-{field_name}-{label}",
                    now=now,
                    hash_overrides={field_name: invalid_hash},
                )

    _insert_download_record(
        engine,
        record_id="complete-binding",
        now=now,
        binding=True,
    )
    _insert_download_record(
        engine,
        record_id="complete-gateway",
        now=now,
        binding=True,
        gateway=True,
    )
    with pytest.raises(IntegrityError):
        _insert_download_record(
            engine,
            record_id="duplicate-registration",
            now=now,
            binding=True,
            gateway=True,
        )

    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM download_records WHERE id IN "
                "('complete-binding', 'complete-gateway')"
            )
        ) == 2
    engine.dispose()

    command.downgrade(config, "0022_download_storage_binding")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0022_download_storage_binding"
        assert "download_gateway_registration_attempts" not in inspect(
            engine
        ).get_table_names()
    engine.dispose()

    command.downgrade(config, "0021_download_completion_proof")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0021_download_completion_proof"
        assert connection.scalar(
            text(
                "SELECT count(*) FROM download_records "
                "WHERE id = 'historical-download'"
            )
        ) == 1
        assert "download_gateway_registration_attempts" not in inspect(
            engine
        ).get_table_names()
    engine.dispose()
