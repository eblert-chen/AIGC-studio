"""Copyable Relay adapter template for a company-delivered async API client.

This file is not loaded by Relay. Copy it into ``relay_service/providers`` (or
an installed external Python package), replace the example client factory and
capabilities, then run the conformance command documented in
``docs/provider-adapter-v1.md``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from relay_service.models import (
    ErrorDetail,
    GenerationJob,
    ModelCapability,
    ProviderAsset,
    ProviderWebhookEvent,
    ProviderWebhookStatus,
)
from relay_service.providers import (
    ProviderAdapter,
    ProviderChannelType,
    ProviderError,
    ProviderSubmission,
)


class DeliveredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    media_type: Literal["image", "video"]
    content_type: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_https(self):
        if self.url.scheme != "https":
            raise ValueError("delivered output URLs must use HTTPS")
        return self


class DeliveredTaskState(BaseModel):
    """Strict state returned by the reviewed company API wrapper."""

    model_config = ConfigDict(extra="forbid")

    provider_task_id: str = Field(min_length=1, max_length=256)
    correlation_id: str = Field(min_length=1, max_length=128)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int | None = Field(default=None, ge=0, le=100)
    outputs: tuple[DeliveredOutput, ...] = ()
    error_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_terminal_payload(self):
        if self.status == "succeeded" and not self.outputs:
            raise ValueError("a succeeded task must contain outputs")
        if self.status != "succeeded" and self.outputs:
            raise ValueError("only a succeeded task may contain outputs")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("a failed task must contain an error code")
        if self.status != "failed" and self.error_code is not None:
            raise ValueError("only a failed task may contain an error code")
        return self


class DeliveredRequestRejected(Exception):
    """The upstream proved that no task was created for this request."""


class DeliveredAccountUnavailable(Exception):
    """The account cannot accept a new task and creation was not attempted."""

    def __init__(self, *, permanent: bool = False) -> None:
        self.permanent = permanent


class DeliveredSubmissionOutcomeUnknown(Exception):
    """The create request may have committed and must be reconciled."""


class DeliveredQueryTemporarilyUnavailable(Exception):
    """Polling failed without changing the upstream task."""


class DeliveredGenerationClient(Protocol):
    """Small wrapper to implement around the API file supplied by the company."""

    async def healthcheck(self) -> bool: ...

    async def create_task(
        self,
        *,
        correlation_id: str,
        model: str,
        mode: str,
        prompt: str,
        assets: list[dict[str, str]],
        output: dict[str, object],
    ) -> tuple[str, str]:
        """Return ``(provider_task_id, correlation_id)``."""

    async def get_task(self, provider_task_id: str) -> DeliveredTaskState: ...

    async def close(self) -> None: ...


class CompanyDeliveredApiAdapter(ProviderAdapter):
    """Example reverse-channel wrapper; no Relay upper-layer changes needed."""

    name = "company-delivered-api"
    channel_type = ProviderChannelType.REVERSE
    production_ready = False

    def __init__(
        self,
        *,
        client: DeliveredGenerationClient,
        capabilities: Sequence[ModelCapability],
        account_id: str = "default",
        priority: int = 100,
        max_concurrency: int | None = None,
        requests_per_minute: int | None = None,
    ) -> None:
        self._client = client
        self._capabilities = tuple(
            capability.model_copy(deep=True) for capability in capabilities
        )
        self.account_id = account_id
        self.priority = priority
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute

    async def capabilities(self) -> list[ModelCapability]:
        return [
            capability.model_copy(deep=True)
            for capability in self._capabilities
        ]

    async def healthcheck(self) -> bool:
        try:
            result = await self._client.healthcheck()
        except Exception:
            return False
        return result is True

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        try:
            provider_task_id, correlation_id = await self._client.create_task(
                correlation_id=str(job.id),
                model=job.model,
                mode=job.mode.value,
                prompt=job.inputs.prompt,
                assets=[
                    {"url": str(asset.url), "media_type": asset.media_type}
                    for asset in job.inputs.assets
                ],
                output=job.output.model_dump(mode="json"),
            )
        except DeliveredRequestRejected:
            raise ProviderError(
                "DELIVERED_API_REQUEST_REJECTED",
                "The generation request was rejected before task creation",
                retryable=False,
                account_unavailable=False,
            ) from None
        except DeliveredAccountUnavailable as exc:
            raise ProviderError(
                "DELIVERED_API_ACCOUNT_UNAVAILABLE",
                "The provider account cannot accept a new task",
                retryable=not exc.permanent,
                account_unavailable=True,
                disable_account=exc.permanent,
            ) from None
        except (DeliveredSubmissionOutcomeUnknown, Exception):
            # A create call may have committed before a timeout/disconnect.
            # Never fail over or retry blindly unless the upstream provides a
            # documented idempotency/recovery contract.
            raise ProviderError(
                "DELIVERED_API_SUBMISSION_OUTCOME_UNKNOWN",
                "The generation submission outcome is unknown",
                retryable=False,
                account_unavailable=False,
                submission_outcome_unknown=True,
            ) from None
        if correlation_id != str(job.id):
            raise ProviderError(
                "DELIVERED_API_SUBMISSION_OUTCOME_UNKNOWN",
                "The generation submission correlation could not be verified",
                retryable=False,
                account_unavailable=False,
                submission_outcome_unknown=True,
            )
        return ProviderSubmission(provider_task_id=provider_task_id)

    async def poll(self, job: GenerationJob) -> ProviderWebhookEvent | None:
        if not job.provider_task_id:
            raise ProviderError(
                "DELIVERED_API_TASK_ID_MISSING",
                "The provider task identifier is missing",
                retryable=False,
                fail_job=True,
            )
        try:
            raw_state = await self._client.get_task(job.provider_task_id)
            state = DeliveredTaskState.model_validate(raw_state)
        except DeliveredAccountUnavailable as exc:
            raise ProviderError(
                "DELIVERED_API_ACCOUNT_UNAVAILABLE",
                "The provider account cannot query its existing task",
                retryable=not exc.permanent,
                account_unavailable=True,
                disable_account=exc.permanent,
            ) from None
        except (DeliveredQueryTemporarilyUnavailable, Exception):
            raise ProviderError(
                "DELIVERED_API_QUERY_TEMPORARILY_UNAVAILABLE",
                "The generation task could not be queried",
                retryable=True,
                account_unavailable=False,
            ) from None

        if (
            state.provider_task_id != job.provider_task_id
            or state.correlation_id != str(job.id)
        ):
            raise ProviderError(
                "DELIVERED_API_TASK_IDENTITY_MISMATCH",
                "The provider returned a different task identity",
                retryable=False,
                account_unavailable=False,
            )

        canonical_state = json.dumps(
            state.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        event_id = (
            "delivered-"
            + hashlib.sha256(
                self.route_id.encode("utf-8") + b":" + canonical_state
            ).hexdigest()
        )
        if state.status in {"queued", "running"}:
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=job.provider_task_id,
                status=ProviderWebhookStatus.PROCESSING,
                progress=state.progress,
            )
        if state.status == "cancelled":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=job.provider_task_id,
                status=ProviderWebhookStatus.CANCELLED,
            )
        if state.status == "failed":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=job.provider_task_id,
                status=ProviderWebhookStatus.FAILED,
                error=ErrorDetail(
                    code=state.error_code or "UPSTREAM_GENERATION_FAILED",
                    message="The generation channel reported failure",
                    retryable=False,
                ),
            )
        return ProviderWebhookEvent(
            event_id=event_id,
            provider_task_id=job.provider_task_id,
            status=ProviderWebhookStatus.SUCCEEDED,
            outputs=[
                ProviderAsset(
                    url=str(output.url),
                    media_type=output.media_type,
                    content_type=output.content_type,
                )
                for output in state.outputs
            ],
        )

    async def close(self) -> None:
        await self._client.close()


# The deployable module must expose a zero-argument factory, for example:
#
# def create_adapter() -> ProviderAdapter:
#     client = CompanyApiClient.from_environment()
#     return CompanyDeliveredApiAdapter(
#         client=client,
#         capabilities=capabilities_from_reviewed_configuration(),
#         account_id="cn-a",
#         priority=10,
#         max_concurrency=2,
#         requests_per_minute=30,
#     )
