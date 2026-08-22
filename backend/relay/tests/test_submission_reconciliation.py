from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from relay_service.auth import (
    SUBMISSION_RECONCILIATION_SCOPE,
    ClientCredential,
    StaticClientAuthenticator,
)
from relay_service.errors import RelayError
from relay_service.main import create_app
from relay_service.models import (
    GenerationInputs,
    GenerationMode,
    GenerationRequest,
    JobStatus,
    SubmissionReconciliationRequest,
    callback_delivery_for_job,
)
from relay_service.providers.base import ProviderError
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.router import ProviderRouter
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.service import GenerationService


class UnknownSubmissionProvider(MockProviderAdapter):
    async def submit(self, job):
        raise ProviderError(
            "PROVIDER_SUBMISSION_OUTCOME_UNKNOWN",
            "The provider response was lost after submission",
            retryable=False,
            submission_outcome_unknown=True,
        )


MOCK_CAPABILITY_REVISION = asyncio.run(
    ProviderRouter([MockProviderAdapter()]).model_catalog()
).data[0].capability_revision


def request() -> GenerationRequest:
    return GenerationRequest(
        model="mock.video.v1",
        expected_capability_revision=MOCK_CAPABILITY_REVISION,
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="reconcile this submission"),
    )


def test_unknown_submission_is_quarantined_with_status_callback() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        provider = UnknownSubmissionProvider(account_id="paid-account")
        service = GenerationService(
            repository,
            queue,
            ProviderRouter([provider]),
        )
        accepted = await service.submit(
            request(), "unknown-submission", uuid4()
        )
        queued = await repository.get(accepted.job_id)
        assert queued is not None
        queued.callback_url = "https://platform.example.test/relay-callback"
        await repository.save(queued)

        job = await service.process_next()

        assert job is not None
        assert job.status == JobStatus.RECONCILIATION_REQUIRED
        assert job.provider == "mock-video@paid-account"
        assert job.provider_task_id is None
        assert job.error is not None
        assert job.error.code == "SUBMISSION_RECONCILIATION_REQUIRED"
        callback = callback_delivery_for_job(job)
        assert callback is not None
        assert callback.event.job.status == JobStatus.RECONCILIATION_REQUIRED
        deliveries = await repository.list_callback_deliveries(job.tenant_id)
        assert len(deliveries) == 1
        assert deliveries[0].job_status == JobStatus.RECONCILIATION_REQUIRED
        assert await queue.depth() == 0
        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.RECONCILIATION_REQUIRED

    asyncio.run(scenario())


def test_reconciliation_cannot_override_a_persisted_provider_route() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        original = UnknownSubmissionProvider(
            account_id="paid-account", priority=1
        )
        other = MockProviderAdapter(account_id="other-account", priority=2)
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([original, other]),
        )
        tenant_id = uuid4()
        accepted = await service.submit(
            request(), "route-mismatch", tenant_id
        )
        await service.process_next()

        with pytest.raises(RelayError) as captured:
            await service.resolve_submission_reconciliation(
                accepted.job_id,
                tenant_id,
                SubmissionReconciliationRequest(
                    outcome="created",
                    provider_task_id="upstream-task-on-other-account",
                    provider_route=other.route_id,
                ),
            )

        assert captured.value.code == "PROVIDER_ROUTE_MISMATCH"
        assert captured.value.status_code == 409
        persisted = await repository.get(accepted.job_id)
        assert persisted is not None
        assert persisted.status == JobStatus.RECONCILIATION_REQUIRED
        assert persisted.provider == original.route_id
        assert persisted.provider_task_id is None

    asyncio.run(scenario())


def test_reconciliation_accepts_provider_route_only_when_missing() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        provider = UnknownSubmissionProvider(account_id="paid-account")
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([provider]),
        )
        tenant_id = uuid4()
        accepted = await service.submit(
            request(), "route-missing", tenant_id
        )
        await service.process_next()
        awaiting = await repository.get(accepted.job_id)
        assert awaiting is not None
        awaiting.provider = None
        await repository.save(awaiting)

        resolved = await service.resolve_submission_reconciliation(
            accepted.job_id,
            tenant_id,
            SubmissionReconciliationRequest(
                outcome="created",
                provider_task_id="confirmed-task-after-route-loss",
                provider_route=provider.route_id,
            ),
        )

        assert resolved.status == JobStatus.PROCESSING
        assert resolved.provider == provider.route_id
        assert resolved.provider_task_id == "confirmed-task-after-route-loss"

    asyncio.run(scenario())


