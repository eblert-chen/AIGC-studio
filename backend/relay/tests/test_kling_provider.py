from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from relay_service.models import (
    AssetInput,
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    OutputOptions,
    ProviderWebhookStatus,
)
from relay_service.providers.base import ProviderError
from relay_service.providers.http import JsonHttpResponse, JsonTransportError
from relay_service.providers.kling import (
    KlingProviderAdapter,
    create_kling_provider,
)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    json: dict[str, Any] | None


class FakeTransport:
    def __init__(self, *results: JsonHttpResponse | Exception) -> None:
        self.results = list(results)
        self.requests: list[RecordedRequest] = []
        self.closed = False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers,
        json=None,
    ) -> JsonHttpResponse:
        self.requests.append(
            RecordedRequest(
                method=method,
                url=url,
                headers=dict(headers),
                json=deepcopy(json),
            )
        )
        if not self.results:
            raise AssertionError("Fake transport has no response")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True


def response(status: int, body: dict[str, Any]) -> JsonHttpResponse:
    return JsonHttpResponse(status=status, body=body)


def query_response(*tasks: dict[str, Any]) -> JsonHttpResponse:
    return response(
        200,
        {
            "code": 0,
            "message": "success",
            "request_id": "query-request",
            "data": list(tasks),
        },
    )


def task_record(
    generation: GenerationJob,
    *,
    task_id: str,
    status: str = "submitted",
    update_time: int = 1_780_000_000_001,
    outputs: list[dict[str, Any]] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "status": status,
        "message": message,
        "create_time": 1_780_000_000_000,
        "update_time": update_time,
        "external_id": str(generation.id),
        "outputs": outputs or [],
    }


@pytest.mark.parametrize(
    ("status", "provider_code", "operation", "account_unavailable"),
    [
        (401, 1201, "submit", True),
        (400, 1000, "submit", True),
        (400, 1101, "submit", True),
        (400, 1201, "submit", False),
        (404, 1203, "poll", False),
    ],
)
def test_provider_error_classifies_account_and_request_domains(
    status: int,
    provider_code: int,
    operation: str,
    account_unavailable: bool,
) -> None:
    error = KlingProviderAdapter._provider_response_error(
        status,
        provider_code,
        operation=operation,
    )

    assert error.account_unavailable is account_unavailable


def job(
    *,
    model: str = "kling-3.0",
    mode: GenerationMode = GenerationMode.TEXT_TO_VIDEO,
    assets: list[AssetInput] | None = None,
    provider_task_id: str | None = None,
) -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model=model,
        mode=mode,
        inputs=GenerationInputs(
            prompt="A paper dragon flies above Hangzhou",
            assets=assets or [],
        ),
        output=OutputOptions(
            duration_seconds=7,
            aspect_ratio="9:16",
            resolution="1080p",
            count=1,
        ),
        callback_url="https://caller.example.test/callback",
        provider_task_id=provider_task_id,
    )


@pytest.mark.asyncio
async def test_capabilities_are_non_production_and_support_model_aliases() -> None:
    provider = KlingProviderAdapter(
        api_key="secret",
        model_aliases={
            "relay.kling3": "kling-3.0",
            "relay.kling3-turbo": "kling-3.0-turbo",
        },
        transport=FakeTransport(),
    )

    capabilities = await provider.capabilities()

    assert provider.production_ready is False
    assert [item.model for item in capabilities] == [
        "relay.kling3",
        "relay.kling3",
        "relay.kling3-turbo",
        "relay.kling3-turbo",
    ]
    assert capabilities[0].modes == [GenerationMode.TEXT_TO_VIDEO]
    assert capabilities[0].input_media_types == []
    assert capabilities[0].limits.max_images == 0
    assert capabilities[1].modes == [GenerationMode.IMAGE_TO_VIDEO]
    assert capabilities[1].input_media_types == ["image"]
    assert capabilities[0].limits.max_prompt_length == 3_072
    assert capabilities[1].limits.max_images == 2
    assert capabilities[0].limits.duration_seconds == list(range(3, 16))


