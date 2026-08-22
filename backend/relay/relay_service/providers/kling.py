from __future__ import annotations

from collections.abc import Mapping
import json
import os
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


_DEFAULT_BASE_URL = "https://api-singapore.klingai.com"
_OFFICIAL_MODEL_PATHS = frozenset({"kling-3.0", "kling-3.0-turbo"})
_DEFAULT_MODEL_ALIASES = {
    "kling-3.0": "kling-3.0",
    "kling-3.0-turbo": "kling-3.0-turbo",
}
_TASK_STATUSES = frozenset(
    {"submitted", "processing", "succeeded", "failed"}
)
_RETRYABLE_QUERY_HTTP_STATUSES = frozenset({408, 425, 429})
_RETRYABLE_SUBMIT_CODES = frozenset({1302, 1303})
_SERVER_ERROR_CODES = frozenset({5000, 5001, 5002})
_ACCOUNT_UNAVAILABLE_CODES = frozenset(
    {
        1000,
        1001,
        1002,
        1003,
        1004,
        1100,
        1101,
        1102,
        1103,
        1304,
    }
)
_PUBLIC_ERROR_CODES = {
    1000: "KLING_AUTHENTICATION_FAILED",
    1001: "KLING_AUTHORIZATION_MISSING",
    1002: "KLING_AUTHORIZATION_INVALID",
    1003: "KLING_AUTHORIZATION_NOT_YET_VALID",
    1004: "KLING_AUTHORIZATION_EXPIRED",
    1100: "KLING_ACCOUNT_EXCEPTION",
    1101: "KLING_ACCOUNT_IN_ARREARS",
    1102: "KLING_RESOURCE_PACKAGE_EXHAUSTED",
    1103: "KLING_RESOURCE_FORBIDDEN",
    1200: "KLING_INVALID_REQUEST",
    1201: "KLING_INVALID_PARAMETER",
    1202: "KLING_METHOD_NOT_FOUND",
    1203: "KLING_RESOURCE_NOT_FOUND",
    1300: "KLING_POLICY_REJECTED",
    1301: "KLING_CONTENT_POLICY_REJECTED",
    1302: "KLING_RATE_LIMITED",
    1303: "KLING_CONCURRENCY_LIMITED",
    1304: "KLING_IP_NOT_ALLOWED",
    5000: "KLING_INTERNAL_ERROR",
    5001: "KLING_SERVICE_UNAVAILABLE",
    5002: "KLING_INTERNAL_TIMEOUT",
}


class _KlingSubmissionData(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1, max_length=256)
    status: Literal["submitted", "processing", "succeeded", "failed"]
    external_id: str = Field(min_length=1, max_length=128)


class _KlingOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    type: str = Field(min_length=1, max_length=32)
    id: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=8_192)


class _KlingTask(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1, max_length=256)
    status: Literal["submitted", "processing", "succeeded", "failed"]
    message: str | None = Field(default=None, max_length=4_096)
    create_time: int = Field(ge=0)
    update_time: int = Field(ge=0)
    external_id: str | None = Field(default=None, max_length=128)
    outputs: list[_KlingOutput] = Field(default_factory=list)


