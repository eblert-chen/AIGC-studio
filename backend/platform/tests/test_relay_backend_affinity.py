from __future__ import annotations

from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text

from platform_api.models import (
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    PersonalLedgerEntry,
    PersonalWalletAccount,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    WalletAccount,
    utcnow,
)
from platform_api.relay_backends import (
    DEFAULT_RELAY_CONTRACT_REVISION,
    LEGACY_RELAY_BACKEND_ID,
    RelayBackendRegistry,
    RelayBackendResolutionError,
    relay_callback_url_for_backend,
)
from platform_api.relay_client import HttpxRelayClient
from platform_api.relay_sync_worker import RelayStatusPoller
from platform_api.services.relay_outbox import RelayOutboxDispatcher
from platform_api.services.relay_callbacks import (
    RelayCallbackService,
    RelayCallbackVerifier,
    RelayCallbackVerifierRegistry,
)
from platform_api.services.errors import NotFoundError
from platform_api.services.relay_status import RelayStatusService
from platform_api.services.task_timeouts import TaskTimeoutService

from .test_relay_boundary import accepted_response, job_snapshot
from .test_artifact_bridge_and_production_safety import (
    ASSET_ID,
    make_task_downloadable,
)
from .test_artifact_preview import _bound_preview_payload
from .test_personal_workspace import _personal_user, _retail_model
from .test_wallet_and_tasks import seed_model

OLD_BACKEND_ID = LEGACY_RELAY_BACKEND_ID
NEW_BACKEND_ID = "new-api-v1"
OLD_JOB_ID = "10101010-1010-4010-8010-101010101010"
NEW_JOB_ID = "20202020-2020-4020-8020-202020202020"


class RecordingRelayClient:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.submit_calls: list[str] = []
        self.get_calls: list[str] = []
        self.snapshots = {}

    def submit(self, payload, *, idempotency_key, request_id=None):
        self.submit_calls.append(payload.client_reference_id)
        return accepted_response(self.job_id, status="processing")

    def get(self, job_id: str, *, request_id=None):
        self.get_calls.append(job_id)
        return self.snapshots[job_id]


def _registry(
    *,
    default_backend_id: str,
    old_client: RecordingRelayClient,
    new_client: RecordingRelayClient,
) -> RelayBackendRegistry:
    return RelayBackendRegistry(
        default_backend_id=default_backend_id,
        default_contract_revision=DEFAULT_RELAY_CONTRACT_REVISION,
        clients={
            OLD_BACKEND_ID: (DEFAULT_RELAY_CONTRACT_REVISION, old_client),
            NEW_BACKEND_ID: (DEFAULT_RELAY_CONTRACT_REVISION, new_client),
        },
    )


def _install_registry(app, registry: RelayBackendRegistry) -> None:
    app.state.relay_backend_registry = registry
    app.state.relay_client = registry.default_client_or_none()


def _seed_context(app, client, tenant, tenant_headers) -> str:
    model_id = seed_model(app, tenant["company_id"])
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 4_000,
            "idempotency_key": "affinity-recharge-once",
        },
    )
    assert response.status_code == 200, response.text
    return model_id