def test_reconciliation_created_resumes_polling_and_is_idempotent() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        provider = UnknownSubmissionProvider(account_id="paid-account")
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([provider]),
        )
        accepted = await service.submit(
            request(), "reconcile-created", uuid4()
        )
        await service.process_next()
        resolution = SubmissionReconciliationRequest(
            outcome="created",
            provider_task_id="upstream-task-123",
        )

        resolved = await service.resolve_submission_reconciliation(
            accepted.job_id,
            (await repository.get(accepted.job_id)).tenant_id,
            resolution,
        )
        repeated = await service.resolve_submission_reconciliation(
            accepted.job_id,
            resolved.tenant_id,
            resolution,
        )

        assert resolved.status == JobStatus.PROCESSING
        assert resolved.provider == "mock-video@paid-account"
        assert resolved.provider_task_id == "upstream-task-123"
        assert resolved.error is None
        assert repeated.status == JobStatus.PROCESSING

    asyncio.run(scenario())


def test_reconciliation_not_created_is_the_only_failure_path() -> None:
    async def scenario() -> None:
        repository = InMemoryJobRepository()
        service = GenerationService(
            repository,
            InMemoryWorkQueue(),
            ProviderRouter([UnknownSubmissionProvider()]),
        )
        tenant_id = uuid4()
        accepted = await service.submit(
            request(), "reconcile-not-created", tenant_id
        )
        await service.process_next()
        resolution = SubmissionReconciliationRequest(outcome="not_created")

        resolved = await service.resolve_submission_reconciliation(
            accepted.job_id, tenant_id, resolution
        )
        repeated = await service.resolve_submission_reconciliation(
            accepted.job_id, tenant_id, resolution
        )

        assert resolved.status == JobStatus.FAILED
        assert resolved.error is not None
        assert resolved.error.code == "SUBMISSION_CONFIRMED_NOT_CREATED"
        assert repeated.status == JobStatus.FAILED

    asyncio.run(scenario())


def test_reconciliation_endpoint_is_tenant_scoped_and_hides_route() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    authenticator = StaticClientAuthenticator(
        {
            "client-a": ClientCredential(tenant_id=tenant_a, api_key="key-a"),
            "client-b": ClientCredential(tenant_id=tenant_b, api_key="key-b"),
            "ops-a": ClientCredential(
                tenant_id=tenant_a,
                api_key="ops-key-a",
                scopes=frozenset({SUBMISSION_RECONCILIATION_SCOPE}),
            ),
            "ops-b": ClientCredential(
                tenant_id=tenant_b,
                api_key="ops-key-b",
                scopes=frozenset({SUBMISSION_RECONCILIATION_SCOPE}),
            ),
        }
    )
    app = create_app(
        authenticator=authenticator,
        repository=InMemoryJobRepository(),
        queue=InMemoryWorkQueue(),
        router=ProviderRouter(
            [UnknownSubmissionProvider(account_id="private-account")]
        ),
        process_in_background=False,
    )
    client = TestClient(app)
    headers_a = {
        "X-Client-ID": "client-a",
        "X-API-Key": "key-a",
        "Idempotency-Key": "api-reconciliation",
    }
    submitted = client.post(
        "/v1/generations",
        headers=headers_a,
        json=request().model_dump(mode="json"),
    )
    assert submitted.status_code == 202
    job_id = UUID(submitted.json()["job_id"])
    asyncio.run(app.state.generation_service.process_next())

    listed_a = client.get(
        "/v1/operations/submission-reconciliations",
        headers={"X-Client-ID": "client-a", "X-API-Key": "key-a"},
    )
    assert listed_a.status_code == 403
    assert listed_a.json()["error"]["code"] == "INSUFFICIENT_CLIENT_SCOPE"
    listed_a = client.get(
        "/v1/operations/submission-reconciliations",
        headers={"X-Client-ID": "ops-a", "X-API-Key": "ops-key-a"},
    )
    listed_b = client.get(
        "/v1/operations/submission-reconciliations",
        headers={"X-Client-ID": "ops-b", "X-API-Key": "ops-key-b"},
    )
    assert listed_a.status_code == 200
    assert [item["id"] for item in listed_a.json()["items"]] == [str(job_id)]
    assert listed_b.status_code == 200
    assert listed_b.json()["items"] == []

    denied = client.post(
        f"/v1/operations/submission-reconciliations/{job_id}",
        headers={"X-Client-ID": "ops-b", "X-API-Key": "ops-key-b"},
        json={"outcome": "not_created"},
    )
    assert denied.status_code == 404

    resolved = client.post(
        f"/v1/operations/submission-reconciliations/{job_id}",
        headers={"X-Client-ID": "ops-a", "X-API-Key": "ops-key-a"},
        json={
            "outcome": "created",
            "provider_task_id": "confirmed-task",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "processing"
    assert "provider" not in resolved.json()
    assert "provider_task_id" not in resolved.json()
