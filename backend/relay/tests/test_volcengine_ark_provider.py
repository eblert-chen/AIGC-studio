from __future__ import annotations

import asyncio
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
from relay_service.providers.http import (
    JsonHttpResponse,
    JsonTransportError,
)
from relay_service.providers.volcengine_ark import (
    VolcengineArkProviderAdapter,
    create_volcengine_ark_provider,
)


@dataclass(frozen=True)
class RequestCall:
    method: str
    url: str
    headers: dict[str, str]
    json: dict[str, Any] | None


class FakeTransport:
    def __init__(
        self,
        *responses: JsonHttpResponse,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses)
        self.error = error
        self.calls: list[RequestCall] = []
        self.closed = False
        self.probe_status = 200
        self.probes: list[tuple[str, str, dict[str, str]]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers,
        json=None,
    ) -> JsonHttpResponse:
        self.calls.append(
            RequestCall(
                method=method,
                url=url,
                headers=dict(headers),
                json=deepcopy(json),
            )
        )
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("Fake transport has no response")
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True

    async def probe(self, method, url, *, headers) -> int:
        self.probes.append((method, url, dict(headers)))
        if self.error is not None:
            raise self.error
        return self.probe_status


def adapter(transport: FakeTransport) -> VolcengineArkProviderAdapter:
    return VolcengineArkProviderAdapter(
        api_key="ark-test-secret",
        model_ids={"relay.seedance": "ep-configured-by-operator"},
        transport=transport,
    )


def job(
    *,
    mode: GenerationMode = GenerationMode.TEXT_TO_VIDEO,
    assets: list[AssetInput] | None = None,
    provider_task_id: str | None = None,
    prompt: str = "A cinematic sunrise over the city",
) -> GenerationJob:
    return GenerationJob(
        tenant_id=uuid4(),
        model="relay.seedance",
        mode=mode,
        inputs=GenerationInputs(prompt=prompt, assets=assets or []),
        output=OutputOptions(
            duration_seconds=5,
            aspect_ratio="16:9",
            resolution="720p",
            count=1,
        ),
        provider_task_id=provider_task_id,
    )


@pytest.mark.parametrize(
    ("status", "provider_code", "operation", "account_unavailable"),
    [
        (401, "InvalidParameter", "submit", True),
        (403, "AccessDenied", "poll", True),
        (400, "InvalidParameter", "submit", False),
        (404, "NotFound", "poll", False),
    ],
)
def test_provider_error_classifies_account_and_request_domains(
    status: int,
    provider_code: str,
    operation: str,
    account_unavailable: bool,
) -> None:
    provider = adapter(FakeTransport())
    error = provider._http_error(
        JsonHttpResponse(
            status=status,
            body={"error": {"code": provider_code, "message": "detail"}},
        ),
        operation=operation,
    )

    assert error.account_unavailable is account_unavailable


def test_capabilities_use_injected_model_id_configuration() -> None:
    async def scenario() -> None:
        provider = adapter(FakeTransport())

        capabilities = await provider.capabilities()

        assert provider.production_ready is False
        assert [item.model for item in capabilities] == [
            "relay.seedance",
            "relay.seedance",
        ]
        assert capabilities[0].modes == [GenerationMode.TEXT_TO_VIDEO]
        assert capabilities[0].input_media_types == []
        assert capabilities[0].limits.max_images == 0
        assert capabilities[1].modes == [GenerationMode.IMAGE_TO_VIDEO]
        assert capabilities[1].input_media_types == ["image"]
        assert capabilities[1].limits.max_images == 1
        assert capabilities[0].available_providers == ["volcengine-ark"]

    asyncio.run(scenario())