def _create_task(client, tenant, tenant_headers, *, model_id: str, suffix: str):
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": f"affinity-task-{suffix}",
            "request_payload": {
                "prompt": f"affinity {suffix}",
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_registry_requires_exact_persisted_contract_revision() -> None:
    old_client = RecordingRelayClient(OLD_JOB_ID)
    registry = RelayBackendRegistry(
        default_backend_id=OLD_BACKEND_ID,
        clients={
            OLD_BACKEND_ID: (DEFAULT_RELAY_CONTRACT_REVISION, old_client),
        },
    )

    assert registry.default_affinity.backend_id == OLD_BACKEND_ID
    assert (
        registry.resolve(
            backend_id=OLD_BACKEND_ID,
            contract_revision=DEFAULT_RELAY_CONTRACT_REVISION,
        )
        is old_client
    )
    with pytest.raises(RelayBackendResolutionError):
        registry.resolve(
            backend_id=OLD_BACKEND_ID,
            contract_revision="generations.v2",
        )
    with pytest.raises(RelayBackendResolutionError):
        registry.resolve(
            backend_id="removed-backend",
            contract_revision=DEFAULT_RELAY_CONTRACT_REVISION,
        )
    callback_base = "https://platform.example/internal/relay-callbacks"
    assert (
        relay_callback_url_for_backend(callback_base, backend_id=OLD_BACKEND_ID)
        == callback_base
    )
    assert (
        relay_callback_url_for_backend(callback_base, backend_id=NEW_BACKEND_ID)
        == f"{callback_base}/{NEW_BACKEND_ID}"
    )


def test_callback_cannot_bind_a_job_through_a_mismatched_outbox_affinity() -> None:
    task = GenerationTask(
        id="callback-affinity-task",
        company_id="callback-affinity-company",
        relay_backend_id=OLD_BACKEND_ID,
        relay_contract_revision=DEFAULT_RELAY_CONTRACT_REVISION,
        relay_job_id=None,
    )
    outbox = RelaySubmissionOutbox(
        company_id=task.company_id,
        task_id=task.id,
        status=RelayOutboxStatus.PROCESSING,
        idempotency_key=f"platform-task-{task.id}",
        relay_backend_id=NEW_BACKEND_ID,
        relay_contract_revision=DEFAULT_RELAY_CONTRACT_REVISION,
        relay_payload={
            "client_reference_id": task.id,
            "metadata": {
                "platform_company_id": task.company_id,
                "platform_task_id": task.id,
            },
        },
        relay_job_id=None,
    )

    with pytest.raises(NotFoundError):
        RelayCallbackService._bind_relay_job_from_trusted_callback(
            task=task,
            outbox=outbox,
            relay_job_id=OLD_JOB_ID,
            source_backend_id=OLD_BACKEND_ID,
        )


def test_task_and_outbox_affinity_survive_default_switch_and_idempotent_replay(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    old_client = RecordingRelayClient(OLD_JOB_ID)
    new_client = RecordingRelayClient(NEW_JOB_ID)
    _install_registry(
        app,
        _registry(
            default_backend_id=OLD_BACKEND_ID,
            old_client=old_client,
            new_client=new_client,
        ),
    )
    model_id = _seed_context(app, client, tenant, tenant_headers)
    old_task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="stable-replay",
    )
    assert "relay_backend_id" not in old_task
    assert "relay_contract_revision" not in old_task

    _install_registry(
        app,
        _registry(
            default_backend_id=NEW_BACKEND_ID,
            old_client=old_client,
            new_client=new_client,
        ),
    )
    replay = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="stable-replay",
    )
    new_task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="after-switch",
    )
    assert replay["id"] == old_task["id"]
    for response in (replay, new_task):
        assert "relay_backend_id" not in response
        assert "relay_contract_revision" not in response

    with app.state.session_factory() as session:
        task_rows = list(
            session.scalars(select(GenerationTask).order_by(GenerationTask.created_at))
        )
        outbox_rows = list(
            session.scalars(
                select(RelaySubmissionOutbox).order_by(RelaySubmissionOutbox.created_at)
            )
        )
        assert [row.relay_backend_id for row in task_rows] == [
            OLD_BACKEND_ID,
            NEW_BACKEND_ID,
        ]
        assert [row.relay_backend_id for row in outbox_rows] == [
            OLD_BACKEND_ID,
            NEW_BACKEND_ID,
        ]
        assert all(
            row.relay_contract_revision == DEFAULT_RELAY_CONTRACT_REVISION
            for row in [*task_rows, *outbox_rows]
        )


