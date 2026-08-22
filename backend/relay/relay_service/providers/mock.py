from __future__ import annotations

import json
from typing import Mapping

from pydantic import ValidationError

from ..models import (
    CapabilityLimits,
    GenerationJob,
    GenerationMode,
    ModelCapability,
    ProviderWebhookEvent,
)
from .base import (
    ProviderAdapter,
    ProviderChannelType,
    ProviderError,
    ProviderSubmission,
    validate_provider_identity,
)


class MockProviderAdapter(ProviderAdapter):
    """Contract test double. This adapter never calls an external service."""

    name = "mock-video"
    channel_type = ProviderChannelType.MOCK

    def __init__(
        self,
        *,
        webhook_secret: str = "development-only-secret",
        healthy: bool = True,
        fail_submit: bool = False,
        priority: int = 100,
        account_id: str = "default",
        max_concurrency: int | None = None,
        requests_per_minute: int | None = None,
    ) -> None:
        validate_provider_identity(
            name=self.name,
            account_id=account_id,
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
        )
        self.webhook_secret = webhook_secret
        self.healthy = healthy
        self.fail_submit = fail_submit
        self.priority = priority
        self.account_id = account_id
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute

    async def capabilities(self) -> list[ModelCapability]:
        return [
            ModelCapability(
                model="mock.video.v1",
                modes=[
                    GenerationMode.TEXT_TO_IMAGE,
                    GenerationMode.TEXT_TO_VIDEO,
                    GenerationMode.IMAGE_TO_VIDEO,
                    GenerationMode.VIDEO_TO_VIDEO,
                ],
                input_media_types=["image", "video", "audio"],
                supports_face=True,
                limits=CapabilityLimits(
                    max_prompt_length=10_000,
                    max_images=9,
                    max_videos=3,
                    max_audio=3,
                    duration_seconds=[5, 10],
                    aspect_ratios=["16:9", "9:16", "1:1"],
                    resolutions=["720p", "1080p"],
                    output_counts=[1, 2, 3, 4],
                ),
                available_providers=[self.name],
            )
        ]

    async def healthcheck(self) -> bool:
        return self.healthy

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        if self.fail_submit:
            raise ProviderError(
                "PROVIDER_TEMPORARILY_UNAVAILABLE",
                "Mock provider is configured to fail",
                retryable=True,
            )
        return ProviderSubmission(provider_task_id=f"mock-{job.id}")

    async def parse_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> ProviderWebhookEvent:
        if headers.get("x-mock-webhook-secret") != self.webhook_secret:
            raise ProviderError(
                "WEBHOOK_SIGNATURE_INVALID",
                "Mock webhook secret is invalid",
                retryable=False,
            )
        try:
            return ProviderWebhookEvent.model_validate(json.loads(body))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderError(
                "WEBHOOK_PAYLOAD_INVALID",
                "Mock webhook payload is invalid",
                retryable=False,
            ) from exc


def create_mock_provider() -> ProviderAdapter:
    """Factory used only for registry contract tests and development."""

    return MockProviderAdapter()
