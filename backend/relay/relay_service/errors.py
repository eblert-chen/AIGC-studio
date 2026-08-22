from __future__ import annotations

from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .models import (
    PUBLIC_API_VERSION,
    PUBLIC_SCHEMA_VERSION,
    PublicAsyncErrorCode,
    PublicErrorDetail,
)


class RelayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    error: ErrorBody


PUBLIC_ASYNC_ERROR_REGISTRY: dict[PublicAsyncErrorCode, str] = {
    PublicAsyncErrorCode.MODEL_CAPABILITY_UNAVAILABLE: (
        "Model capability is no longer available"
    ),
    PublicAsyncErrorCode.CAPABILITY_REVISION_MISMATCH: (
        "Model capabilities changed; refresh the catalog and resubmit"
    ),
    PublicAsyncErrorCode.REQUEST_NOT_SUPPORTED_BY_MODEL: (
        "Generation request is not supported by the selected model"
    ),
    PublicAsyncErrorCode.MODE_NOT_SUPPORTED_BY_MODEL: (
        "Generation mode is not supported by the selected model"
    ),
    PublicAsyncErrorCode.NO_PROVIDER_AVAILABLE: (
        "No generation channel is currently available"
    ),
    PublicAsyncErrorCode.PROVIDER_ACCOUNT_POOL_BUSY: (
        "All compatible generation channels are busy"
    ),
    PublicAsyncErrorCode.PROVIDER_ACCOUNT_POOL_RATE_LIMITED: (
        "Compatible generation channels are temporarily rate limited"
    ),
    PublicAsyncErrorCode.PROVIDER_TASK_NOT_ASSIGNED: (
        "Generation task is not assigned to a channel"
    ),
    PublicAsyncErrorCode.PROVIDER_NOT_FOUND: (
        "The assigned generation channel is unavailable"
    ),
    PublicAsyncErrorCode.PROVIDER_CIRCUIT_OPEN: (
        "Generation status polling is temporarily paused"
    ),
    PublicAsyncErrorCode.PROVIDER_POLL_FAILED: (
        "Generation task status could not be queried"
    ),
    PublicAsyncErrorCode.PROVIDER_TASK_MISMATCH: (
        "The generation channel returned an unexpected task identifier"
    ),
    PublicAsyncErrorCode.PROVIDER_TASK_ID_INVALID: (
        "The generation task identifier is invalid"
    ),
    PublicAsyncErrorCode.UPSTREAM_FAILED: (
        "Generation failed in the upstream channel"
    ),
    PublicAsyncErrorCode.CONTENT_POLICY_REJECTED: (
        "Generation content was rejected by safety policy"
    ),
    PublicAsyncErrorCode.INPUT_ASSET_UNAVAILABLE: (
        "An input asset could not be accepted"
    ),
    PublicAsyncErrorCode.GENERATION_FAILED: "Generation failed",
    PublicAsyncErrorCode.GENERATION_TASK_NOT_FOUND_UPSTREAM: (
        "The upstream generation task could not be found"
    ),
    PublicAsyncErrorCode.GENERATION_CHANNEL_RESPONSE_INVALID: (
        "The generation channel returned an invalid response"
    ),
    PublicAsyncErrorCode.GENERATION_CHANNEL_UNAVAILABLE: (
        "The generation channel is unavailable"
    ),
    PublicAsyncErrorCode.ARTIFACT_TRANSFER_RETRYING: (
        "Artifact transfer will be retried"
    ),
    PublicAsyncErrorCode.ARTIFACT_TRANSFER_FAILED: (
        "Generated artifacts could not be stored safely"
    ),
    PublicAsyncErrorCode.SUBMISSION_RECONCILIATION_REQUIRED: (
        "Provider submission outcome requires reconciliation"
    ),
    PublicAsyncErrorCode.SUBMISSION_CONFIRMED_NOT_CREATED: (
        "Provider confirmed that no generation task was created"
    ),
    PublicAsyncErrorCode.PROVIDER_RETRIES_EXHAUSTED: (
        "Provider submission retries were exhausted"
    ),
    PublicAsyncErrorCode.WORKER_ATTEMPTS_EXHAUSTED: (
        "Worker could not complete this task"
    ),
    PublicAsyncErrorCode.PROVIDER_POLL_RECONCILIATION_REQUIRED: (
        "Provider polling cannot safely determine the task state"
    ),
}

if set(PUBLIC_ASYNC_ERROR_REGISTRY) != set(PublicAsyncErrorCode):
    raise RuntimeError("Public async error registry is incomplete")


def public_async_error(
    code: PublicAsyncErrorCode,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> PublicErrorDetail:
    return PublicErrorDetail(
        code=code,
        message=PUBLIC_ASYNC_ERROR_REGISTRY[code],
        retryable=retryable,
        details=details or {},
    )


def public_generation_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> PublicErrorDetail:
    """Remove provider identity and diagnostics from caller-visible failures."""

    normalized = code.upper()
    if normalized in PublicAsyncErrorCode._value2member_map_:
        return public_async_error(
            PublicAsyncErrorCode(normalized),
            retryable=retryable,
            details=details,
        )
    if "POLICY" in normalized or "INSPECTION" in normalized:
        public_code = "CONTENT_POLICY_REJECTED"
        public_message = "Generation content was rejected by safety policy"
    elif "INPUT_URL" in normalized or "INPUT_ASSET" in normalized:
        public_code = "INPUT_ASSET_UNAVAILABLE"
        public_message = "An input asset could not be accepted"
    elif any(
        marker in normalized
        for marker in (
            "INVALID_REQUEST",
            "INVALID_PARAMETER",
            "MODEL_NOT_CONFIGURED",
            "MODEL_UNSUPPORTED",
            "MODE_NOT_CONFIGURED",
            "MODE_NOT_SUPPORTED",
            "PROMPT_",
            "DURATION_",
            "RESOLUTION_",
            "OUTPUT_COUNT_",
            "IMAGE_COUNT_",
            "INPUT_NOT_SUPPORTED",
            "INVALID_INPUT_MEDIA",
        )
    ):
        public_code = "REQUEST_NOT_SUPPORTED_BY_MODEL"
        public_message = "Generation request is not supported by the selected model"
    elif "GENERATION_FAILED" in normalized:
        public_code = "GENERATION_FAILED"
        public_message = "Generation failed"
    elif "TASK_NOT_FOUND" in normalized or "TASK_UNKNOWN" in normalized:
        public_code = "GENERATION_TASK_NOT_FOUND_UPSTREAM"
        public_message = "The upstream generation task could not be found"
    elif "RESPONSE_INVALID" in normalized:
        public_code = "GENERATION_CHANNEL_RESPONSE_INVALID"
        public_message = "The generation channel returned an invalid response"
    else:
        public_code = "GENERATION_CHANNEL_UNAVAILABLE"
        public_message = "The generation channel is unavailable"
    return public_async_error(
        PublicAsyncErrorCode(public_code),
        retryable=retryable,
    )


async def relay_error_handler(request: Request, exc: RelayError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    body = ErrorEnvelope(
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            request_id=request_id,
            details=exc.details,
        )
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))
