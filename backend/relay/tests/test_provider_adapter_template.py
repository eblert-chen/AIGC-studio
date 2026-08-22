from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from examples.provider_adapter_template import (
    CompanyDeliveredApiAdapter,
    DeliveredAccountUnavailable,
    DeliveredOutput,
    DeliveredQueryTemporarilyUnavailable,
    DeliveredTaskState,
)
from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    OutputOptions,
    ProviderWebhookStatus,
)
from relay_service.providers.base import ProviderError
from relay_service.providers.mock import MockProviderAdapter


_CAPABILITIES = asyncio.run(MockProviderAdapter().capabilities())


def _job() -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model="mock.video.v1",
        mode=GenerationMode.TEXT_TO_VIDEO,
        inputs=GenerationInputs(prompt="template contract"),
        output=OutputOptions(),
    )


class DeliveredClient:
    def __init__(self) -> None:
        self.job_id: str | None = None
        self.output_url = "https://outputs.example.test/one.mp4"
        self.query_error: Exception | None = None

    async def healthcheck(self) -> bool:
        return True

    async def create_task(self, *, correlation_id: str, **_):
        self.job_id = correlation_id
        return "delivered-task", correlation_id

    async def get_task(self, provider_task_id: str):
        if self.query_error is not None:
            raise self.query_error
        assert self.job_id is not None
        return DeliveredTaskState(
            provider_task_id=provider_task_id,
            correlation_id=self.job_id,
            status="succeeded",
            progress=100,
            outputs=[
                DeliveredOutput(
                    url=self.output_url,
                    media_type="video",
                    content_type="video/mp4",
                )
            ],
        )

    async def close(self) -> None:
        return None


def _adapter(client: DeliveredClient) -> CompanyDeliveredApiAdapter:
    return CompanyDeliveredApiAdapter(
        client=client,
        capabilities=_CAPABILITIES,
        account_id="reverse-a",
        max_concurrency=2,
        requests_per_minute=30,
    )


def test_template_checks_task_identity_and_hashes_complete_state() -> None:
    async def scenario() -> None:
        client = DeliveredClient()
        adapter = _adapter(client)
        job = _job()
        submission = await adapter.submit(job)
        job.provider = adapter.route_id
        job.provider_task_id = submission.provider_task_id

        first = await adapter.poll(job)
        assert first is not None
        assert first.status == ProviderWebhookStatus.SUCCEEDED
        client.output_url = "https://outputs.example.test/two.mp4"
        second = await adapter.poll(job)
        assert second is not None
        assert second.event_id != first.event_id

        client.job_id = str(uuid4())
        with pytest.raises(ProviderError) as caught:
            await adapter.poll(job)
        assert caught.value.code == "DELIVERED_API_TASK_IDENTITY_MISMATCH"

    asyncio.run(scenario())


def test_template_rejects_insecure_outputs_and_unknown_states() -> None:
    with pytest.raises(ValidationError):
        DeliveredOutput(
            url="http://outputs.example.test/result.mp4",
            media_type="video",
        )
    with pytest.raises(ValidationError):
        DeliveredTaskState.model_validate(
            {
                "provider_task_id": "task",
                "correlation_id": "correlation",
                "status": "mystery",
            }
        )


def test_template_query_errors_do_not_misclassify_account_health() -> None:
    async def scenario() -> None:
        client = DeliveredClient()
        adapter = _adapter(client)
        job = _job()
        submission = await adapter.submit(job)
        job.provider_task_id = submission.provider_task_id
        client.query_error = DeliveredQueryTemporarilyUnavailable()

        with pytest.raises(ProviderError) as caught:
            await adapter.poll(job)
        assert caught.value.retryable is True
        assert caught.value.account_unavailable is False

        client.query_error = DeliveredAccountUnavailable(permanent=True)
        with pytest.raises(ProviderError) as disabled:
            await adapter.poll(job)
        assert disabled.value.account_unavailable is True
        assert disabled.value.disable_account is True

    asyncio.run(scenario())
