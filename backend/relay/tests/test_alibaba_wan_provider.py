from __future__ import annotations

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
from relay_service.providers.alibaba_wan import (
    AlibabaWanProviderAdapter,
    create_alibaba_wan_provider,
)
from relay_service.providers.base import ProviderError
from relay_service.providers.http import JsonHttpResponse, JsonTransportError


@dataclass
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
            RecordedRequest(method, url, dict(headers), dict(json) if json else json)
        )
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True


def job(
    *,
    model: str = "wan2.7-t2v",
    mode: GenerationMode = GenerationMode.TEXT_TO_VIDEO,
    assets: list[AssetInput] | None = None,
    metadata: dict[str, Any] | None = None,
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
        metadata=metadata or {},
    )


def response(status: int, body: dict[str, Any]) -> JsonHttpResponse:
    return JsonHttpResponse(status=status, body=body)


@pytest.mark.parametrize(
    ("status", "provider_code", "operation", "account_unavailable"),
    [
        (401, "InvalidParameter", "submit", True),
        (400, "InvalidApiKey", "submit", True),
        (400, "Arrearage", "submit", True),
        (400, "InvalidParameter", "submit", False),
        (404, "NotFound", "query", False),
    ],
)
def test_provider_error_classifies_account_and_request_domains(
    status: int,
    provider_code: str,
    operation: str,
    account_unavailable: bool,
) -> None:
    error = AlibabaWanProviderAdapter._provider_response_error(
        status,
        {"code": provider_code},
        operation=operation,
    )

    assert error.account_unavailable is account_unavailable


@pytest.mark.asyncio
async def test_capabilities_cover_wan_27_t2v_and_i2v_without_production_opt_in() -> None:
    provider = AlibabaWanProviderAdapter(api_key="secret", transport=FakeTransport())

    capabilities = await provider.capabilities()
    by_model = {capability.model: capability for capability in capabilities}

    assert provider.production_ready is False
    assert by_model["wan2.7-t2v"].modes == [GenerationMode.TEXT_TO_VIDEO]
    assert by_model["wan2.7-i2v"].modes == [GenerationMode.IMAGE_TO_VIDEO]
    assert by_model["wan2.7-t2v"].limits.duration_seconds == list(range(2, 16))
    assert by_model["wan2.7-i2v"].limits.max_images == 2
    assert by_model["wan2.7-i2v"].limits.output_counts == [1]


