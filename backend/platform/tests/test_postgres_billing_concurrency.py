from __future__ import annotations

import os
from datetime import timedelta
from threading import Barrier, Event, Lock, Thread
import time
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from platform_api.database import Base
from platform_api.models import (
    AuditLog,
    ChannelCostEntry,
    ChannelCostSource,
    ChannelType,
    Company,
    CompanyMembership,
    CompanyModelGrant,
    CompanyResourceGrant,
    DownloadCompletion,
    DownloadCompletionSource,
    DownloadRecord,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    MembershipStatus,
    ModelCapability,
    ModelDefinition,
    ResourceDefinition,
    ResourceKind,
    RelayOutboxStatus,
    RelayChannelOperationJournal,
    RelaySubmissionOutbox,
    TaskArtifact,
    TaskStatus,
    User,
    WalletAccount,
    utcnow,
)
from platform_api.services.billing import WalletService
from platform_api.services.channel_costs import ChannelCostService
from platform_api.services.errors import (
    ConflictError,
    InsufficientBalanceError,
    NotFoundError,
    PermissionDeniedError,
)
from platform_api.services.models import ModelCatalogService, ModelGrantService
from platform_api.services.reports import DownloadCompletionService
from platform_api.services.relay_channel_operations import (
    RelayChannelOperationConflict,
    RelayChannelOperationJournalService,
)
from platform_api.services.tasks import TaskService
from platform_api.services.task_cancellation import GenerationCancellationService

from .test_model_capability_v1_contract import _mode, canonical_capability

DATABASE_URL = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


@pytest.fixture
def postgres_session_factory():
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("requires a PostgreSQL test database")

    schema_name = f"billing_concurrency_{uuid.uuid4().hex}"
    assert schema_name.startswith("billing_concurrency_")
    administration_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with administration_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')

    engine = create_engine(
        DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema_name}"},
        pool_size=8,
        max_overflow=0,
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        with administration_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema_name}" CASCADE')
        administration_engine.dispose()


def _seed_company(factory, suffix: str, *, balance_cents: int):
    with factory.begin() as session:
        company = Company(name=f"Concurrency {suffix}")
        first_user = User(
            email=f"{suffix}-one@example.com", display_name=f"{suffix} One"
        )
        second_user = User(
            email=f"{suffix}-two@example.com", display_name=f"{suffix} Two"
        )
        model = ModelDefinition(
            slug=f"concurrency-{suffix}",
            display_name=f"Concurrency {suffix}",
            provider_key="postgres-test",
            billing_mode="per_item",
        )
        session.add_all([company, first_user, second_user, model])
        session.flush()
        session.add(
            WalletAccount(
                company_id=company.id,
                available_cents=balance_cents,
                reserved_cents=0,
            )
        )
        return company.id, first_user.id, second_user.id, model.id


def _draft_task(
    factory,
    *,
    company_id: str,
    user_id: str,
    model_id: str,
    suffix: str,
    quote_cents: int,
) -> str:
    with factory.begin() as session:
        task = GenerationTask(
            company_id=company_id,
            user_id=user_id,
            model_id=model_id,
            idempotency_key=f"task-{suffix}",
            request_fingerprint=(suffix * 64)[:64],
            status=TaskStatus.DRAFT,
            request_payload={"output_count": 1},
            quote_cents=quote_cents,
            pricing_snapshot={
                "mode": "per_item",
                "unit_price_cents": quote_cents,
                "quantity": 1,
                "quote_cents": quote_cents,
            },
            capability_snapshot={},
            reserved_cents=0,
        )
        session.add(task)
        session.flush()
        return task.id


def _run_concurrently(*operations):
    barrier = Barrier(len(operations))
    result_lock = Lock()
    results: list[tuple[str, object]] = []

    def run(operation):
        barrier.wait(timeout=10)
        try:
            value = operation()
            outcome: tuple[str, object] = ("ok", value)
        except Exception as error:  # asserted by concrete exception type below
            outcome = ("error", error)
        with result_lock:
            results.append(outcome)

    threads = [Thread(target=run, args=(operation,)) for operation in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "PostgreSQL billing operation deadlocked"
    return results


def test_relay_channel_operation_claim_is_tenant_global_and_atomic_on_postgres(
    postgres_session_factory,
):
    factory = postgres_session_factory
    tenant_id = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
    operation_id = "postgres-relay-channel-operation-0001"
    with factory.begin() as session:
        actor = User(
            email=f"relay-journal-{uuid.uuid4()}@example.com",
            display_name="Relay Journal Admin",
            is_platform_admin=True,
        )
        session.add(actor)
        session.flush()
        actor_id = actor.id

    def claim(channel_id: int, reason: str):
        with factory() as session:
            row, created = RelayChannelOperationJournalService.claim(
                session,
                tenant_id=tenant_id,
                operation_id=operation_id,
                channel_id=channel_id,
                kind="test",
                actor_user_id=actor_id,
                reason=reason,
                expected_revision=None,
                target_status=None,
                before_summary={"id": channel_id, "status": "enabled"},
                approval_proof={
                    "operation_id": operation_id,
                    "channel_id": channel_id,
                    "kind": "test",
                    "actor": actor_id,
                    "reason": reason,
                    "approved": True,
                },
                request_id=f"relay-journal-{channel_id}",
            )
            session.commit()
            return row.id, created

    results = _run_concurrently(
        lambda: claim(17, "Verify channel seventeen"),
        lambda: claim(18, "Verify channel eighteen"),
    )
    assert len(results) == 2
    assert sum(kind == "ok" for kind, _ in results) == 1
    errors = [value for kind, value in results if kind == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], RelayChannelOperationConflict)

    with factory() as session:
        rows = session.scalars(select(RelayChannelOperationJournal)).all()
        assert len(rows) == 1
        assert rows[0].operation_id == operation_id
        approval_count = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "relay.channel.test.approve")
        )
        assert approval_count == 1