def test_image_to_video_capability_cannot_advertise_two_images() -> None:
    with pytest.raises(ValueError, match="incompatible with Relay"):
        VolcengineArkProviderAdapter(
            api_key="ark-test-secret",
            model_ids={"relay.seedance": "ep-configured-by-operator"},
            transport=FakeTransport(),
            model_capability_limits={
                "relay.seedance": {
                    "max_prompt_length": 10_000,
                    "max_images": 2,
                    "max_videos": 0,
                    "max_audio": 0,
                    "duration_seconds": [5, 10],
                    "aspect_ratios": ["16:9", "9:16", "1:1"],
                    "resolutions": ["720p", "1080p"],
                    "output_counts": [1],
                }
            },
        )


def test_submit_text_to_video_uses_official_contract_without_callback() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(status=200, body={"id": "cgt-text-1"})
        )
        provider = adapter(transport)
        generation = job()

        result = await provider.submit(generation)

        assert result.provider_task_id == "cgt-text-1"
        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call.method == "POST"
        assert call.url == (
            "https://ark.cn-beijing.volces.com/api/v3/contents/"
            "generations/tasks"
        )
        assert call.headers == {
            "Authorization": "Bearer ark-test-secret",
            "Content-Type": "application/json",
        }
        assert call.json == {
            "model": "ep-configured-by-operator",
            "content": [
                {
                    "type": "text",
                    "text": "A cinematic sunrise over the city",
                }
            ],
            "resolution": "720p",
            "ratio": "16:9",
            "duration": 5,
        }
        assert "callback_url" not in call.json
        assert str(generation.id) not in repr(call.json)

    asyncio.run(scenario())


def test_submit_image_to_video_adds_one_image_url() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(status=200, body={"id": "cgt-image-1"})
        )
        provider = adapter(transport)
        generation = job(
            mode=GenerationMode.IMAGE_TO_VIDEO,
            assets=[
                AssetInput(
                    url="https://assets.example.test/first-frame.png",
                    media_type="image",
                )
            ],
        )

        await provider.submit(generation)

        assert transport.calls[0].json is not None
        assert transport.calls[0].json["content"][1] == {
            "type": "image_url",
            "image_url": {
                "url": "https://assets.example.test/first-frame.png"
            },
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("generation", "expected_code"),
    [
        (
            job(
                mode=GenerationMode.IMAGE_TO_VIDEO,
                assets=[],
            ),
            "ARK_IMAGE_COUNT_INVALID",
        ),
        (
            job(
                assets=[
                    AssetInput(
                        url="https://assets.example.test/audio.wav",
                        media_type="audio",
                    )
                ]
            ),
            "ARK_INPUT_NOT_SUPPORTED",
        ),
        (
            job(prompt="A city --ratio 9:16"),
            "ARK_PROMPT_CONTROL_CONFLICT",
        ),
    ],
)
def test_submit_rejects_unsupported_or_conflicting_input(
    generation: GenerationJob,
    expected_code: str,
) -> None:
    async def scenario() -> None:
        provider = adapter(FakeTransport())

        with pytest.raises(ProviderError) as caught:
            await provider.submit(generation)

        assert caught.value.code == expected_code
        assert caught.value.retryable is False

    asyncio.run(scenario())


def test_submit_transport_failure_is_not_safe_to_retry_or_leak() -> None:
    async def scenario() -> None:
        transport = FakeTransport(error=TimeoutError("ark-test-secret"))
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.submit(job())

        assert caught.value.code == "ARK_SUBMISSION_OUTCOME_UNKNOWN"
        assert caught.value.retryable is False
        assert "ark-test-secret" not in str(caught.value)

    asyncio.run(scenario())


def test_submit_malformed_success_is_an_ambiguous_non_retryable_outcome() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(status=200, body={"unexpected": "body"})
        )
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.submit(job())

        assert caught.value.code == "ARK_SUBMISSION_OUTCOME_UNKNOWN"
        assert caught.value.retryable is False

    asyncio.run(scenario())