@pytest.mark.asyncio
async def test_submit_t2v_recovers_first_then_uses_official_path_contract() -> None:
    generation = job(model="kling-3.0-turbo")
    transport = FakeTransport(
        query_response(),
        response(
            200,
            {
                "code": 0,
                "message": "success",
                "request_id": "submit-request",
                "data": {
                    "id": "kling-task-t2v",
                    "status": "submitted",
                    "external_id": str(generation.id),
                },
            },
        ),
    )
    provider = KlingProviderAdapter(
        api_key="top-secret",
        transport=transport,
    )

    submission = await provider.submit(generation)

    assert submission.provider_task_id == "kling-task-t2v"
    assert transport.requests == [
        RecordedRequest(
            method="GET",
            url=(
                "https://api-singapore.klingai.com/tasks?"
                f"external_task_ids={generation.id}"
            ),
            headers={
                "Authorization": "Bearer top-secret",
                "Content-Type": "application/json",
            },
            json=None,
        ),
        RecordedRequest(
            method="POST",
            url=(
                "https://api-singapore.klingai.com/"
                "text-to-video/kling-3.0-turbo"
            ),
            headers={
                "Authorization": "Bearer top-secret",
                "Content-Type": "application/json",
            },
            json={
                "prompt": "A paper dragon flies above Hangzhou",
                "settings": {
                    "resolution": "1080p",
                    "duration": 7,
                    "aspect_ratio": "9:16",
                },
                "options": {"external_task_id": str(generation.id)},
            },
        ),
    ]
    assert "callback_url" not in repr(transport.requests)
    assert "caller.example.test" not in repr(transport.requests)


@pytest.mark.asyncio
async def test_submit_i2v_uses_contents_and_does_not_send_aspect_ratio() -> None:
    assets = [
        AssetInput(
            url="https://assets.example.test/first.png",
            media_type="image",
        ),
        AssetInput(
            url="https://assets.example.test/last.png",
            media_type="image",
        ),
    ]
    generation = job(mode=GenerationMode.IMAGE_TO_VIDEO, assets=assets)
    transport = FakeTransport(
        query_response(),
        response(
            200,
            {
                "code": 0,
                "message": "success",
                "request_id": "submit-request",
                "data": {
                    "id": "kling-task-i2v",
                    "status": "submitted",
                    "external_id": str(generation.id),
                },
            },
        ),
    )
    provider = KlingProviderAdapter(
        api_key="secret",
        base_url="https://api-beijing.klingai.com/",
        transport=transport,
    )

    submission = await provider.submit(generation)

    assert submission.provider_task_id == "kling-task-i2v"
    request = transport.requests[1]
    assert request.url == (
        "https://api-beijing.klingai.com/image-to-video/kling-3.0"
    )
    assert request.json == {
        "contents": [
            {
                "type": "prompt",
                "text": "A paper dragon flies above Hangzhou",
            },
            {
                "type": "first_frame",
                "url": "https://assets.example.test/first.png",
            },
            {
                "type": "last_frame",
                "url": "https://assets.example.test/last.png",
            },
        ],
        "settings": {"resolution": "1080p", "duration": 7},
        "options": {"external_task_id": str(generation.id)},
    }


@pytest.mark.asyncio
async def test_submit_returns_unique_recovered_task_without_posting() -> None:
    generation = job()
    transport = FakeTransport(
        query_response(
            task_record(generation, task_id="kling-existing-task")
        )
    )
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    submission = await provider.submit(generation)

    assert submission.provider_task_id == "kling-existing-task"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_submit_refuses_multiple_recovery_matches() -> None:
    generation = job()
    transport = FakeTransport(
        query_response(
            task_record(generation, task_id="kling-task-1"),
            task_record(generation, task_id="kling-task-2"),
        )
    )
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    with pytest.raises(ProviderError) as caught:
        await provider.submit(generation)

    assert caught.value.code == "KLING_EXTERNAL_TASK_ID_CONFLICT"
    assert caught.value.retryable is False
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_recovery_query_failure_does_not_post_and_is_retryable() -> None:
    secret = "secret-do-not-leak"
    transport = FakeTransport(TimeoutError(secret))
    provider = KlingProviderAdapter(api_key=secret, transport=transport)

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "KLING_RECOVERY_QUERY_UNAVAILABLE"
    assert caught.value.retryable is True
    assert secret not in str(caught.value)
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        JsonTransportError("request contained secret", outcome_unknown=True),
        TimeoutError("request contained secret"),
        ConnectionError("request contained secret"),
    ],
)
async def test_post_transport_failure_is_unknown_and_never_retryable(
    failure: Exception,
) -> None:
    transport = FakeTransport(query_response(), failure)
    provider = KlingProviderAdapter(
        api_key="secret-do-not-leak",
        transport=transport,
    )

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "KLING_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False
    assert "secret-do-not-leak" not in str(caught.value)
    assert [request.method for request in transport.requests] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_malformed_post_success_is_unknown_and_not_retryable() -> None:
    transport = FakeTransport(
        query_response(),
        response(
            200,
            {
                "code": 0,
                "data": {
                    "id": "created-but-response-is-incomplete",
                    "status": "submitted",
                },
            },
        ),
    )
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "KLING_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_poll_processing_has_stable_event_id_and_encoded_query() -> None:
    generation = job(provider_task_id="task with/slash")
    task = task_record(
        generation,
        task_id="task with/slash",
        status="processing",
    )
    transport = FakeTransport(query_response(task), query_response(task))
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    first = await provider.poll(generation)
    second = await provider.poll(generation)

    assert first is not None and second is not None
    assert first.status == ProviderWebhookStatus.PROCESSING
    assert first.progress == 50
    assert first.event_id == second.event_id
    assert transport.requests[0].url.endswith(
        "/tasks?task_ids=task+with%2Fslash"
    )
    assert transport.requests[0].json is None