def test_relay_channel_operation_same_intent_replays_once_on_postgres(
    postgres_session_factory,
):
    factory = postgres_session_factory
    tenant_id = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
    operation_id = "postgres-relay-channel-operation-0002"
    with factory.begin() as session:
        actor = User(
            email=f"relay-journal-replay-{uuid.uuid4()}@example.com",
            display_name="Relay Journal Replay Admin",
            is_platform_admin=True,
        )
        session.add(actor)
        session.flush()
        actor_id = actor.id

    def claim():
        with factory() as session:
            row, created = RelayChannelOperationJournalService.claim(
                session,
                tenant_id=tenant_id,
                operation_id=operation_id,
                channel_id=17,
                kind="test",
                actor_user_id=actor_id,
                reason="Verify the same channel once",
                expected_revision=None,
                target_status=None,
                before_summary={"id": 17, "status": "enabled"},
                approval_proof={
                    "operation_id": operation_id,
                    "channel_id": 17,
                    "kind": "test",
                    "actor": actor_id,
                    "reason": "Verify the same channel once",
                    "approved": True,
                },
                request_id="relay-journal-replay",
            )
            session.commit()
            return row.id, created

    results = _run_concurrently(claim, claim)
    assert all(kind == "ok" for kind, _ in results)
    values = [value for _, value in results]
    assert len({value[0] for value in values}) == 1
    assert sorted(value[1] for value in values) == [False, True]

    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(RelayChannelOperationJournal)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "relay.channel.test.approve")
            )
            == 1
        )


def _seed_download_completion_records(
    factory,
    *,
    suffix: str,
    record_count: int,
) -> dict[str, object]:
    company_id, user_id, _, model_id = _seed_company(factory, suffix, balance_cents=0)
    task_id = _draft_task(
        factory,
        company_id=company_id,
        user_id=user_id,
        model_id=model_id,
        suffix=f"{suffix}-task",
        quote_cents=125,
    )
    issued_at = utcnow()
    asset_id = str(uuid.uuid4())
    record_ids = [str(uuid.uuid4()) for _ in range(record_count)]
    with factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        task.status = TaskStatus.SUCCEEDED
        task.actual_cost_cents = 125
        task.output_artifacts = [
            {
                "asset_id": asset_id,
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 4_096,
                "sha256": "a" * 64,
            }
        ]
        session.add(
            TaskArtifact(
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                position=0,
                media_type="video",
                content_type="video/mp4",
                size_bytes=4_096,
                sha256="a" * 64,
                created_at=issued_at,
            )
        )
        session.add_all(
            [
                DownloadRecord(
                    id=record_id,
                    company_id=company_id,
                    task_id=task_id,
                    asset_id=asset_id,
                    requested_by_user_id=user_id,
                    expires_seconds=300,
                    expires_at=issued_at + timedelta(seconds=300),
                    request_id=f"postgres-{suffix}-issue-{position}",
                    storage_binding_version=1,
                    storage_provider="huawei_obs",
                    storage_endpoint_host=("obs.cn-north-4.myhuaweicloud.com"),
                    storage_bucket="postgres-artifact-bucket",
                    storage_object_key=(f"tasks/{task_id}/{asset_id}"),
                    source_url_sha256="d" * 64,
                    relay_issued_at=issued_at,
                    relay_expires_at=(issued_at + timedelta(seconds=300)),
                    created_at=issued_at,
                )
                for position, record_id in enumerate(record_ids)
            ]
        )
    return {
        "company_id": company_id,
        "user_id": user_id,
        "task_id": task_id,
        "asset_id": asset_id,
        "record_ids": record_ids,
        "issued_at": issued_at,
        "completed_at": issued_at + timedelta(seconds=1),
    }


def _confirm_download_completion(
    factory,
    target: dict[str, object],
    *,
    record_id: str,
    external_event_id: str,
    signed_event_id: str,
    signed_payload_sha256: str,
    source_event_id: str,
) -> tuple[str, bool]:
    with factory.begin() as session:
        completion, created = DownloadCompletionService.confirm(
            session,
            download_record_id=record_id,
            company_id=str(target["company_id"]),
            task_id=str(target["task_id"]),
            asset_id=str(target["asset_id"]),
            external_event_id=external_event_id,
            source=DownloadCompletionSource.OBS_ACCESS_LOG,
            bytes_sent=4_096,
            completed_at=target["completed_at"],
            artifact_sha256="a" * 64,
            expected_size_bytes=4_096,
            http_status=200,
            transfer_scope="full_body",
            source_evidence={
                "obs_bucket": "postgres-artifact-bucket",
                "obs_object_key": (f"tasks/{target['task_id']}/{target['asset_id']}"),
                "obs_request_id": source_event_id,
            },
            signed_event_id=signed_event_id,
            signed_event_timestamp=target["issued_at"],
            signed_payload_sha256=signed_payload_sha256,
        )
        return completion.id, created