def test_only_documented_quota_rejection_is_retryable_on_submit() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(
                status=429,
                body={
                    "error": {
                        "code": "QuotaExceeded",
                        "message": "sensitive upstream detail",
                    }
                },
            )
        )
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.submit(job())

        assert caught.value.code == "ARK_QUOTA_EXCEEDED"
        assert caught.value.retryable is True
        assert "sensitive upstream detail" not in str(caught.value)

    asyncio.run(scenario())


def test_poll_processing_event_id_is_stable() -> None:
    async def scenario() -> None:
        body = {
            "id": "cgt-running-1",
            "status": "running",
            "updated_at": 1_755_000_123,
        }
        transport = FakeTransport(
            JsonHttpResponse(status=200, body=body),
            JsonHttpResponse(status=200, body=body),
        )
        provider = adapter(transport)
        generation = job(provider_task_id="cgt-running-1")

        first = await provider.poll(generation)
        second = await provider.poll(generation)

        assert first is not None
        assert second is not None
        assert first.event_id == second.event_id
        assert first.status == ProviderWebhookStatus.PROCESSING
        assert first.provider_task_id == "cgt-running-1"
        assert transport.calls[0].method == "GET"
        assert transport.calls[0].json is None
        assert transport.calls[0].url.endswith(
            "/contents/generations/tasks/cgt-running-1"
        )

    asyncio.run(scenario())


def test_poll_succeeded_maps_video_for_immediate_transfer() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(
                status=200,
                body={
                    "id": "cgt-success-1",
                    "status": "succeeded",
                    "updated_at": 1_755_000_124,
                    "content": {
                        "video_url": "https://ark.example.test/result.mp4"
                    },
                },
            )
        )
        provider = adapter(transport)

        event = await provider.poll(
            job(provider_task_id="cgt-success-1")
        )

        assert event is not None
        assert event.status == ProviderWebhookStatus.SUCCEEDED
        assert len(event.outputs) == 1
        assert str(event.outputs[0].url) == (
            "https://ark.example.test/result.mp4"
        )
        assert event.outputs[0].content_type == "video/mp4"

    asyncio.run(scenario())


def test_poll_failed_maps_code_without_exposing_provider_message() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(
                status=200,
                body={
                    "id": "cgt-failed-1",
                    "status": "failed",
                    "updated_at": 1_755_000_125,
                    "error": {
                        "code": "OutputVideoSensitiveContentDetected",
                        "message": "request contained ark-test-secret",
                    },
                },
            )
        )
        provider = adapter(transport)

        event = await provider.poll(
            job(provider_task_id="cgt-failed-1")
        )

        assert event is not None
        assert event.status == ProviderWebhookStatus.FAILED
        assert event.error is not None
        assert event.error.code == (
            "ARK_OUTPUT_VIDEO_SENSITIVE_CONTENT_DETECTED"
        )
        assert event.error.details == {
            "provider_code": "OutputVideoSensitiveContentDetected"
        }
        assert "ark-test-secret" not in event.model_dump_json()

    asyncio.run(scenario())


def test_poll_cancelled_maps_terminal_status() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(
                status=200,
                body={
                    "id": "cgt-cancelled-1",
                    "status": "cancelled",
                    "updated_at": 1_755_000_126,
                },
            )
        )
        provider = adapter(transport)

        event = await provider.poll(
            job(provider_task_id="cgt-cancelled-1")
        )

        assert event is not None
        assert event.status == ProviderWebhookStatus.CANCELLED

    asyncio.run(scenario())


def test_poll_requires_valid_task_id_without_calling_transport() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.poll(job(provider_task_id=None))

        assert caught.value.code == "ARK_TASK_ID_INVALID"
        assert caught.value.retryable is False
        assert transport.calls == []

    asyncio.run(scenario())


