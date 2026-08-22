from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    model_validator,
)

from ..models import (
    CapabilityLimits,
    ErrorDetail,
    GenerationJob,
    GenerationMode,
    ModelCapability,
    ProviderAsset,
    ProviderWebhookEvent,
    ProviderWebhookStatus,
)
from .base import (
    ProviderAdapter,
    ProviderChannelType,
    ProviderError,
    ProviderFailureScope,
    ProviderSubmission,
    validate_provider_identity,
)
from .http import (
    AioHttpJsonTransport,
    JsonHttpResponse,
    JsonHttpTransport,
    JsonTransportError,
)


_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PROVIDER_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_CONTROL_PATTERN = re.compile(
    r"(?<!\S)--(?:dur|duration|ratio|resolution)(?=\s|=|$)",
    re.IGNORECASE,
)


class _ArkSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1, max_length=256)


class _ArkTaskError(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    )
    message: str = Field(min_length=1, max_length=4_096)


class _ArkTaskContent(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    video_url: HttpUrl | None = None


class _ArkTaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1, max_length=256)
    status: Literal[
        "queued",
        "running",
        "cancelled",
        "succeeded",
        "failed",
    ]
    updated_at: int = Field(ge=0)
    content: _ArkTaskContent | None = None
    error: _ArkTaskError | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "_ArkTaskResponse":
        if self.status == "succeeded" and (
            self.content is None or self.content.video_url is None
        ):
            raise ValueError("succeeded Ark task requires video_url")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed Ark task requires error")
        return self