def test_postgres_wallet_row_lock_prevents_two_employee_overdraft(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, first_user_id, second_user_id, model_id = _seed_company(
        factory, "reserve", balance_cents=500
    )
    first_task_id = _draft_task(
        factory,
        company_id=company_id,
        user_id=first_user_id,
        model_id=model_id,
        suffix="reserve-one",
        quote_cents=400,
    )
    second_task_id = _draft_task(
        factory,
        company_id=company_id,
        user_id=second_user_id,
        model_id=model_id,
        suffix="reserve-two",
        quote_cents=400,
    )

    def reserve(task_id: str, key: str):
        def operation():
            with factory.begin() as session:
                return WalletService.reserve(
                    session,
                    company_id=company_id,
                    task_id=task_id,
                    amount_cents=400,
                    idempotency_key=key,
                )[1].id

        return operation

    results = _run_concurrently(
        reserve(first_task_id, "reserve-ledger-one"),
        reserve(second_task_id, "reserve-ledger-two"),
    )
    successes = [value for status, value in results if status == "ok"]
    errors = [value for status, value in results if status == "error"]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], InsufficientBalanceError)

    with factory() as session:
        wallet = session.get(WalletAccount, company_id)
        assert (wallet.available_cents, wallet.reserved_cents) == (100, 400)
        tasks = list(
            session.scalars(
                select(GenerationTask).where(
                    GenerationTask.id.in_([first_task_id, second_task_id])
                )
            ).all()
        )
        assert sorted(task.status for task in tasks) == [
            TaskStatus.DRAFT,
            TaskStatus.QUEUED,
        ]
        assert (
            session.scalar(
                select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.company_id == company_id,
                    LedgerEntry.kind == LedgerKind.RESERVE,
                )
            )
            == 1
        )


def test_postgres_cancel_and_dispatch_claim_have_one_safe_winner(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, user_id, _, model_id = _seed_company(
        factory, "cancel-claim", balance_cents=1000
    )
    task_id = _draft_task(
        factory,
        company_id=company_id,
        user_id=user_id,
        model_id=model_id,
        suffix="cancel-claim-task",
        quote_cents=400,
    )
    with factory.begin() as session:
        WalletService.reserve(
            session,
            company_id=company_id,
            task_id=task_id,
            amount_cents=400,
            idempotency_key="cancel-claim-reserve",
        )
        session.add(
            RelaySubmissionOutbox(
                company_id=company_id,
                task_id=task_id,
                status=RelayOutboxStatus.PENDING,
                idempotency_key="cancel-claim-outbox",
                relay_payload={},
                attempt_count=0,
                next_attempt_at=utcnow(),
            )
        )

    def cancel():
        with factory.begin() as session:
            return GenerationCancellationService.cancel_unsubmitted(
                session,
                company_id=company_id,
                task_id=task_id,
                actor_user_id=user_id,
            ).task.status

    def claim():
        with factory.begin() as session:
            outbox = session.scalar(
                select(RelaySubmissionOutbox)
                .where(RelaySubmissionOutbox.task_id == task_id)
                .with_for_update(skip_locked=True)
            )
            if outbox is None or outbox.status != RelayOutboxStatus.PENDING:
                return "not_claimed"
            outbox.status = RelayOutboxStatus.PROCESSING
            outbox.attempt_count += 1
            return "claimed"

    results = _run_concurrently(cancel, claim)
    assert len(results) == 2
    with factory() as session:
        task = session.get(GenerationTask, task_id)
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        )
        wallet = session.get(WalletAccount, company_id)
        assert task is not None and outbox is not None and wallet is not None
        if task.status == TaskStatus.CANCELLED:
            assert outbox.status == RelayOutboxStatus.CANCELLED
            assert (wallet.available_cents, wallet.reserved_cents) == (1000, 0)
        else:
            assert task.status == TaskStatus.QUEUED
            assert outbox.status == RelayOutboxStatus.PROCESSING
            assert (wallet.available_cents, wallet.reserved_cents) == (600, 400)