def test_callback_path_and_secret_are_bound_to_the_task_backend(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    old_client = RecordingRelayClient(OLD_JOB_ID)
    new_client = RecordingRelayClient(NEW_JOB_ID)
    _install_registry(
        app,
        _registry(
            default_backend_id=NEW_BACKEND_ID,
            old_client=old_client,
            new_client=new_client,
        ),
    )
    app.state.settings.relay_callback_public_url = (
        "http://platform-internal:8000/internal/relay-callbacks"
    )
    old_secret = "old-backend-callback-secret-with-32-bytes!!"
    new_secret = "new-backend-callback-secret-with-32-bytes!!"
    app.state.relay_callback_verifier_registry = RelayCallbackVerifierRegistry(
        {
            OLD_BACKEND_ID: RelayCallbackVerifier(old_secret),
            NEW_BACKEND_ID: RelayCallbackVerifier(new_secret),
        }
    )
    model_id = _seed_context(app, client, tenant, tenant_headers)
    created = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="callback-source",
    )

    with app.state.session_factory() as session:
        stored_task = session.get(GenerationTask, created["id"])
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == created["id"]
            )
        )
        assert stored_task is not None
        assert outbox is not None
        expected_revision = stored_task.capability_snapshot["relay_capability_revision"]
        assert outbox.relay_payload["callback"] == {
            "url": ("http://platform-internal:8000/internal/relay-callbacks/new-api-v1")
        }

    event_id = str(uuid4())
    body = {
        "api_version": "v1",
        "schema_version": 1,
        "event_id": event_id,
        "type": "generation.status_changed",
        "occurred_at": "2033-05-18T03:33:20Z",
        "job": {
            "api_version": "v1",
            "id": NEW_JOB_ID,
            "client_reference_id": created["id"],
            "status": "processing",
            "expected_capability_revision": expected_revision,
            "capability_revision": expected_revision,
            "reservation_action": "hold",
            "progress": 50,
            "outputs": [],
            "error": None,
        },
    }
    raw_body = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    timestamp = int(time.time())

    def signed_headers(secret: str) -> dict[str, str]:
        signing_input = f"{timestamp}.{event_id}.".encode("ascii") + raw_body
        signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Relay-Event-ID": event_id,
            "X-Relay-Timestamp": str(timestamp),
            "X-Relay-Signature": f"v1={signature}",
        }

    wrong_source = client.post(
        f"/internal/relay-callbacks/{OLD_BACKEND_ID}",
        content=raw_body,
        headers=signed_headers(old_secret),
    )
    assert wrong_source.status_code == 404
    wrong_secret = client.post(
        f"/internal/relay-callbacks/{OLD_BACKEND_ID}",
        content=raw_body,
        headers=signed_headers(new_secret),
    )
    assert wrong_secret.status_code == 401
    accepted = client.post(
        f"/internal/relay-callbacks/{NEW_BACKEND_ID}",
        content=raw_body,
        headers=signed_headers(new_secret),
    )
    assert accepted.status_code == 204, accepted.text

    with app.state.session_factory() as session:
        stored_task = session.get(GenerationTask, created["id"])
        assert stored_task is not None
        assert stored_task.relay_job_id == NEW_JOB_ID