@pytest.mark.asyncio
async def test_submit_text_to_video_ignores_provider_specific_metadata() -> None:
    transport = FakeTransport(
        response(
            200,
            {
                "output": {"task_status": "PENDING", "task_id": "wan-task-1"},
                "request_id": "request-1",
            },
        )
    )
    provider = AlibabaWanProviderAdapter(
        api_key="top-secret",
        base_url="https://workspace.cn-beijing.maas.aliyuncs.com/",
        transport=transport,
    )
    audio = AssetInput(
        url="https://assets.example.test/narration.mp3", media_type="audio"
    )

    submission = await provider.submit(
        job(
            assets=[audio],
            metadata={
                "alibaba_wan": {
                    "negative_prompt": "blurry",
                    "prompt_extend": False,
                    "watermark": True,
                    "seed": 42,
                }
            },
        )
    )

    assert submission.provider_task_id == "wan-task-1"
    assert transport.requests == [
        RecordedRequest(
            method="POST",
            url=(
                "https://workspace.cn-beijing.maas.aliyuncs.com"
                "/api/v1/services/aigc/video-generation/video-synthesis"
            ),
            headers={
                "Authorization": "Bearer top-secret",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            },
            json={
                "model": "wan2.7-t2v",
                "input": {
                    "prompt": "A paper dragon flies above Hangzhou",
                    "audio_url": "https://assets.example.test/narration.mp3",
                },
                "parameters": {
                    "resolution": "1080P",
                    "duration": 7,
                    "prompt_extend": True,
                    "watermark": False,
                    "ratio": "9:16",
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_submit_image_to_video_uses_media_array_and_omits_ratio() -> None:
    transport = FakeTransport(
        response(
            200,
            {
                "output": {"task_status": "PENDING", "task_id": "wan-task-2"},
                "request_id": "request-2",
            },
        )
    )
    provider = AlibabaWanProviderAdapter(api_key="secret", transport=transport)
    assets = [
        AssetInput(url="https://assets.example.test/first.png", media_type="image"),
        AssetInput(url="https://assets.example.test/last.png", media_type="image"),
        AssetInput(url="https://assets.example.test/voice.wav", media_type="audio"),
    ]

    await provider.submit(
        job(
            model="wan2.7-i2v-2026-04-25",
            mode=GenerationMode.IMAGE_TO_VIDEO,
            assets=assets,
        )
    )

    payload = transport.requests[0].json
    assert payload is not None
    assert payload["input"]["media"] == [
        {"type": "first_frame", "url": "https://assets.example.test/first.png"},
        {"type": "last_frame", "url": "https://assets.example.test/last.png"},
        {"type": "driving_audio", "url": "https://assets.example.test/voice.wav"},
    ]
    assert "ratio" not in payload["parameters"]


@pytest.mark.asyncio
async def test_submit_rejects_invalid_media_before_calling_provider() -> None:
    transport = FakeTransport()
    provider = AlibabaWanProviderAdapter(api_key="secret", transport=transport)
    image = AssetInput(url="https://assets.example.test/frame.png", media_type="image")

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job(assets=[image]))

    assert caught.value.code == "ALIBABA_WAN_INVALID_INPUT_MEDIA"
    assert caught.value.retryable is False
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        JsonTransportError("request failed with sk-live-do-not-leak", outcome_unknown=True),
        TimeoutError("sk-live-do-not-leak"),
        ConnectionError("sk-live-do-not-leak"),
    ],
)
async def test_ambiguous_submit_failure_is_not_retryable_and_hides_secret(
    failure: Exception,
) -> None:
    provider = AlibabaWanProviderAdapter(
        api_key="sk-live-do-not-leak", transport=FakeTransport(failure)
    )

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "ALIBABA_WAN_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False
    assert "sk-live-do-not-leak" not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_successful_submission_is_ambiguous_and_not_retryable() -> None:
    provider = AlibabaWanProviderAdapter(
        api_key="secret",
        transport=FakeTransport(
            response(200, {"output": {"task_status": "PENDING"}})
        ),
    )

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "ALIBABA_WAN_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_submit_server_error_is_ambiguous_and_never_safe_to_fail_over() -> None:
    provider = AlibabaWanProviderAdapter(
        api_key="secret",
        transport=FakeTransport(
            response(
                503,
                {
                    "code": "InternalError",
                    "message": "the gateway failed after accepting the request",
                },
            )
        ),
    )

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "ALIBABA_WAN_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.retryable is False
    assert caught.value.submission_outcome_unknown is True
    assert caught.value.account_unavailable is False


@pytest.mark.asyncio
async def test_explicit_submit_throttle_is_retryable_but_body_is_not_exposed() -> None:
    secret = "sk-response-secret"
    provider = AlibabaWanProviderAdapter(
        api_key=secret,
        transport=FakeTransport(
            response(
                429,
                {
                    "code": "Throttling.RateQuota",
                    "message": f"request contained {secret}",
                    "request_id": "request-3",
                },
            )
        ),
    )

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job())

    assert caught.value.code == "ALIBABA_WAN_RATE_QUOTA_EXCEEDED"
    assert caught.value.retryable is True
    assert secret not in str(caught.value)


@pytest.mark.asyncio
async def test_poll_maps_progress_and_produces_stable_event_id() -> None:
    pending = response(
        200,
        {
            "request_id": "poll-1",
            "output": {"task_id": "task with/slash", "task_status": "PENDING"},
        },
    )
    transport = FakeTransport(pending, pending)
    provider = AlibabaWanProviderAdapter(
        api_key="secret",
        base_url="https://workspace.ap-southeast-1.maas.aliyuncs.com",
        transport=transport,
    )
    generation_job = job()
    generation_job.provider_task_id = "task with/slash"

    first = await provider.poll(generation_job)
    second = await provider.poll(generation_job)

    assert first is not None and second is not None
    assert first.status == ProviderWebhookStatus.PROCESSING
    assert first.progress == 0
    assert first.event_id == second.event_id
    assert transport.requests[0].url.endswith("/api/v1/tasks/task%20with%2Fslash")
    assert transport.requests[0].headers == {"Authorization": "Bearer secret"}
    assert transport.requests[0].json is None


@pytest.mark.asyncio
async def test_poll_maps_success_to_video_asset() -> None:
    provider = AlibabaWanProviderAdapter(
        api_key="secret",
        transport=FakeTransport(
            response(
                200,
                {
                    "request_id": "poll-2",
                    "output": {
                        "task_id": "task-success",
                        "task_status": "SUCCEEDED",
                        "video_url": "https://results.example.test/output.mp4?Expires=1",
                    },
                    "usage": {"duration": 7, "video_count": 1},
                },
            )
        ),
    )
    generation_job = job()
    generation_job.provider_task_id = "task-success"

    event = await provider.poll(generation_job)

    assert event is not None
    assert event.status == ProviderWebhookStatus.SUCCEEDED
    assert event.progress == 100
    assert len(event.outputs) == 1
    assert str(event.outputs[0].url) == (
        "https://results.example.test/output.mp4?Expires=1"
    )
    assert event.outputs[0].content_type == "video/mp4"