def test_postgres_operator_first_channel_cost_race_never_false_acks_relay(
    postgres_session_factory,
):
    factory = postgres_session_factory
    _, operator_user_id, _, _ = _seed_company(
        factory,
        "channel-cost-race",
        balance_cents=0,
    )
    idempotency_key = "postgres-channel-cost-operator-relay-race"
    relay_event_id = str(uuid.uuid4())
    occurred_at = utcnow()
    operator_inserted = Event()
    relay_entering_insert = Event()

    common_values = {
        "amount_cents": 37,
        "idempotency_key": idempotency_key,
        "channel_key": "official.concurrent-route",
        "channel_type": ChannelType.OFFICIAL,
        "occurred_at": occurred_at,
        "external_reference": "postgres-provider-charge-race",
        "note": "operator and Relay race",
    }

    def operator_write():
        with factory.begin() as session:
            entry, created = ChannelCostService.create(
                session,
                **common_values,
                source=ChannelCostSource.PLATFORM_ADMIN,
                recorded_by_user_id=operator_user_id,
            )
            assert created
            operator_inserted.set()
            assert relay_entering_insert.wait(timeout=10)
            # Keep the unique-key insert uncommitted until the Relay transaction
            # has reached its competing insert and is waiting on PostgreSQL.
            time.sleep(0.05)
            return entry.id

    def relay_write():
        assert operator_inserted.wait(timeout=10)
        relay_entering_insert.set()
        with factory.begin() as session:
            return ChannelCostService.create(
                session,
                **common_values,
                relay_event_id=relay_event_id,
                relay_event_timestamp=utcnow(),
                relay_payload_sha256="a" * 64,
                evidence_source="provider_reported",
                source=ChannelCostSource.RELAY,
                recorded_by_user_id=None,
            )[0].id

    results = _run_concurrently(operator_write, relay_write)
    successes = [value for status, value in results if status == "ok"]
    errors = [value for status, value in results if status == "error"]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ConflictError)
    assert "unsigned channel cost entry" in str(errors[0])

    with factory() as session:
        entries = list(
            session.scalars(
                select(ChannelCostEntry).where(
                    ChannelCostEntry.idempotency_key == idempotency_key
                )
            )
        )
        assert len(entries) == 1
        entry = entries[0]
        assert entry.source == ChannelCostSource.PLATFORM_ADMIN
        assert entry.relay_event_id is None
        assert entry.relay_event_timestamp is None
        assert entry.relay_payload_sha256 is None


def test_postgres_terminal_race_and_recharge_replay_are_single_effect(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, first_user_id, _, model_id = _seed_company(
        factory, "terminal", balance_cents=500
    )
    task_id = _draft_task(
        factory,
        company_id=company_id,
        user_id=first_user_id,
        model_id=model_id,
        suffix="terminal-task",
        quote_cents=400,
    )
    with factory.begin() as session:
        WalletService.reserve(
            session,
            company_id=company_id,
            task_id=task_id,
            amount_cents=400,
            idempotency_key="terminal-reserve",
        )

    def settle():
        with factory.begin() as session:
            return WalletService.settle_success(
                session,
                company_id=company_id,
                task_id=task_id,
                actual_cost_cents=350,
                idempotency_key="terminal-settle",
            )[1].id

    def release():
        with factory.begin() as session:
            return WalletService.release_failure(
                session,
                company_id=company_id,
                task_id=task_id,
                idempotency_key="terminal-release",
                failure_reason="concurrent provider failure",
            )[1].id

    terminal_results = _run_concurrently(settle, release)
    assert sum(status == "ok" for status, _ in terminal_results) == 1
    terminal_errors = [value for status, value in terminal_results if status == "error"]
    assert len(terminal_errors) == 1
    assert isinstance(terminal_errors[0], ConflictError)

    with factory() as session:
        task = session.get(GenerationTask, task_id)
        wallet = session.get(WalletAccount, company_id)
        terminal_entries = list(
            session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.task_id == task_id,
                    LedgerEntry.kind.in_([LedgerKind.SETTLE, LedgerKind.RELEASE]),
                )
            ).all()
        )
        assert len(terminal_entries) == 1
        assert wallet.reserved_cents == 0
        if task.status == TaskStatus.SUCCEEDED:
            assert wallet.available_cents == 150
            assert task.actual_cost_cents == 350
            assert terminal_entries[0].kind == LedgerKind.SETTLE
        else:
            assert task.status == TaskStatus.FAILED
            assert wallet.available_cents == 500
            assert task.actual_cost_cents is None
            assert terminal_entries[0].kind == LedgerKind.RELEASE

    recharge_company_id, _, _, _ = _seed_company(factory, "recharge", balance_cents=0)

    def recharge():
        with factory.begin() as session:
            _, entry, created = WalletService.recharge(
                session,
                company_id=recharge_company_id,
                amount_cents=250,
                idempotency_key="concurrent-recharge-key",
                note="same request replay",
            )
            return entry.id, created

    recharge_results = _run_concurrently(recharge, recharge)
    assert all(status == "ok" for status, _ in recharge_results)
    values = [value for _, value in recharge_results]
    assert len({entry_id for entry_id, _ in values}) == 1
    assert sorted(created for _, created in values) == [False, True]
    with factory() as session:
        wallet = session.get(WalletAccount, recharge_company_id)
        assert (wallet.available_cents, wallet.reserved_cents) == (250, 0)
        assert (
            session.scalar(
                select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.company_id == recharge_company_id,
                    LedgerEntry.kind == LedgerKind.RECHARGE,
                )
            )
            == 1
        )