@pytest.mark.asyncio
async def test_poll_success_maps_https_video_for_transfer() -> None:
    generation = job(provider_task_id="kling-success")
    transport = FakeTransport(
        query_response(
            task_record(
                generation,
                task_id="kling-success",
                status="succeeded",
                outputs=[
                    {
                        "type": "video",
                        "id": "video-1",
                        "url": "https://results.example.test/video.mp4?token=1",
                        "duration": "7",
                    }
                ],
            )
        )
    )
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    event = await provider.poll(generation)

    assert event is not None
    assert event.status == ProviderWebhookStatus.SUCCEEDED
    assert event.progress == 100
    assert len(event.outputs) == 1
    assert str(event.outputs[0].url) == (
        "https://results.example.test/video.mp4?token=1"
    )
    assert event.outputs[0].content_type == "video/mp4"


@pytest.mark.asyncio
async def test_poll_failed_does_not_expose_provider_message() -> None:
    secret = "provider-message-secret"
    generation = job(provider_task_id="kling-failed")
    task = task_record(
        generation,
        task_id="kling-failed",
        status="failed",
        message=f"diagnostic contained {secret}",
    )
    provider = KlingProviderAdapter(
        api_key="secret",
        transport=FakeTransport(query_response(task)),
    )

    event = await provider.poll(generation)

    assert event is not None and event.error is not None
    assert event.status == ProviderWebhookStatus.FAILED
    assert event.error.code == "KLING_GENERATION_FAILED"
    assert event.error.retryable is False
    assert secret not in event.model_dump_json()


@pytest.mark.asyncio
async def test_https_is_required_for_base_and_input_media() -> None:
    with pytest.raises(ValueError, match="HTTPS origin"):
        KlingProviderAdapter(
            api_key="secret",
            base_url="http://api-singapore.klingai.com",
            transport=FakeTransport(),
        )

    generation = job(
        mode=GenerationMode.IMAGE_TO_VIDEO,
        assets=[
            AssetInput(
                url="http://assets.example.test/first.png",
                media_type="image",
            )
        ],
    )
    transport = FakeTransport()
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    with pytest.raises(ProviderError) as caught:
        await provider.submit(generation)

    assert caught.value.code == "KLING_INPUT_URL_INVALID"
    assert caught.value.retryable is False
    assert transport.requests == []


@pytest.mark.asyncio
async def test_untrusted_webhook_is_disabled_and_close_delegates() -> None:
    transport = FakeTransport()
    provider = KlingProviderAdapter(api_key="secret", transport=transport)

    with pytest.raises(ProviderError) as caught:
        await provider.parse_webhook(
            b'{"id":"untrusted"}', {"authorization": "untrusted"}
        )

    assert caught.value.code == "KLING_WEBHOOK_NOT_TRUSTED"
    assert caught.value.retryable is False
    await provider.close()
    assert transport.closed is True


def test_environment_factory_requires_region_and_parses_model_aliases(
    monkeypatch,
) -> None:
    monkeypatch.setenv("KLING_API_KEY", "secret")
    monkeypatch.setenv(
        "KLING_BASE_URL", "https://api-beijing.klingai.com"
    )
    monkeypatch.setenv(
        "KLING_MODEL_ALIASES_JSON",
        '{"video.premium":"kling-3.0"}',
    )

    provider = create_kling_provider()

    assert isinstance(provider, KlingProviderAdapter)
    assert provider._model_aliases == {"video.premium": "kling-3.0"}