def test_dispatch_poll_and_timeout_reconciliation_use_each_task_affinity(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    old_client = RecordingRelayClient(OLD_JOB_ID)
    new_client = RecordingRelayClient(NEW_JOB_ID)
    old_registry = _registry(
        default_backend_id=OLD_BACKEND_ID,
        old_client=old_client,
        new_client=new_client,
    )
    _install_registry(app, old_registry)
    model_id = _seed_context(app, client, tenant, tenant_headers)
    old_task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="old-dispatch",
    )

    registry = _registry(
        default_backend_id=NEW_BACKEND_ID,
        old_client=old_client,
        new_client=new_client,
    )
    _install_registry(app, registry)
    new_task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="new-dispatch",
    )

    dispatcher = RelayOutboxDispatcher(app.state.session_factory, registry)
    assert dispatcher.dispatch_once().relay_job_id == OLD_JOB_ID
    assert dispatcher.dispatch_once().relay_job_id == NEW_JOB_ID
    assert old_client.submit_calls == [old_task["id"]]
    assert new_client.submit_calls == [new_task["id"]]

    old_client.snapshots[OLD_JOB_ID] = job_snapshot(
        task_id=old_task["id"],
        job_id=OLD_JOB_ID,
        status="processing",
    )
    new_client.snapshots[NEW_JOB_ID] = job_snapshot(
        task_id=new_task["id"],
        job_id=NEW_JOB_ID,
        status="processing",
    )
    assert (
        RelayStatusPoller(
            app.state.session_factory,
            registry,
            batch_size=10,
        ).poll_once()
        == 2
    )
    assert old_client.get_calls == [OLD_JOB_ID]
    assert new_client.get_calls == [NEW_JOB_ID]

    old_client.get_calls.clear()
    new_client.get_calls.clear()
    timeout_result = TaskTimeoutService(
        app.state.session_factory,
        registry,
        queued_timeout_seconds=1,
        processing_timeout_seconds=1,
        batch_size=10,
    ).scan_once(now=utcnow() + timedelta(seconds=2))
    assert timeout_result.scanned == 2
    assert {item.outcome for item in timeout_result.items} == {"deferred_relay_active"}
    assert old_client.get_calls == [OLD_JOB_ID]
    assert new_client.get_calls == [NEW_JOB_ID]


def test_poller_preserves_company_and_personal_scope_affinity_and_billing_idempotency(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    old_client = RecordingRelayClient(OLD_JOB_ID)
    new_client = RecordingRelayClient(NEW_JOB_ID)
    old_registry = _registry(
        default_backend_id=OLD_BACKEND_ID,
        old_client=old_client,
        new_client=new_client,
    )
    _install_registry(app, old_registry)
    company_model_id = _seed_context(app, client, tenant, tenant_headers)
    company_task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=company_model_id,
        suffix="company-poll-scope",
    )
    assert RelayOutboxDispatcher(
        app.state.session_factory, old_registry
    ).dispatch_once().relay_job_id == OLD_JOB_ID

    new_registry = _registry(
        default_backend_id=NEW_BACKEND_ID,
        old_client=old_client,
        new_client=new_client,
    )
    _install_registry(app, new_registry)
    personal_user_id = _personal_user(app, "affinity-poller")
    personal_headers = {"X-User-ID": personal_user_id}
    workspace_response = client.get(
        "/api/v1/personal/me", headers=personal_headers
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_id = workspace_response.json()["workspace_id"]
    credited = client.post(
        f"/internal/personal/wallets/{workspace_id}/credit",
        headers={"X-Internal-Service-Token": "test-internal-token"},
        json={
            "amount_points": 100,
            "idempotency_key": "affinity-personal-credit",
            "note": "poller scope acceptance",
        },
    )
    assert credited.status_code == 200, credited.text
    personal_model_id = _retail_model(app)
    personal_models = client.get(
        "/api/v1/personal/models", headers=personal_headers
    )
    assert personal_models.status_code == 200, personal_models.text
    personal_model = next(
        item
        for item in personal_models.json()
        if item["id"] == personal_model_id
    )
    personal_created = client.post(
        "/api/v1/personal/tasks",
        headers=personal_headers,
        json={
            "model_id": personal_model_id,
            "expected_capability_version": personal_model["capability_version"],
            "expected_quote_revision": personal_model["quote_revision"],
            "idempotency_key": "affinity-personal-poll-task",
            "request_payload": {
                "mode": "text_to_video",
                "prompt": "personal poll scope",
                "assets": [],
                "duration_seconds": 5,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "output_count": 1,
                "face_enabled": False,
            },
        },
    )
    assert personal_created.status_code == 201, personal_created.text
    personal_task = personal_created.json()
    assert RelayOutboxDispatcher(
        app.state.session_factory, new_registry
    ).dispatch_once().relay_job_id == NEW_JOB_ID

    old_client.snapshots[OLD_JOB_ID] = job_snapshot(
        task_id=company_task["id"],
        job_id=OLD_JOB_ID,
        status="failed",
        error={
            "code": "GENERATION_FAILED",
            "message": "company provider failed",
            "retryable": False,
            "details": {},
        },
    )
    new_client.snapshots[NEW_JOB_ID] = job_snapshot(
        task_id=personal_task["id"],
        job_id=NEW_JOB_ID,
        status="failed",
        error={
            "code": "GENERATION_FAILED",
            "message": "personal provider failed",
            "retryable": False,
            "details": {},
        },
    )
    assert RelayStatusPoller(
        app.state.session_factory,
        new_registry,
        batch_size=10,
    ).poll_once() == 2
    assert old_client.get_calls == [OLD_JOB_ID]
    assert new_client.get_calls == [NEW_JOB_ID]

    with app.state.session_factory.begin() as session:
        company_row = session.get(GenerationTask, company_task["id"])
        personal_row = session.get(GenerationTask, personal_task["id"])
        assert company_row is not None
        assert personal_row is not None
        assert company_row.status == TaskStatus.FAILED
        assert personal_row.status == TaskStatus.FAILED
        assert (company_row.relay_backend_id, company_row.company_id) == (
            OLD_BACKEND_ID,
            tenant["company_id"],
        )
        assert (
            personal_row.relay_backend_id,
            personal_row.company_id,
            personal_row.personal_workspace_id,
        ) == (NEW_BACKEND_ID, None, workspace_id)
        company_wallet = session.get(WalletAccount, tenant["company_id"])
        personal_wallet = session.get(PersonalWalletAccount, workspace_id)
        assert (company_wallet.available_cents, company_wallet.reserved_cents) == (
            4_000,
            0,
        )
        assert (
            personal_wallet.available_points,
            personal_wallet.reserved_points,
        ) == (100, 0)

    # At-least-once callback/poll delivery must replay the same terminal state
    # without appending a second release to either billing domain.
    for company_id, personal_workspace_id, task_id, relay_job_id in (
        (tenant["company_id"], None, company_task["id"], OLD_JOB_ID),
        (None, workspace_id, personal_task["id"], NEW_JOB_ID),
    ):
        with app.state.session_factory.begin() as session:
            RelayStatusService.apply(
                session,
                company_id=company_id,
                personal_workspace_id=personal_workspace_id,
                task_id=task_id,
                relay_job_id=relay_job_id,
                status="failed",
                reservation_action="release",
            )

    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.task_id == company_task["id"],
                LedgerEntry.kind == LedgerKind.RELEASE,
            )
        ) == 1
        assert session.scalar(
            select(func.count(PersonalLedgerEntry.id)).where(
                PersonalLedgerEntry.task_id == personal_task["id"],
                PersonalLedgerEntry.kind == LedgerKind.RELEASE,
            )
        ) == 1