def test_poll_transport_and_malformed_response_failures_are_retryable() -> None:
    async def transport_scenario() -> None:
        transport = FakeTransport(
            error=JsonTransportError("ark-test-secret")
        )
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.poll(job(provider_task_id="cgt-query-1"))

        assert caught.value.code == "ARK_QUERY_TEMPORARILY_UNAVAILABLE"
        assert caught.value.retryable is True
        assert "ark-test-secret" not in str(caught.value)

    async def malformed_scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(
                status=200,
                body={
                    "id": "cgt-query-1",
                    "status": "succeeded",
                    "updated_at": 1_755_000_127,
                },
            )
        )
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.poll(job(provider_task_id="cgt-query-1"))

        assert caught.value.code == "ARK_TASK_RESPONSE_INVALID"
        assert caught.value.retryable is True

    asyncio.run(transport_scenario())
    asyncio.run(malformed_scenario())


def test_poll_rejects_mismatched_task_response() -> None:
    async def scenario() -> None:
        transport = FakeTransport(
            JsonHttpResponse(
                status=200,
                body={
                    "id": "cgt-other-task",
                    "status": "running",
                    "updated_at": 1_755_000_128,
                },
            )
        )
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.poll(job(provider_task_id="cgt-query-2"))

        assert caught.value.code == "ARK_TASK_RESPONSE_INVALID"
        assert caught.value.retryable is True

    asyncio.run(scenario())


def test_webhooks_are_disabled_and_close_delegates_to_transport() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        provider = adapter(transport)

        with pytest.raises(ProviderError) as caught:
            await provider.parse_webhook(
                b'{"id":"untrusted"}', {"authorization": "secret"}
            )

        assert caught.value.code == "ARK_WEBHOOK_NOT_TRUSTED"
        assert caught.value.retryable is False
        await provider.close()
        assert transport.closed is True

    asyncio.run(scenario())


def test_healthcheck_uses_official_root_ping_without_parsing_body() -> None:
    async def scenario() -> None:
        healthy_transport = FakeTransport()
        provider = adapter(healthy_transport)
        failed_transport = FakeTransport(
            error=JsonTransportError("network unavailable")
        )

        assert await provider.healthcheck() is True
        assert healthy_transport.probes == [
            (
                "GET",
                "https://ark.cn-beijing.volces.com/ping",
                {
                    "Authorization": "Bearer ark-test-secret",
                    "Content-Type": "application/json",
                },
            )
        ]
        assert await adapter(failed_transport).healthcheck() is False

    asyncio.run(scenario())


def test_environment_factory_uses_explicit_model_mapping(monkeypatch) -> None:
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "secret")
    monkeypatch.setenv(
        "VOLCENGINE_ARK_MODEL_IDS_JSON",
        '{"seedance.standard":"ep-account-specific"}',
    )
    monkeypatch.setenv(
        "VOLCENGINE_ARK_MODEL_MODES_JSON",
        '{"seedance.standard":["text_to_video"]}',
    )
    monkeypatch.setenv(
        "VOLCENGINE_ARK_MODEL_CAPABILITIES_JSON",
        '{"seedance.standard":{"max_prompt_length":10000,'
        '"max_images":0,"max_videos":0,"max_audio":0,'
        '"duration_seconds":[5],"aspect_ratios":["16:9"],'
        '"resolutions":["720p"],"output_counts":[1]}}',
    )

    provider = create_volcengine_ark_provider()

    assert isinstance(provider, VolcengineArkProviderAdapter)
    assert provider._model_ids == {
        "seedance.standard": "ep-account-specific"
    }


def test_submit_rejects_non_https_image_url() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        provider = adapter(transport)
        generation = job(
            mode=GenerationMode.IMAGE_TO_VIDEO,
            assets=[
                AssetInput(
                    url="http://assets.example.test/frame.png",
                    media_type="image",
                )
            ],
        )

        with pytest.raises(ProviderError) as caught:
            await provider.submit(generation)

        assert caught.value.code == "ARK_INPUT_URL_INVALID"
        assert transport.calls == []

    asyncio.run(scenario())