class VolcengineArkProviderAdapter(ProviderAdapter):
    """Polling-first adapter for Ark asynchronous video generation.

    Ark's public create contract does not expose an idempotency key. A failed
    POST whose outcome is unknown is consequently terminal for automatic
    retries: repeating it could create and charge for a second generation.
    """

    name = "volcengine-ark"
    channel_type = ProviderChannelType.OFFICIAL
    production_ready = False

    def __init__(
        self,
        *,
        api_key: str,
        model_ids: Mapping[str, str],
        transport: JsonHttpTransport | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        priority: int = 100,
        capability_limits: CapabilityLimits | None = None,
        model_capability_limits: Mapping[
            str, CapabilityLimits | Mapping[str, Any]
        ]
        | None = None,
        model_modes: Mapping[
            str, Sequence[GenerationMode | str]
        ] | None = None,
        timeout_seconds: float = 30,
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
        self._api_key = self._validate_secret(api_key)
        self._base_url = self._validate_base_url(base_url)
        self._model_ids = self._validate_model_ids(model_ids)
        self._transport = transport or AioHttpJsonTransport(
            timeout_seconds=timeout_seconds
        )
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        self.priority = priority
        self.account_id = account_id
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute

        self._model_modes = self._validate_model_modes(model_modes or {})
        default_limits = capability_limits or CapabilityLimits(
            max_prompt_length=10_000,
            max_images=1,
            max_videos=0,
            max_audio=0,
            duration_seconds=[5, 10],
            aspect_ratios=["16:9", "9:16", "1:1"],
            resolutions=["720p", "1080p"],
            output_counts=[1],
        )
        if capability_limits is not None and model_capability_limits is not None:
            raise ValueError(
                "Use either capability_limits or model_capability_limits"
            )
        self._limits_by_model = self._validate_capability_limits(
            model_capability_limits,
            default=default_limits,
        )

    async def capabilities(self) -> list[ModelCapability]:
        capabilities: list[ModelCapability] = []
        for model in sorted(self._model_ids):
            for mode in self._model_modes[model]:
                limits = self._limits_by_model[model].model_copy(deep=True)
                input_media_types: list[str] = []
                if mode == GenerationMode.IMAGE_TO_VIDEO:
                    input_media_types = ["image"]
                else:
                    limits.max_images = 0
                capabilities.append(
                    ModelCapability(
                        model=model,
                        modes=[mode],
                        input_media_types=input_media_types,
                        limits=limits,
                        available_providers=[self.name],
                    )
                )
        return capabilities

    async def healthcheck(self) -> bool:
        parsed = urlsplit(self._base_url)
        ping_url = urlunsplit((parsed.scheme, parsed.netloc, "/ping", "", ""))
        probe = getattr(self._transport, "probe", None)
        if probe is None:
            return False
        try:
            status = await probe("GET", ping_url, headers=self._headers())
        except Exception:
            return False
        return 200 <= status < 300

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        provider_model_id = self._model_ids.get(job.model)
        if provider_model_id is None:
            raise ProviderError(
                "ARK_MODEL_NOT_CONFIGURED",
                "The requested model is not configured for Volcengine Ark",
                retryable=False,
            )
        self._validate_job(job)

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._prompt_with_controls(job),
            }
        ]
        if job.mode == GenerationMode.IMAGE_TO_VIDEO:
            image = job.inputs.assets[0]
            self._validate_media_url(str(image.url))
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": str(image.url)},
                }
            )

        # Do not send callback_url: Ark's public callback contract has no
        # documented signature verification. Polling is the trusted path.
        payload: dict[str, Any] = {
            "model": provider_model_id,
            "content": content,
            "resolution": job.output.resolution,
            "ratio": job.output.aspect_ratio,
            "duration": job.output.duration_seconds,
        }
        try:
            response = await self._transport.request(
                "POST",
                f"{self._base_url}/contents/generations/tasks",
                headers=self._headers(),
                json=payload,
            )
        except JsonTransportError:
            raise self._submission_outcome_unknown() from None
        except Exception:
            # Injected transports may use their native timeout/connection
            # exceptions. They have the same ambiguous POST semantics.
            raise self._submission_outcome_unknown() from None

        if response.status != 200:
            raise self._http_error(response, operation="submit")
        try:
            parsed = _ArkSubmissionResponse.model_validate(response.body)
        except ValidationError:
            raise self._submission_outcome_unknown() from None
        if not _TASK_ID_PATTERN.fullmatch(parsed.id):
            raise self._submission_outcome_unknown()
        return ProviderSubmission(provider_task_id=parsed.id)

    async def poll(
        self, job: GenerationJob
    ) -> ProviderWebhookEvent | None:
        task_id = job.provider_task_id
        if not task_id or not _TASK_ID_PATTERN.fullmatch(task_id):
            raise ProviderError(
                "ARK_TASK_ID_INVALID",
                "A valid Volcengine Ark task ID is required for polling",
                retryable=False,
                fail_job=True,
            )
        try:
            response = await self._transport.request(
                "GET",
                (
                    f"{self._base_url}/contents/generations/tasks/"
                    f"{task_id}"
                ),
                headers=self._headers(),
                json=None,
            )
        except JsonTransportError:
            raise ProviderError(
                "ARK_QUERY_TEMPORARILY_UNAVAILABLE",
                "Volcengine Ark task state could not be queried",
                retryable=True,
            ) from None
        except Exception:
            raise ProviderError(
                "ARK_QUERY_TEMPORARILY_UNAVAILABLE",
                "Volcengine Ark task state could not be queried",
                retryable=True,
            ) from None

        if response.status != 200:
            raise self._http_error(response, operation="poll")
        try:
            task = _ArkTaskResponse.model_validate(response.body)
        except ValidationError:
            raise ProviderError(
                "ARK_TASK_RESPONSE_INVALID",
                "Volcengine Ark returned an invalid task response",
                retryable=True,
            ) from None
        if task.id != task_id:
            raise ProviderError(
                "ARK_TASK_RESPONSE_INVALID",
                "Volcengine Ark returned a mismatched task response",
                retryable=True,
            )
        return self._event_for_task(task)

    async def parse_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> ProviderWebhookEvent:
        del body, headers
        raise ProviderError(
            "ARK_WEBHOOK_NOT_TRUSTED",
            "Volcengine Ark callbacks are disabled; poll the task instead",
            retryable=False,
        )

    async def close(self) -> None:
        await self._transport.close()

    def _validate_job(self, job: GenerationJob) -> None:
        modes = self._model_modes[job.model]
        limits = self._limits_by_model[job.model]
        if job.mode not in modes:
            raise ProviderError(
                "ARK_MODE_NOT_CONFIGURED",
                "The requested mode is not configured for this Ark model",
                retryable=False,
            )
        if job.mode not in {
            GenerationMode.TEXT_TO_VIDEO,
            GenerationMode.IMAGE_TO_VIDEO,
        }:
            raise ProviderError(
                "ARK_MODE_NOT_SUPPORTED",
                "Volcengine Ark adapter supports only text-to-video and "
                "image-to-video",
                retryable=False,
            )
        if len(job.inputs.prompt) > limits.max_prompt_length:
            raise ProviderError(
                "ARK_PROMPT_TOO_LONG",
                "The prompt exceeds the configured Ark model limit",
                retryable=False,
            )
        if _CONTROL_PATTERN.search(job.inputs.prompt):
            raise ProviderError(
                "ARK_PROMPT_CONTROL_CONFLICT",
                "Prompt must not override Relay-controlled video options",
                retryable=False,
            )

        images = [
            asset
            for asset in job.inputs.assets
            if asset.media_type == "image"
        ]
        if len(images) != len(job.inputs.assets):
            raise ProviderError(
                "ARK_INPUT_NOT_SUPPORTED",
                "Volcengine Ark video adapter accepts only image assets",
                retryable=False,
            )
        if job.mode == GenerationMode.TEXT_TO_VIDEO and images:
            raise ProviderError(
                "ARK_INPUT_NOT_SUPPORTED",
                "Text-to-video does not accept image assets",
                retryable=False,
            )
        if job.mode == GenerationMode.IMAGE_TO_VIDEO and len(images) != 1:
            raise ProviderError(
                "ARK_IMAGE_COUNT_INVALID",
                "Image-to-video requires exactly one image asset",
                retryable=False,
            )
        if job.output.count != 1:
            raise ProviderError(
                "ARK_OUTPUT_COUNT_NOT_SUPPORTED",
                "Volcengine Ark creates one video per task",
                retryable=False,
            )
        if job.output.duration_seconds not in limits.duration_seconds:
            raise ProviderError(
                "ARK_DURATION_NOT_CONFIGURED",
                "The requested duration is not configured for this Ark model",
                retryable=False,
            )
        if job.output.aspect_ratio not in limits.aspect_ratios:
            raise ProviderError(
                "ARK_ASPECT_RATIO_NOT_CONFIGURED",
                "The requested aspect ratio is not configured for this Ark "
                "model",
                retryable=False,
            )
        if job.output.resolution not in limits.resolutions:
            raise ProviderError(
                "ARK_RESOLUTION_NOT_CONFIGURED",
                "The requested resolution is not configured for this Ark model",
                retryable=False,
            )

    def _prompt_with_controls(self, job: GenerationJob) -> str:
        return job.inputs.prompt

    def _event_for_task(
        self, task: _ArkTaskResponse
    ) -> ProviderWebhookEvent:
        identity = (
            f"{self.name}:{task.id}:{task.status}:{task.updated_at}"
        )
        event_id = f"ark-{uuid5(NAMESPACE_URL, identity)}"
        if task.status in {"queued", "running"}:
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task.id,
                status=ProviderWebhookStatus.PROCESSING,
            )
        if task.status == "cancelled":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task.id,
                status=ProviderWebhookStatus.CANCELLED,
            )
        if task.status == "succeeded":
            assert task.content is not None
            assert task.content.video_url is not None
            self._validate_result_url(str(task.content.video_url))
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task.id,
                status=ProviderWebhookStatus.SUCCEEDED,
                outputs=[
                    ProviderAsset(
                        url=task.content.video_url,
                        media_type="video",
                        content_type="video/mp4",
                    )
                ],
            )

        assert task.error is not None
        provider_code = task.error.code
        return ProviderWebhookEvent(
            event_id=event_id,
            provider_task_id=task.id,
            status=ProviderWebhookStatus.FAILED,
            error=ErrorDetail(
                code=self._public_error_code(provider_code, 400),
                message="Volcengine Ark reported that generation failed",
                retryable=False,
                details={"provider_code": provider_code},
            ),
        )

    def _http_error(
        self,
        response: JsonHttpResponse,
        *,
        operation: Literal["submit", "poll"],
    ) -> ProviderError:
        provider_code = self._extract_provider_code(response.body)
        public_code = self._public_error_code(
            provider_code, response.status
        )
        if operation == "submit":
            # A documented quota rejection is known not to have created a
            # task. All other non-successful POST outcomes remain fenced from
            # automatic retries because Ark has no public idempotency token.
            retryable = (
                response.status == 429
                and provider_code == "QuotaExceeded"
            )
            message = "Volcengine Ark rejected the generation request"
        else:
            retryable = response.status in {408, 425, 429} or (
                500 <= response.status <= 599
            )
            message = "Volcengine Ark task query failed"
        account_unavailable = (
            True
            if response.status in {401, 403}
            else (None if operation == "submit" else False)
        )
        return ProviderError(
            public_code,
            message,
            retryable=retryable,
            account_unavailable=account_unavailable,
            disable_account=response.status in {401, 403},
            failure_scope=(
                ProviderFailureScope.ACCOUNT
                if operation == "submit"
                and response.status == 429
                and provider_code == "QuotaExceeded"
                else None
            ),
        )

    @staticmethod
    def _extract_provider_code(body: Mapping[str, Any]) -> str | None:
        error = body.get("error")
        candidate: Any = None
        if isinstance(error, Mapping):
            candidate = error.get("code")
        if candidate is None:
            candidate = body.get("code")
        if (
            isinstance(candidate, str)
            and _PROVIDER_CODE_PATTERN.fullmatch(candidate)
        ):
            return candidate
        return None

    @staticmethod
    def _public_error_code(
        provider_code: str | None, status: int
    ) -> str:
        if provider_code is None:
            return f"ARK_HTTP_{status}"
        snake_case = re.sub(
            r"(?<!^)(?=[A-Z])", "_", provider_code
        ).replace("-", "_").replace(".", "_")
        return f"ARK_{snake_case.upper()}"

    @staticmethod
    def _submission_outcome_unknown() -> ProviderError:
        return ProviderError(
            "ARK_SUBMISSION_OUTCOME_UNKNOWN",
            "Volcengine Ark submission outcome is unknown; automatic retry "
            "is unsafe",
            retryable=False,
            submission_outcome_unknown=True,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _validate_secret(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("api_key must be a non-empty header-safe value")
        return value

    @staticmethod
    def _validate_base_url(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("base_url must be a non-empty HTTPS URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api/v3"
        ):
            raise ValueError(
                "base_url must be an HTTPS URL without credentials, query, "
                "or fragment"
            )
        return value.rstrip("/")

    @staticmethod
    def _validate_media_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ProviderError(
                "ARK_INPUT_URL_INVALID",
                "Volcengine Ark input media must use a credential-free HTTPS URL",
                retryable=False,
            )

    @staticmethod
    def _validate_result_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ProviderError(
                "ARK_TASK_RESPONSE_INVALID",
                "Volcengine Ark returned an invalid task response",
                retryable=True,
            )

    @staticmethod
    def _validate_model_ids(
        model_ids: Mapping[str, str]
    ) -> dict[str, str]:
        if not isinstance(model_ids, Mapping) or not model_ids:
            raise ValueError("At least one Ark model ID must be configured")
        validated: dict[str, str] = {}
        for model, provider_model_id in model_ids.items():
            if (
                not isinstance(model, str)
                or not model
                or model != model.strip()
                or len(model) > 128
            ):
                raise ValueError("Relay model names must be non-empty strings")
            if (
                not isinstance(provider_model_id, str)
                or not provider_model_id
                or provider_model_id != provider_model_id.strip()
                or len(provider_model_id) > 256
                or any(
                    ord(character) < 32
                    for character in provider_model_id
                )
            ):
                raise ValueError("Ark model IDs must be non-empty strings")
            validated[model] = provider_model_id
        return validated

    def _validate_model_modes(
        self,
        configured: Mapping[str, Sequence[GenerationMode | str]],
    ) -> dict[str, tuple[GenerationMode, ...]]:
        if not isinstance(configured, Mapping):
            raise ValueError("model_modes must be an object")
        unknown_models = set(configured) - set(self._model_ids)
        if unknown_models:
            raise ValueError("model_modes contains an unknown Relay model")
        result: dict[str, tuple[GenerationMode, ...]] = {}
        defaults = (
            GenerationMode.TEXT_TO_VIDEO,
            GenerationMode.IMAGE_TO_VIDEO,
        )
        for model in self._model_ids:
            raw_modes = configured.get(model, defaults)
            if isinstance(raw_modes, (str, GenerationMode)):
                raw_modes = (raw_modes,)
            try:
                modes = tuple(
                    dict.fromkeys(GenerationMode(mode) for mode in raw_modes)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Ark model modes must be valid generation modes"
                ) from exc
            if not modes or any(mode not in defaults for mode in modes):
                raise ValueError(
                    "Ark model modes support only text-to-video and "
                    "image-to-video"
                )
            result[model] = modes
        return result

    def _validate_capability_limits(
        self,
        configured: Mapping[
            str, CapabilityLimits | Mapping[str, Any]
        ]
        | None,
        *,
        default: CapabilityLimits,
    ) -> dict[str, CapabilityLimits]:
        if configured is None:
            raw_by_model: Mapping[
                str, CapabilityLimits | Mapping[str, Any]
            ] = {model: default for model in self._model_ids}
        else:
            if not isinstance(configured, Mapping) or (
                set(configured) != set(self._model_ids)
            ):
                raise ValueError(
                    "model_capability_limits must configure every Ark model"
                )
            raw_by_model = configured

        result: dict[str, CapabilityLimits] = {}
        allowed_fields = set(CapabilityLimits.model_fields)
        for model, raw in raw_by_model.items():
            if isinstance(raw, Mapping) and set(raw) != allowed_fields:
                raise ValueError(
                    "Ark capability limits must contain the exact schema"
                )
            try:
                limits = (
                    raw.model_copy(deep=True)
                    if isinstance(raw, CapabilityLimits)
                    else CapabilityLimits.model_validate(raw)
                )
            except ValidationError as exc:
                raise ValueError("Ark capability limits are invalid") from exc
            modes = self._model_modes[model]
            if (
                limits.max_prompt_length < 1
                or limits.max_videos != 0
                or limits.max_audio != 0
                or limits.output_counts != [1]
                or not limits.duration_seconds
                or any(not 1 <= value <= 60 for value in limits.duration_seconds)
                or not limits.aspect_ratios
                or not set(limits.aspect_ratios) <= {"16:9", "9:16", "1:1"}
                or not limits.resolutions
                or not set(limits.resolutions) <= {"720p", "1080p"}
                or (
                    GenerationMode.IMAGE_TO_VIDEO in modes
                    and limits.max_images != 1
                )
                or (
                    GenerationMode.IMAGE_TO_VIDEO not in modes
                    and limits.max_images != 0
                )
            ):
                raise ValueError(
                    "Ark capability limits are incompatible with Relay"
                )
            result[model] = limits
        return result


def create_volcengine_ark_provider() -> ProviderAdapter:
    """Environment factory used by ``RELAY_PROVIDER_FACTORIES`` in staging."""

    api_key = os.getenv("VOLCENGINE_ARK_API_KEY", "")
    raw_model_ids = os.getenv("VOLCENGINE_ARK_MODEL_IDS_JSON", "")
    raw_capabilities = os.getenv(
        "VOLCENGINE_ARK_MODEL_CAPABILITIES_JSON", ""
    )
    if not api_key or not raw_model_ids or not raw_capabilities:
        raise RuntimeError(
            "Volcengine Ark key, model IDs, and capabilities are required"
        )
    try:
        model_ids = json.loads(raw_model_ids)
        raw_modes = os.getenv("VOLCENGINE_ARK_MODEL_MODES_JSON")
        model_modes = json.loads(raw_modes) if raw_modes else None
        model_capability_limits = json.loads(raw_capabilities)
        priority = int(os.getenv("VOLCENGINE_ARK_PRIORITY", "100"))
        timeout_seconds = float(
            os.getenv("VOLCENGINE_ARK_TIMEOUT_SECONDS", "30")
        )
        raw_max_concurrency = os.getenv(
            "VOLCENGINE_ARK_MAX_CONCURRENCY", ""
        )
        max_concurrency = (
            int(raw_max_concurrency) if raw_max_concurrency else None
        )
        raw_requests_per_minute = os.getenv(
            "VOLCENGINE_ARK_REQUESTS_PER_MINUTE", ""
        )
        requests_per_minute = (
            int(raw_requests_per_minute) if raw_requests_per_minute else None
        )
        return VolcengineArkProviderAdapter(
            api_key=api_key,
            model_ids=model_ids,
            model_modes=model_modes,
            model_capability_limits=model_capability_limits,
            base_url=os.getenv("VOLCENGINE_ARK_BASE_URL", _DEFAULT_BASE_URL),
            priority=priority,
            timeout_seconds=timeout_seconds,
            account_id=os.getenv("VOLCENGINE_ARK_ACCOUNT_ID", "default"),
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "Volcengine Ark provider configuration is invalid"
        ) from exc


__all__ = [
    "VolcengineArkProviderAdapter",
    "create_volcengine_ark_provider",
]