def test_postgres_concurrent_download_completion_replays_one_winner(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, user_id, _, model_id = _seed_company(
        factory, "download-completion", balance_cents=0
    )
    task_id = _draft_task(
        factory,
        company_id=company_id,
        user_id=user_id,
        model_id=model_id,
        suffix="download-completion-task",
        quote_cents=125,
    )
    issued_at = utcnow()
    completed_at = issued_at + timedelta(seconds=1)
    signed_event_timestamp = issued_at
    signed_event_id = "55555555-5555-4555-8555-555555555555"
    asset_id = str(uuid.uuid4())
    record_id = str(uuid.uuid4())
    with factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        task.status = TaskStatus.SUCCEEDED
        task.actual_cost_cents = 125
        task.output_artifacts = [
            {
                "asset_id": asset_id,
                "media_type": "video",
                "content_type": "video/mp4",
                "size_bytes": 4_096,
                "sha256": "a" * 64,
            }
        ]
        session.add(
            TaskArtifact(
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                position=0,
                media_type="video",
                content_type="video/mp4",
                size_bytes=4_096,
                sha256="a" * 64,
                created_at=issued_at,
            )
        )
        session.add(
            DownloadRecord(
                id=record_id,
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                requested_by_user_id=user_id,
                expires_seconds=300,
                expires_at=issued_at + timedelta(seconds=300),
                request_id="postgres-download-issue",
                storage_binding_version=1,
                storage_provider="huawei_obs",
                storage_endpoint_host=("obs.cn-north-4.myhuaweicloud.com"),
                storage_bucket="postgres-artifact-bucket",
                storage_object_key=f"tasks/{task_id}/{asset_id}",
                source_url_sha256="d" * 64,
                relay_issued_at=issued_at,
                relay_expires_at=issued_at + timedelta(seconds=300),
                created_at=issued_at,
            )
        )

    def confirm():
        with factory.begin() as session:
            completion, created = DownloadCompletionService.confirm(
                session,
                download_record_id=record_id,
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                external_event_id="postgres-concurrent-download-event",
                source=DownloadCompletionSource.OBS_ACCESS_LOG,
                bytes_sent=4_096,
                completed_at=completed_at,
                artifact_sha256="a" * 64,
                expected_size_bytes=4_096,
                http_status=200,
                transfer_scope="full_body",
                source_evidence={
                    "obs_bucket": "postgres-artifact-bucket",
                    "obs_object_key": f"tasks/{task_id}/{asset_id}",
                    "obs_request_id": "postgres-obs-request",
                },
                signed_event_id=signed_event_id,
                signed_event_timestamp=signed_event_timestamp,
                signed_payload_sha256="b" * 64,
            )
            return completion.id, created

    results = _run_concurrently(confirm, confirm)
    assert all(status == "ok" for status, _ in results), results
    values = [value for _, value in results]
    assert len({completion_id for completion_id, _ in values}) == 1
    assert sorted(created for _, created in values) == [False, True]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(DownloadCompletion.id)).where(
                    DownloadCompletion.download_record_id == record_id
                )
            )
            == 1
        )
        completion = session.scalar(
            select(DownloadCompletion).where(
                DownloadCompletion.download_record_id == record_id
            )
        )
        assert completion.verification_version == 1
        assert completion.signed_event_id == signed_event_id


def test_postgres_different_signed_events_race_for_one_download_record(
    postgres_session_factory,
):
    factory = postgres_session_factory
    target = _seed_download_completion_records(
        factory,
        suffix="download-event-record-race",
        record_count=1,
    )
    record_id = target["record_ids"][0]
    candidates = (
        {
            "external_event_id": "postgres-record-race-event-a",
            "signed_event_id": "66666666-6666-4666-8666-666666666666",
            "signed_payload_sha256": "b" * 64,
            "source_event_id": "postgres-record-race-obs-a",
        },
        {
            "external_event_id": "postgres-record-race-event-b",
            "signed_event_id": "77777777-7777-4777-8777-777777777777",
            "signed_payload_sha256": "c" * 64,
            "source_event_id": "postgres-record-race-obs-b",
        },
    )

    def confirm(candidate):
        return lambda: _confirm_download_completion(
            factory,
            target,
            record_id=record_id,
            **candidate,
        )

    results = _run_concurrently(*(confirm(candidate) for candidate in candidates))
    successes = [value for status, value in results if status == "ok"]
    failures = [value for status, value in results if status == "error"]
    assert len(successes) == 1, results
    assert successes[0][1] is True
    assert len(failures) == 1, results
    assert isinstance(failures[0], ConflictError), results

    with factory() as session:
        completions = list(
            session.scalars(
                select(DownloadCompletion).where(
                    DownloadCompletion.download_record_id == record_id
                )
            ).all()
        )
    assert len(completions) == 1
    assert completions[0].signed_event_id in {
        candidate["signed_event_id"] for candidate in candidates
    }
    assert completions[0].external_event_id in {
        candidate["external_event_id"] for candidate in candidates
    }


def test_postgres_same_signed_event_races_across_download_records(
    postgres_session_factory,
):
    factory = postgres_session_factory
    target = _seed_download_completion_records(
        factory,
        suffix="download-signed-event-race",
        record_count=2,
    )
    record_ids = target["record_ids"]
    signed_event_id = "88888888-8888-4888-8888-888888888888"
    candidates = (
        {
            "record_id": record_ids[0],
            "external_event_id": "postgres-signed-race-event-a",
            "signed_payload_sha256": "d" * 64,
            "source_event_id": "postgres-signed-race-obs-a",
        },
        {
            "record_id": record_ids[1],
            "external_event_id": "postgres-signed-race-event-b",
            "signed_payload_sha256": "e" * 64,
            "source_event_id": "postgres-signed-race-obs-b",
        },
    )

    def confirm(candidate):
        return lambda: _confirm_download_completion(
            factory,
            target,
            signed_event_id=signed_event_id,
            **candidate,
        )

    results = _run_concurrently(*(confirm(candidate) for candidate in candidates))
    successes = [value for status, value in results if status == "ok"]
    failures = [value for status, value in results if status == "error"]
    assert len(successes) == 1, results
    assert successes[0][1] is True
    assert len(failures) == 1, results
    assert isinstance(failures[0], ConflictError), results

    with factory() as session:
        completions = list(
            session.scalars(
                select(DownloadCompletion).where(
                    DownloadCompletion.download_record_id.in_(record_ids)
                )
            ).all()
        )
    assert len(completions) == 1
    assert completions[0].signed_event_id == signed_event_id
    assert completions[0].download_record_id in set(record_ids)
    assert completions[0].external_event_id in {
        candidate["external_event_id"] for candidate in candidates
    }