@pytest.mark.asyncio
async def test_poll_failed_task_maps_error_without_exposing_provider_message() -> None:
    secret = "sk-error-secret"
    failed = response(
        200,
        {
            "request_id": "poll-3",
            "output": {
                "task_id": "task-failed",
                "task_status": "FAILED",
                "code": "InternalError.Timeout",
                "message": f"diagnostic included {secret}",
            },
        },
    )
    provider = AlibabaWanProviderAdapter(
        api_key=secret, transport=FakeTransport(failed, failed)
    )
    generation_job = job()
    generation_job.provider_task_id = "task-failed"

    first = await provider.poll(generation_job)
    second = await provider.poll(generation_job)

    assert first is not None and second is not None
    assert first.event_id == second.event_id
    assert first.status == ProviderWebhookStatus.FAILED
    assert first.error is not None
    assert first.error.code == "ALIBABA_WAN_INTERNAL_TIMEOUT"
    assert first.error.retryable is True
    assert secret not in first.error.message


@pytest.mark.asyncio
async def test_poll_unknown_task_is_terminal_and_not_retryable() -> None:
    provider = AlibabaWanProviderAdapter(
        api_key="secret",
        transport=FakeTransport(
            response(
                200,
                {
                    "output": {
                        "task_id": "expired-task",
                        "task_status": "UNKNOWN",
                    }
                },
            )
        ),
    )
    generation_job = job()
    generation_job.provider_task_id = "expired-task"

    event = await provider.poll(generation_job)

    assert event is not None and event.error is not None
    assert event.status == ProviderWebhookStatus.FAILED
    assert event.error.code == "ALIBABA_WAN_TASK_UNKNOWN"
    assert event.error.retryable is False


@pytest.mark.asyncio
async def test_query_transport_failure_is_retryable_and_hides_secret() -> None:
    secret = "sk-query-secret"
    provider = AlibabaWanProviderAdapter(
        api_key=secret,
        transport=FakeTransport(TimeoutError(f"failed with {secret}")),
    )
    generation_job = job()
    generation_job.provider_task_id = "task-4"

    with pytest.raises(ProviderError) as caught:
        await provider.poll(generation_job)

    assert caught.value.code == "ALIBABA_WAN_QUERY_UNAVAILABLE"
    assert caught.value.retryable is True
    assert secret not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"output": {"task_id": "different", "task_status": "RUNNING"}},
        {"output": {"task_id": "task-5", "task_status": "FINISHED"}},
        {"output": {"task_id": "task-5", "task_status": "SUCCEEDED"}},
        {
            "output": {
                "task_id": "task-5",
                "task_status": "SUCCEEDED",
                "video_url": "not-a-url",
            }
        },
    ],
)
async def test_poll_strictly_rejects_invalid_success_payloads(
    body: dict[str, Any],
) -> None:
    provider = AlibabaWanProviderAdapter(
        api_key="secret", transport=FakeTransport(response(200, body))
    )
    generation_job = job()
    generation_job.provider_task_id = "task-5"

    with pytest.raises(ProviderError) as caught:
        await provider.poll(generation_job)

    assert caught.value.code == "ALIBABA_WAN_RESPONSE_INVALID"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_close_delegates_to_injected_transport() -> None:
    transport = FakeTransport()
    provider = AlibabaWanProviderAdapter(api_key="secret", transport=transport)

    await provider.close()

    assert transport.closed is True


def test_environment_factory_requires_region_and_limits_advertised_models(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALIBABA_WAN_API_KEY", "secret")
    monkeypatch.setenv(
        "ALIBABA_WAN_BASE_URL",
        "https://workspace.cn-beijing.maas.aliyuncs.com",
    )
    monkeypatch.setenv("ALIBABA_WAN_MODELS", "wan2.7-t2v")

    provider = create_alibaba_wan_provider()

    assert isinstance(provider, AlibabaWanProviderAdapter)
    assert provider._models == {"wan2.7-t2v"}


@pytest.mark.asyncio
async def test_submit_rejects_non_https_media_url() -> None:
    transport = FakeTransport()
    provider = AlibabaWanProviderAdapter(api_key="secret", transport=transport)
    audio = AssetInput(
        url="http://assets.example.test/narration.mp3", media_type="audio"
    )

    with pytest.raises(ProviderError) as caught:
        await provider.submit(job(assets=[audio]))

    assert caught.value.code == "ALIBABA_WAN_INPUT_URL_INVALID"
    assert transport.requests == []