def test_artifact_preview_uses_task_affinity_after_global_default_switch(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    placeholder_old = RecordingRelayClient(OLD_JOB_ID)
    placeholder_new = RecordingRelayClient(NEW_JOB_ID)
    _install_registry(
        app,
        _registry(
            default_backend_id=OLD_BACKEND_ID,
            old_client=placeholder_old,
            new_client=placeholder_new,
        ),
    )
    model_id = _seed_context(app, client, tenant, tenant_headers)
    task = _create_task(
        client,
        tenant,
        tenant_headers,
        model_id=model_id,
        suffix="artifact-old-backend",
    )
    make_task_downloadable(app, task["id"])

    old_requests: list[httpx.Request] = []
    new_requests: list[httpx.Request] = []

    def old_handler(request: httpx.Request) -> httpx.Response:
        old_requests.append(request)
        return httpx.Response(200, json=_bound_preview_payload())

    def new_handler(request: httpx.Request) -> httpx.Response:
        new_requests.append(request)
        return httpx.Response(500, json={"error": "wrong backend"})

    old_http = HttpxRelayClient(
        base_url="https://relay-python.example.test",
        client_id="platform-old",
        api_key="old-server-secret",
        transport=httpx.MockTransport(old_handler),
    )
    new_http = HttpxRelayClient(
        base_url="https://relay-new-api.example.test",
        client_id="platform-new",
        api_key="new-server-secret",
        transport=httpx.MockTransport(new_handler),
    )
    registry = RelayBackendRegistry(
        default_backend_id=NEW_BACKEND_ID,
        clients={
            OLD_BACKEND_ID: (DEFAULT_RELAY_CONTRACT_REVISION, old_http),
            NEW_BACKEND_ID: (DEFAULT_RELAY_CONTRACT_REVISION, new_http),
        },
    )
    _install_registry(app, registry)
    try:
        response = client.get(
            f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
            f"/artifacts/{ASSET_ID}/preview",
            headers=tenant_headers,
        )
        assert response.status_code == 200, response.text
        assert len(old_requests) == 1
        assert new_requests == []
    finally:
        registry.close()


def _migration_config(project_root: Path, database_path: Path) -> Config:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def test_affinity_migration_backfills_a_fixed_legacy_identity_and_downgrades(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "relay-affinity.db"
    config = _migration_config(project_root, database_path)
    command.upgrade(config, "0032_publisher_oauth")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    metadata = MetaData()
    tasks = Table("generation_tasks", metadata, autoload_with=engine)
    outbox = Table("relay_submission_outbox", metadata, autoload_with=engine)
    now = utcnow()
    task_id = "affinity-migration-task"
    task_values = {
        "id": task_id,
        "company_id": "affinity-company",
        "user_id": "affinity-user",
        "model_id": "affinity-model",
        "idempotency_key": "affinity-migration-idempotency",
        "request_fingerprint": "a" * 64,
        "status": "QUEUED",
        "request_payload": {},
        "quote_cents": 1,
        "pricing_snapshot": {},
        "capability_snapshot": {},
        "reserved_cents": 1,
        "actual_cost_cents": None,
        "provider_task_id": None,
        "relay_job_id": None,
        "output_artifacts": [],
        "failure_reason": None,
        "relay_error_snapshot": None,
        "timeout_checked_at": None,
        "created_at": now,
        "updated_at": now,
    }
    outbox_values = {
        "id": "affinity-migration-outbox",
        "company_id": "affinity-company",
        "task_id": task_id,
        "status": "PENDING",
        "idempotency_key": f"platform-task-{task_id}",
        "relay_payload": {},
        "materialized_relay_payload": None,
        "relay_job_id": None,
        "relay_submit_attempted_at": None,
        "submission_outcome_uncertain_at": None,
        "attempt_count": 0,
        "next_attempt_at": now,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    with engine.begin() as connection:
        connection.execute(tasks.insert().values(**task_values))
        connection.execute(outbox.insert().values(**outbox_values))
    engine.dispose()

    command.upgrade(config, "0033_relay_backend_affinity")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        task_affinity = connection.execute(
            text(
                "SELECT relay_backend_id, relay_contract_revision "
                "FROM generation_tasks WHERE id = :task_id"
            ),
            {"task_id": task_id},
        ).one()
        outbox_affinity = connection.execute(
            text(
                "SELECT relay_backend_id, relay_contract_revision "
                "FROM relay_submission_outbox WHERE task_id = :task_id"
            ),
            {"task_id": task_id},
        ).one()
    assert tuple(task_affinity) == (
        LEGACY_RELAY_BACKEND_ID,
        DEFAULT_RELAY_CONTRACT_REVISION,
    )
    assert tuple(outbox_affinity) == tuple(task_affinity)
    for table_name in ("generation_tasks", "relay_submission_outbox"):
        columns = {
            column["name"]: column for column in inspect(engine).get_columns(table_name)
        }
        assert columns["relay_backend_id"]["nullable"] is False
        assert columns["relay_contract_revision"]["nullable"] is False
    engine.dispose()

    command.downgrade(config, "0032_publisher_oauth")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    for table_name in ("generation_tasks", "relay_submission_outbox"):
        column_names = {
            column["name"] for column in inspect(engine).get_columns(table_name)
        }
        assert "relay_backend_id" not in column_names
        assert "relay_contract_revision" not in column_names
    engine.dispose()