def test_postgres_model_mode_and_first_grant_race_stays_consistent(
    postgres_session_factory,
):
    factory = postgres_session_factory
    with factory.begin() as session:
        company = Company(name="Billing Mode Race")
        model = ModelDefinition(
            slug="billing-mode-race",
            display_name="Billing Mode Race",
            provider_key="postgres-test",
            billing_mode="per_second",
            active=False,
            published_at=None,
        )
        session.add_all([company, model])
        session.flush()
        company_id = company.id
        model_id = model.id

    def change_mode():
        with factory.begin() as session:
            _, updated, _ = ModelCatalogService.update_model(
                session,
                model_id=model_id,
                display_name="Billing Mode Race",
                provider_key="postgres-test",
                billing_mode="per_item",
                expected_capability_version=1,
                capabilities=[],
            )
            return updated.billing_mode

    def create_first_grant():
        with factory.begin() as session:
            grant = ModelGrantService.upsert_grant(
                session,
                company_id=company_id,
                model_id=model_id,
                enabled=False,
                price_per_second_cents=25,
                price_per_item_cents=None,
                config_override={},
            )
            return grant.id

    results = _run_concurrently(change_mode, create_first_grant)
    assert sum(status == "ok" for status, _ in results) == 1
    errors = [value for status, value in results if status == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], ConflictError)

    with factory() as session:
        model = session.get(ModelDefinition, model_id)
        grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.model_id == model_id,
            )
        )
        if grant is None:
            assert model.billing_mode == "per_item"
            assert model.capability_version == 2
        else:
            assert model.billing_mode == "per_second"
            assert grant.price_per_second_cents == 25
            assert grant.price_per_item_cents is None


def test_postgres_model_update_rejects_stale_full_replacement(
    postgres_session_factory,
):
    factory = postgres_session_factory
    with factory.begin() as session:
        model = ModelDefinition(
            slug="model-edit-race",
            display_name="Original Model",
            provider_key="provider-original",
            billing_mode="per_second",
            active=False,
            published_at=None,
        )
        session.add(model)
        session.flush()
        model_id = model.id

    def update(display_name: str, provider_key: str):
        def operation():
            with factory.begin() as session:
                _, changed_model, _ = ModelCatalogService.update_model(
                    session,
                    model_id=model_id,
                    display_name=display_name,
                    provider_key=provider_key,
                    billing_mode="per_second",
                    expected_capability_version=1,
                    capabilities=[],
                )
                return changed_model.capability_version

        return operation

    results = _run_concurrently(
        update("First Edit", "provider-first"),
        update("Second Edit", "provider-second"),
    )
    assert sum(status == "ok" for status, _ in results) == 1
    errors = [value for status, value in results if status == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], ConflictError)

    with factory() as session:
        model = session.get(ModelDefinition, model_id)
        assert model.capability_version == 2
        assert (model.display_name, model.provider_key) in {
            ("First Edit", "provider-first"),
            ("Second Edit", "provider-second"),
        }


def test_postgres_model_delete_races_are_serialized(
    postgres_session_factory,
):
    factory = postgres_session_factory
    with factory.begin() as session:
        publish_model = ModelDefinition(
            slug="publish-delete-race",
            display_name="Publish Delete Race",
            provider_key="postgres-test",
            billing_mode="per_second",
            active=False,
            published_at=None,
        )
        session.add(publish_model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=publish_model.id,
                capability_key="generation",
                config=canonical_capability(
                    modes={"text_to_video": _mode(output_counts=[1])}
                ),
            )
        )
        publish_model_id = publish_model.id

    def publish():
        with factory.begin() as session:
            return ModelCatalogService.publish(session, model_id=publish_model_id)[1].id

    def delete_publish_model():
        with factory.begin() as session:
            return ModelCatalogService.delete_draft(session, model_id=publish_model_id)[
                "id"
            ]

    publish_delete_results = _run_concurrently(publish, delete_publish_model)
    assert sum(status == "ok" for status, _ in publish_delete_results) == 1
    publish_delete_errors = [
        value for status, value in publish_delete_results if status == "error"
    ]
    assert len(publish_delete_errors) == 1
    assert isinstance(publish_delete_errors[0], (ConflictError, NotFoundError))
    with factory() as session:
        remaining = session.get(ModelDefinition, publish_model_id)
        if remaining is not None:
            assert remaining.active is True
            assert remaining.published_at is not None

    with factory.begin() as session:
        company = Company(name="Grant Delete Race")
        grant_model = ModelDefinition(
            slug="grant-delete-race",
            display_name="Grant Delete Race",
            provider_key="postgres-test",
            billing_mode="per_second",
            active=False,
            published_at=None,
        )
        session.add_all([company, grant_model])
        session.flush()
        company_id = company.id
        grant_model_id = grant_model.id

    def grant():
        with factory.begin() as session:
            return ModelGrantService.upsert_grant(
                session,
                company_id=company_id,
                model_id=grant_model_id,
                enabled=False,
                price_per_second_cents=25,
                price_per_item_cents=None,
                config_override={},
            ).id

    def delete_grant_model():
        with factory.begin() as session:
            return ModelCatalogService.delete_draft(session, model_id=grant_model_id)[
                "id"
            ]

    grant_delete_results = _run_concurrently(grant, delete_grant_model)
    assert sum(status == "ok" for status, _ in grant_delete_results) == 1
    grant_delete_errors = [
        value for status, value in grant_delete_results if status == "error"
    ]
    assert len(grant_delete_errors) == 1
    assert isinstance(grant_delete_errors[0], (ConflictError, NotFoundError))
    with factory() as session:
        remaining_model = session.get(ModelDefinition, grant_model_id)
        remaining_grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.model_id == grant_model_id,
            )
        )
        assert (remaining_model is None) == (remaining_grant is None)