class KlingProviderAdapter(ProviderAdapter):
    """Polling-first adapter for Kling's current model-version-in-path API.

    Kling documents ``external_task_id`` as a unique correlation identifier,
    not as an idempotency guarantee. Every submit therefore performs a lookup
    first. A transport failure after POST remains outcome-unknown and is never
    automatically retryable, because another POST could create a second paid
    task.
    """

    name = "kling"
    channel_type = ProviderChannelType.OFFICIAL
    production_ready = False

    def __init__(
        self,
        *,
        api_key: str,
        model_aliases: Mapping[str, str] | None = None,
        transport: JsonHttpTransport | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        priority: int = 100,
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
        self._model_aliases = self._validate_model_aliases(
            _DEFAULT_MODEL_ALIASES
            if model_aliases is None
            else model_aliases
        )
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        self.priority = priority
        self.account_id = account_id
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute
        self._transport = transport or AioHttpJsonTransport(
            timeout_seconds=timeout_seconds
        )

    async def capabilities(self) -> list[ModelCapability]:
        image_to_video_limits = CapabilityLimits(
            max_prompt_length=3_072,
            max_images=2,
            max_videos=0,
            max_audio=0,
            duration_seconds=list(range(3, 16)),
            aspect_ratios=["16:9", "9:16", "1:1"],
            # Keep this adapter's advertised set aligned with the currently
            # verified provider request mapping; the Relay contract itself is
            # intentionally wider.
            resolutions=["720p", "1080p"],
            output_counts=[1],
        )
        text_to_video_limits = image_to_video_limits.model_copy(
            deep=True, update={"max_images": 0}
        )
        capabilities: list[ModelCapability] = []
        for model_alias in sorted(self._model_aliases):
            capabilities.extend(
                [
                    ModelCapability(
                        model=model_alias,
                        modes=[GenerationMode.TEXT_TO_VIDEO],
                        input_media_types=[],
                        limits=text_to_video_limits.model_copy(deep=True),
                        available_providers=[self.name],
                    ),
                    ModelCapability(
                        model=model_alias,
                        modes=[GenerationMode.IMAGE_TO_VIDEO],
                        input_media_types=["image"],
                        limits=image_to_video_limits.model_copy(deep=True),
                        available_providers=[self.name],
                    ),
                ]
            )
        return capabilities

    async def healthcheck(self) -> bool:
        # Kling documents no non-billable health endpoint. Constructor
        # validation establishes local readiness; request-time failures remain
        # explicit and sanitized.
        return True

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        path, payload = self._submission_request(job)

        recovered = await self._recover_submission(job)
        if recovered is not None:
            return recovered

        try:
            response = await self._transport.request(
                "POST",
                f"{self._base_url}{path}",
                headers=self._headers(),
                json=payload,
            )
        except JsonTransportError:
            raise self._submission_outcome_unknown() from None
        except Exception:
            # Injected transports may use native timeout/connection exception
            # classes. Their messages may contain credentials or URLs.
            raise self._submission_outcome_unknown() from None

        status, body = self._response_parts(response, operation="submit")
        provider_code = self._provider_code(body)
        if provider_code is None:
            raise self._submission_outcome_unknown()
        if not 200 <= status < 300 or provider_code != 0:
            if status >= 500 or provider_code in _SERVER_ERROR_CODES:
                raise self._submission_outcome_unknown()
            raise self._provider_response_error(
                status, provider_code, operation="submit"
            )

        data = body.get("data")
        try:
            task = _KlingSubmissionData.model_validate(data)
        except ValidationError:
            raise self._submission_outcome_unknown() from None
        external_id = str(job.id)
        if (
            not self._valid_identifier(task.id)
            or task.status not in _TASK_STATUSES
            or task.external_id != external_id
        ):
            raise self._submission_outcome_unknown()
        return ProviderSubmission(provider_task_id=task.id)

    async def poll(
        self, job: GenerationJob
    ) -> ProviderWebhookEvent | None:
        task_id = job.provider_task_id
        if not isinstance(task_id, str) or not self._valid_identifier(task_id):
            raise ProviderError(
                "KLING_TASK_ID_INVALID",
                "A valid Kling task ID is required for polling",
                retryable=False,
                fail_job=True,
            )

        tasks = await self._query_tasks(
            query_name="task_ids",
            query_value=task_id,
            operation="poll",
        )
        if not tasks:
            raise ProviderError(
                "KLING_TASK_NOT_FOUND",
                "Kling task is not visible yet",
                retryable=True,
            )
        if len(tasks) != 1:
            raise self._invalid_query_response(operation="poll")
        task = tasks[0]
        if task.id != task_id or task.external_id != str(job.id):
            raise self._invalid_query_response(operation="poll")
        return self._event_for_task(task)

    async def parse_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> ProviderWebhookEvent:
        del body, headers
        raise ProviderError(
            "KLING_WEBHOOK_NOT_TRUSTED",
            "Kling callbacks are disabled; poll the task instead",
            retryable=False,
        )

    async def close(self) -> None:
        await self._transport.close()

    def _submission_request(
        self, job: GenerationJob
    ) -> tuple[str, dict[str, Any]]:
        provider_model = self._model_aliases.get(job.model)
        if provider_model is None:
            raise ProviderError(
                "KLING_MODEL_NOT_CONFIGURED",
                "The requested model is not configured for Kling",
                retryable=False,
            )
        if job.mode not in {
            GenerationMode.TEXT_TO_VIDEO,
            GenerationMode.IMAGE_TO_VIDEO,
        }:
            raise ProviderError(
                "KLING_MODE_NOT_SUPPORTED",
                "Kling adapter supports only text-to-video and image-to-video",
                retryable=False,
            )
        if not 1 <= len(job.inputs.prompt) <= 3_072:
            raise ProviderError(
                "KLING_PROMPT_INVALID",
                "Kling prompt must contain at most 3072 characters",
                retryable=False,
            )
        if job.output.duration_seconds not in range(3, 16):
            raise ProviderError(
                "KLING_DURATION_INVALID",
                "Kling duration must be between 3 and 15 seconds",
                retryable=False,
            )
        if job.output.resolution not in {"720p", "1080p"}:
            raise ProviderError(
                "KLING_RESOLUTION_INVALID",
                "Kling resolution is not supported by the Relay schema",
                retryable=False,
            )
        if job.output.count != 1:
            raise ProviderError(
                "KLING_OUTPUT_COUNT_INVALID",
                "Kling produces one video per task",
                retryable=False,
            )

        images = [
            asset
            for asset in job.inputs.assets
            if asset.media_type == "image"
        ]
        if len(images) != len(job.inputs.assets):
            raise ProviderError(
                "KLING_INPUT_NOT_SUPPORTED",
                "Kling video generation accepts only image assets",
                retryable=False,
            )

        external_id = str(job.id)
        options = {"external_task_id": external_id}
        settings: dict[str, Any] = {
            "resolution": job.output.resolution,
            "duration": job.output.duration_seconds,
        }
        if job.mode == GenerationMode.TEXT_TO_VIDEO:
            if images:
                raise ProviderError(
                    "KLING_INPUT_NOT_SUPPORTED",
                    "Kling text-to-video does not accept image assets",
                    retryable=False,
                )
            settings["aspect_ratio"] = job.output.aspect_ratio
            return (
                f"/text-to-video/{provider_model}",
                {
                    "prompt": job.inputs.prompt,
                    "settings": settings,
                    "options": options,
                },
            )

        if not 1 <= len(images) <= 2:
            raise ProviderError(
                "KLING_IMAGE_COUNT_INVALID",
                "Kling image-to-video requires one or two image assets",
                retryable=False,
            )
        contents: list[dict[str, str]] = [
            {"type": "prompt", "text": job.inputs.prompt}
        ]
        for index, image in enumerate(images):
            image_url = str(image.url)
            self._validate_https_url(
                image_url,
                code="KLING_INPUT_URL_INVALID",
                message="Kling input media must use a credential-free HTTPS URL",
                retryable=False,
            )
            contents.append(
                {
                    "type": "first_frame" if index == 0 else "last_frame",
                    "url": image_url,
                }
            )
        return (
            f"/image-to-video/{provider_model}",
            {"contents": contents, "settings": settings, "options": options},
        )

    async def _recover_submission(
        self, job: GenerationJob
    ) -> ProviderSubmission | None:
        external_id = str(job.id)
        tasks = await self._query_tasks(
            query_name="external_task_ids",
            query_value=external_id,
            operation="recovery",
        )
        if not tasks:
            return None
        if len(tasks) != 1:
            raise ProviderError(
                "KLING_EXTERNAL_TASK_ID_CONFLICT",
                "Kling returned multiple tasks for one external task ID",
                retryable=False,
            )
        task = tasks[0]
        if task.external_id != external_id or not self._valid_identifier(task.id):
            raise self._invalid_query_response(operation="recovery")
        return ProviderSubmission(provider_task_id=task.id)

    async def _query_tasks(
        self,
        *,
        query_name: Literal["task_ids", "external_task_ids"],
        query_value: str,
        operation: Literal["recovery", "poll"],
    ) -> list[_KlingTask]:
        query = urlencode({query_name: query_value})
        try:
            response = await self._transport.request(
                "GET",
                f"{self._base_url}/tasks?{query}",
                headers=self._headers(),
                json=None,
            )
        except JsonTransportError:
            raise self._query_transport_error(operation=operation) from None
        except Exception:
            raise self._query_transport_error(operation=operation) from None

        status, body = self._response_parts(response, operation=operation)
        provider_code = self._provider_code(body)
        if provider_code is None:
            raise self._invalid_query_response(operation=operation)
        if not 200 <= status < 300 or provider_code != 0:
            raise self._provider_response_error(
                status, provider_code, operation=operation
            )
        data = body.get("data")
        if not isinstance(data, list):
            raise self._invalid_query_response(operation=operation)
        try:
            tasks = [_KlingTask.model_validate(item) for item in data]
        except ValidationError:
            raise self._invalid_query_response(operation=operation) from None
        if any(not self._valid_identifier(task.id) for task in tasks):
            raise self._invalid_query_response(operation=operation)
        return tasks

    def _event_for_task(self, task: _KlingTask) -> ProviderWebhookEvent:
        identity = (
            f"{self.name}:{task.id}:{task.status}:{task.update_time}"
        )
        event_id = f"kling-{uuid5(NAMESPACE_URL, identity)}"
        if task.status == "submitted":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task.id,
                status=ProviderWebhookStatus.PROCESSING,
                progress=0,
            )
        if task.status == "processing":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task.id,
                status=ProviderWebhookStatus.PROCESSING,
                progress=50,
            )
        if task.status == "failed":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task.id,
                status=ProviderWebhookStatus.FAILED,
                error=ErrorDetail(
                    code="KLING_GENERATION_FAILED",
                    message="Kling reported that generation failed",
                    retryable=False,
                ),
            )

        outputs: list[ProviderAsset] = []
        for output in task.outputs:
            if output.type != "video":
                continue
            if output.url is None:
                raise self._invalid_query_response(operation="poll")
            self._validate_https_url(
                output.url,
                code="KLING_TASK_RESPONSE_INVALID",
                message="Kling returned an invalid task response",
                retryable=True,
            )
            try:
                outputs.append(
                    ProviderAsset(
                        url=output.url,
                        media_type="video",
                        content_type="video/mp4",
                    )
                )
            except ValidationError:
                raise self._invalid_query_response(operation="poll") from None
        if not outputs:
            raise self._invalid_query_response(operation="poll")
        return ProviderWebhookEvent(
            event_id=event_id,
            provider_task_id=task.id,
            status=ProviderWebhookStatus.SUCCEEDED,
            progress=100,
            outputs=outputs,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _response_parts(
        response: JsonHttpResponse,
        *,
        operation: Literal["submit", "recovery", "poll"],
    ) -> tuple[int, Mapping[str, Any]]:
        status = getattr(response, "status", None)
        body = getattr(response, "body", None)
        if (
            isinstance(status, bool)
            or not isinstance(status, int)
            or not 100 <= status <= 599
            or not isinstance(body, Mapping)
        ):
            if operation == "submit":
                raise KlingProviderAdapter._submission_outcome_unknown()
            raise KlingProviderAdapter._invalid_query_response(
                operation=operation
            )
        return status, body

    @staticmethod
    def _provider_code(body: Mapping[str, Any]) -> int | None:
        code = body.get("code")
        if isinstance(code, bool) or not isinstance(code, int):
            return None
        return code

    @classmethod
    def _provider_response_error(
        cls,
        status: int,
        provider_code: int | None,
        *,
        operation: Literal["submit", "recovery", "poll"],
    ) -> ProviderError:
        public_code = _PUBLIC_ERROR_CODES.get(
            provider_code, f"KLING_HTTP_{status}"
        )
        if operation == "submit":
            retryable = provider_code in _RETRYABLE_SUBMIT_CODES
            message = "Kling rejected the generation request"
        else:
            retryable = (
                status in _RETRYABLE_QUERY_HTTP_STATUSES
                or status >= 500
                or provider_code in _RETRYABLE_SUBMIT_CODES
                or provider_code in _SERVER_ERROR_CODES
            )
            message = "Kling task query failed"
        account_unavailable = (
            True
            if status in {401, 403}
            or provider_code in _ACCOUNT_UNAVAILABLE_CODES
            else (None if operation == "submit" else False)
        )
        return ProviderError(
            public_code,
            message,
            retryable=retryable,
            account_unavailable=account_unavailable,
            disable_account=status in {401, 403},
            failure_scope=(
                ProviderFailureScope.CHANNEL
                if operation == "submit"
                and account_unavailable is not True
                and (
                    status >= 500
                    or provider_code in _SERVER_ERROR_CODES
                )
                else None
            ),
        )

    @staticmethod
    def _submission_outcome_unknown() -> ProviderError:
        return ProviderError(
            "KLING_SUBMISSION_OUTCOME_UNKNOWN",
            "Kling submission outcome is unknown; automatic retry is unsafe",
            retryable=False,
            submission_outcome_unknown=True,
        )

    @staticmethod
    def _query_transport_error(
        *, operation: Literal["recovery", "poll"]
    ) -> ProviderError:
        code = (
            "KLING_RECOVERY_QUERY_UNAVAILABLE"
            if operation == "recovery"
            else "KLING_QUERY_UNAVAILABLE"
        )
        return ProviderError(
            code,
            "Kling task query could not be completed",
            retryable=True,
            account_unavailable=False,
        )

    @staticmethod
    def _invalid_query_response(
        *, operation: Literal["recovery", "poll"]
    ) -> ProviderError:
        code = (
            "KLING_RECOVERY_RESPONSE_INVALID"
            if operation == "recovery"
            else "KLING_TASK_RESPONSE_INVALID"
        )
        return ProviderError(
            code,
            "Kling returned an invalid task response",
            retryable=True,
            account_unavailable=False,
        )

    @staticmethod
    def _valid_identifier(value: str) -> bool:
        return (
            bool(value)
            and value == value.strip()
            and len(value) <= 256
            and not any(
                ord(character) < 32 or ord(character) == 127
                for character in value
            )
        )

    @staticmethod
    def _validate_secret(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("api_key must be a non-empty header-safe value")
        return value

    @staticmethod
    def _validate_base_url(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "?" in value
            or "#" in value
        ):
            raise ValueError("base_url must be an HTTPS origin")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("base_url must be an HTTPS origin")
        parsed = urlsplit(value)
        try:
            parsed_port = parsed.port
        except ValueError as exc:
            raise ValueError("base_url must be an HTTPS origin") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (
                parsed_port is not None
                and not 1 <= parsed_port <= 65_535
            )
        ):
            raise ValueError("base_url must be an HTTPS origin")
        return value.rstrip("/")

    @staticmethod
    def _validate_model_aliases(value: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError("At least one Kling model alias is required")
        result: dict[str, str] = {}
        for model_alias, provider_model in value.items():
            if (
                not isinstance(model_alias, str)
                or not model_alias
                or model_alias != model_alias.strip()
                or len(model_alias) > 128
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in model_alias
                )
            ):
                raise ValueError("Kling model aliases must be non-empty strings")
            if (
                not isinstance(provider_model, str)
                or provider_model not in _OFFICIAL_MODEL_PATHS
            ):
                raise ValueError(
                    "Kling model aliases must target a documented model path"
                )
            result[model_alias] = provider_model
        return result

    @staticmethod
    def _validate_https_url(
        value: str,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ProviderError(code, message, retryable=retryable)
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ProviderError(code, message, retryable=retryable)


def create_kling_provider() -> ProviderAdapter:
    """Environment factory used by ``RELAY_PROVIDER_FACTORIES`` in staging."""

    api_key = os.getenv("KLING_API_KEY", "")
    base_url = os.getenv("KLING_BASE_URL", "")
    if not api_key or not base_url:
        raise RuntimeError("Kling API key and regional base URL are required")
    try:
        raw_aliases = os.getenv("KLING_MODEL_ALIASES_JSON")
        aliases = json.loads(raw_aliases) if raw_aliases else None
        priority = int(os.getenv("KLING_PRIORITY", "100"))
        timeout_seconds = float(os.getenv("KLING_TIMEOUT_SECONDS", "30"))
        raw_max_concurrency = os.getenv("KLING_MAX_CONCURRENCY", "")
        max_concurrency = (
            int(raw_max_concurrency) if raw_max_concurrency else None
        )
        raw_requests_per_minute = os.getenv("KLING_REQUESTS_PER_MINUTE", "")
        requests_per_minute = (
            int(raw_requests_per_minute) if raw_requests_per_minute else None
        )
        return KlingProviderAdapter(
            api_key=api_key,
            base_url=base_url,
            model_aliases=aliases,
            priority=priority,
            timeout_seconds=timeout_seconds,
            account_id=os.getenv("KLING_ACCOUNT_ID", "default"),
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("Kling provider configuration is invalid") from exc


__all__ = ["KlingProviderAdapter", "create_kling_provider"]