def _seed_capability_snapshot_race(factory, suffix: str):
    capability = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=9,
                max_videos=3,
                max_audio=3,
                supports_face=True,
                output_counts=[1],
            )
        }
    )
    with factory.begin() as session:
        company = Company(name=f"Capability Race {suffix}")
        user = User(
            email=f"capability-race-{suffix}@example.com",
            display_name=f"Capability Race {suffix}",
        )
        model = ModelDefinition(
            slug=f"capability-race-{suffix}",
            display_name=f"Capability Race {suffix}",
            provider_key="postgres-capability-test",
            billing_mode="per_item",
            active=True,
        )
        session.add_all([company, user, model])
        session.flush()
        session.add_all(
            [
                CompanyMembership(
                    company_id=company.id,
                    user_id=user.id,
                    status=MembershipStatus.ACTIVE,
                ),
                ModelCapability(
                    model_id=model.id,
                    capability_key="generation",
                    config=capability,
                ),
                CompanyModelGrant(
                    company_id=company.id,
                    model_id=model.id,
                    enabled=True,
                    price_per_second_cents=None,
                    price_per_item_cents=100,
                    config_override={},
                ),
            ]
        )
        return company.id, user.id, model.id, capability


def _create_capability_race_task(
    factory,
    *,
    company_id: str,
    user_id: str,
    model_id: str,
    idempotency_key: str,
):
    with factory.begin() as session:
        task, created = TaskService.create(
            session,
            company_id=company_id,
            user_id=user_id,
            model_id=model_id,
            idempotency_key=idempotency_key,
            request_payload={
                "mode": "text_to_video",
                "prompt": "PostgreSQL coherent capability snapshot",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
            },
        )
        assert created is True
        return {
            "task_id": task.id,
            "pricing_snapshot": dict(task.pricing_snapshot),
            "capability_snapshot": dict(task.capability_snapshot),
        }


def test_postgres_idempotency_race_preserves_one_relay_backend_affinity(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, user_id, model_id, _ = _seed_capability_snapshot_race(
        factory, "relay-affinity"
    )
    request_payload = {
        "mode": "text_to_video",
        "prompt": "PostgreSQL Relay affinity winner",
        "assets": [],
        "duration_seconds": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "output_count": 1,
        "face_enabled": False,
    }

    def create(backend_id: str):
        with factory.begin() as session:
            task, created = TaskService.create(
                session,
                company_id=company_id,
                user_id=user_id,
                model_id=model_id,
                idempotency_key="postgres-relay-affinity-race",
                request_payload=request_payload,
                relay_backend_id=backend_id,
                relay_contract_revision="generations.v1",
            )
            return (
                task.id,
                created,
                task.relay_backend_id,
                task.relay_contract_revision,
            )

    results = _run_concurrently(
        lambda: create("legacy-default-v1"),
        lambda: create("new-api-v1"),
    )
    assert all(status == "ok" for status, _ in results), results
    values = [value for _, value in results]
    assert len({value[0] for value in values}) == 1
    assert sum(value[1] for value in values) == 1
    assert len({value[2:] for value in values}) == 1
    assert values[0][2] in {"legacy-default-v1", "new-api-v1"}

    with factory() as session:
        task = session.scalar(
            select(GenerationTask).where(
                GenerationTask.company_id == company_id,
                GenerationTask.idempotency_key == "postgres-relay-affinity-race",
            )
        )
        assert task is not None
        assert (task.relay_backend_id, task.relay_contract_revision) == values[0][2:]


def test_postgres_task_and_model_revision_race_has_no_mixed_snapshot(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, user_id, model_id, original = _seed_capability_snapshot_race(
        factory, "model-revision"
    )
    revised = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=4,
                max_videos=2,
                max_audio=1,
                supports_face=False,
                durations=[5, 10, 15],
                output_counts=[1],
            )
        }
    )

    def create_task():
        return _create_capability_race_task(
            factory,
            company_id=company_id,
            user_id=user_id,
            model_id=model_id,
            idempotency_key="capability-race-model-task",
        )

    def revise_model():
        with factory.begin() as session:
            ModelCatalogService.disable(session, model_id=model_id)
            _, model, changed = ModelCatalogService.update_model(
                session,
                model_id=model_id,
                display_name="Capability Race model revised",
                provider_key="postgres-capability-test",
                billing_mode="per_item",
                expected_capability_version=1,
                capabilities=[("generation", revised)],
            )
            assert changed is True
            ModelCatalogService.publish(session, model_id=model_id)
            return model.capability_version

    results = _run_concurrently(create_task, revise_model)
    assert all(status == "ok" for status, _ in results), results
    task_result = next(
        value for status, value in results if status == "ok" and isinstance(value, dict)
    )
    snapshot = task_result["capability_snapshot"]
    observed = (
        snapshot["capability_version"],
        snapshot["capabilities"]["generation"],
        snapshot["effective_capabilities"],
    )
    assert observed in [
        (1, original, original),
        (2, revised, revised),
    ]
    assert task_result["pricing_snapshot"]["unit_price_cents"] == 100


def test_postgres_task_and_grant_revision_race_has_no_mixed_snapshot(
    postgres_session_factory,
):
    factory = postgres_session_factory
    company_id, user_id, model_id, base = _seed_capability_snapshot_race(
        factory, "grant-revision"
    )
    restricted = canonical_capability(
        modes={
            "text_to_video": _mode(
                max_images=4,
                max_videos=2,
                max_audio=1,
                supports_face=False,
                durations=[5],
                resolutions=["720p"],
                output_counts=[1],
            )
        }
    )

    def create_task():
        return _create_capability_race_task(
            factory,
            company_id=company_id,
            user_id=user_id,
            model_id=model_id,
            idempotency_key="capability-race-grant-task",
        )

    def revise_grant():
        with factory.begin() as session:
            grant = ModelGrantService.upsert_grant(
                session,
                company_id=company_id,
                model_id=model_id,
                enabled=True,
                price_per_second_cents=None,
                price_per_item_cents=200,
                config_override=restricted,
            )
            return grant.id

    results = _run_concurrently(create_task, revise_grant)
    assert all(status == "ok" for status, _ in results), results
    task_result = next(
        value for status, value in results if status == "ok" and isinstance(value, dict)
    )
    unit_price = task_result["pricing_snapshot"]["unit_price_cents"]
    effective = task_result["capability_snapshot"]["effective_capabilities"]
    assert (unit_price, effective) in [
        (100, base),
        (200, restricted),
    ]


@pytest.mark.parametrize(
    ("call_quota", "concurrency_limit"),
    [(1, None), (None, 1)],
)
def test_postgres_required_resource_limits_serialize_across_models(
    postgres_session_factory,
    call_quota,
    concurrency_limit,
):
    factory = postgres_session_factory
    suffix = "quota" if call_quota is not None else "concurrency"
    resource_key = f"feature.pg-generation-{suffix}"
    capability = canonical_capability(modes={"text_to_video": _mode(output_counts=[1])})
    capability["modes"]["text_to_video"]["required_resource_keys"] = [resource_key]
    with factory.begin() as session:
        company = Company(name=f"Resource limit {suffix}")
        user = User(
            email=f"resource-limit-{suffix}@example.com",
            display_name=f"Resource limit {suffix}",
        )
        resource = ResourceDefinition(
            key=resource_key,
            kind=ResourceKind.FEATURE,
            display_name=f"Resource limit {suffix}",
            active=True,
        )
        models = [
            ModelDefinition(
                slug=f"resource-limit-{suffix}-{index}",
                display_name=f"Resource limit {suffix} {index}",
                provider_key="postgres-resource-limit",
                billing_mode="per_item",
                active=True,
            )
            for index in range(2)
        ]
        session.add_all([company, user, resource, *models])
        session.flush()
        session.add(
            CompanyMembership(
                company_id=company.id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE,
            )
        )
        session.add(
            CompanyResourceGrant(
                company_id=company.id,
                resource_id=resource.id,
                enabled=True,
                call_quota=call_quota,
                concurrency_limit=concurrency_limit,
            )
        )
        for model in models:
            session.add_all(
                [
                    ModelCapability(
                        model_id=model.id,
                        capability_key="generation",
                        config=capability,
                    ),
                    CompanyModelGrant(
                        company_id=company.id,
                        model_id=model.id,
                        enabled=True,
                        price_per_second_cents=None,
                        price_per_item_cents=100,
                        config_override={},
                    ),
                ]
            )
        company_id = company.id
        user_id = user.id
        model_ids = [model.id for model in models]

    def create(model_id, index):
        with factory.begin() as session:
            task, created = TaskService.create(
                session,
                company_id=company_id,
                user_id=user_id,
                model_id=model_id,
                idempotency_key=f"resource-{suffix}-{index}",
                request_payload={
                    "mode": "text_to_video",
                    "prompt": "serialize the shared resource grant",
                    "assets": [],
                    "duration_seconds": 5,
                    "aspect_ratio": "16:9",
                    "resolution": "720p",
                    "output_count": 1,
                    "face_enabled": False,
                },
            )
            assert created is True
            return task.id

    results = _run_concurrently(
        lambda: create(model_ids[0], 0),
        lambda: create(model_ids[1], 1),
    )
    assert sum(status == "ok" for status, _ in results) == 1, results
    errors = [value for status, value in results if status == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], PermissionDeniedError)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(GenerationTask.id)).where(
                    GenerationTask.company_id == company_id
                )
            )
            == 1
        )
