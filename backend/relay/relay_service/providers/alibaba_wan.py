from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

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
    ProviderSubmission,
    validate_provider_identity,
)
from .http import (
    AioHttpJsonTransport,
    JsonHttpResponse,
    JsonHttpTransport,
    JsonTransportError,
)


_CREATE_TASK_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
_QUERY_TASK_PATH = "/api/v1/tasks/{task_id}"

_TEXT_TO_VIDEO_MODELS = (
    "wan2.7-t2v",
    "wan2.7-t2v-2026-06-12",
    "wan2.7-t2v-2026-04-25",
)
_IMAGE_TO_VIDEO_MODELS = (
    "wan2.7-i2v",
    "wan2.7-i2v-2026-04-25",
)
_ALL_MODELS = frozenset((*_TEXT_TO_VIDEO_MODELS, *_IMAGE_TO_VIDEO_MODELS))
_KNOWN_TASK_STATUSES = frozenset(
    {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}
)
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "throttling",
        "throttling.ratequota",
        "throttling.burstrate",
        "throttling.allocationquota",
        "internalerror",
        "internalerror.timeout",
        "systemerror",
        "modelservicefailed",
    }
)
_ACCOUNT_UNAVAILABLE_ERROR_CODES = frozenset(
    {
        "arrearage",
        "invalidapikey",
        "invalid_api_key",
    }
)
_PUBLIC_ERROR_CODES = {
    "invalidparameter": "ALIBABA_WAN_INVALID_PARAMETER",
    "arrearage": "ALIBABA_WAN_ARREARAGE",
    "datainspectionfailed": "ALIBABA_WAN_DATA_INSPECTION_FAILED",
    "data_inspection_failed": "ALIBABA_WAN_DATA_INSPECTION_FAILED",
    "invalidapikey": "ALIBABA_WAN_INVALID_API_KEY",
    "invalid_api_key": "ALIBABA_WAN_INVALID_API_KEY",
    "workspacenotfound": "ALIBABA_WAN_WORKSPACE_NOT_FOUND",
    "notfound": "ALIBABA_WAN_NOT_FOUND",
    "throttling": "ALIBABA_WAN_THROTTLED",
    "throttling.ratequota": "ALIBABA_WAN_RATE_QUOTA_EXCEEDED",
    "throttling.burstrate": "ALIBABA_WAN_BURST_RATE_EXCEEDED",
    "throttling.allocationquota": "ALIBABA_WAN_ALLOCATION_QUOTA_EXCEEDED",
    "internalerror": "ALIBABA_WAN_INTERNAL_ERROR",
    "internalerror.timeout": "ALIBABA_WAN_INTERNAL_TIMEOUT",
    "systemerror": "ALIBABA_WAN_SYSTEM_ERROR",
    "modelservicefailed": "ALIBABA_WAN_MODEL_SERVICE_FAILED",
}


class AlibabaWanProviderAdapter(ProviderAdapter):
    """Wan 2.7 HTTP adapter for Alibaba Cloud Model Studio.

    Model Studio does not document a request-level idempotency token for this
    API. Ambiguous submission failures are therefore terminal for automatic
    routing: retrying could create and charge for a second upstream task. The
    adapter deliberately remains non-production-ready until a durable
    submission-reconciliation policy and deployment credentials are in place.
    """

    name = "alibaba-wan"
    channel_type = ProviderChannelType.OFFICIAL
    production_ready = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com",
        transport: JsonHttpTransport | None = None,
        priority: int = 100,
        timeout_seconds: float = 30,
        models: tuple[str, ...] | None = None,
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
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(base_url, str):
            raise ValueError("base_url must be an HTTPS origin")
        normalized_base_url = base_url.rstrip("/")
        parsed = urlsplit(normalized_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be an HTTPS origin")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        configured_models = tuple(models or _ALL_MODELS)
        if not configured_models or any(
            model not in _ALL_MODELS for model in configured_models
        ):
            raise ValueError("models must contain supported Wan 2.7 model IDs")

        self._api_key = api_key
        self._base_url = normalized_base_url
        self._transport = transport or AioHttpJsonTransport(
            timeout_seconds=timeout_seconds
        )
        self._models = frozenset(configured_models)
        self.priority = priority
        self.account_id = account_id
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute

    async def capabilities(self) -> list[ModelCapability]:
        duration_seconds = list(range(2, 16))
        # Keep this adapter's advertised set aligned with the currently
        # verified provider request mapping; the Relay contract itself is
        # intentionally wider.
        relay_aspect_ratios = ["16:9", "9:16", "1:1"]
        capabilities: list[ModelCapability] = []
        for model in _TEXT_TO_VIDEO_MODELS:
            if model not in self._models:
                continue
            capabilities.append(
                ModelCapability(
                    model=model,
                    modes=[GenerationMode.TEXT_TO_VIDEO],
                    input_media_types=["audio"],
                    limits=CapabilityLimits(
                        max_prompt_length=5_000,
                        max_images=0,
                        max_videos=0,
                        max_audio=1,
                        duration_seconds=duration_seconds,
                        aspect_ratios=relay_aspect_ratios,
                        resolutions=["720p", "1080p"],
                        output_counts=[1],
                    ),
                    available_providers=[self.name],
                )
            )
        for model in _IMAGE_TO_VIDEO_MODELS:
            if model not in self._models:
                continue
            capabilities.append(
                ModelCapability(
                    model=model,
                    modes=[GenerationMode.IMAGE_TO_VIDEO],
                    input_media_types=["image", "audio"],
                    limits=CapabilityLimits(
                        max_prompt_length=5_000,
                        max_images=2,
                        max_videos=0,
                        max_audio=1,
                        duration_seconds=duration_seconds,
                        # Wan I2V follows the input image aspect ratio. The
                        # generic relay field is accepted but not sent upstream.
                        aspect_ratios=relay_aspect_ratios,
                        resolutions=["720p", "1080p"],
                        output_counts=[1],
                    ),
                    available_providers=[self.name],
                )
            )
        return capabilities

    async def healthcheck(self) -> bool:
        # Model Studio documents no non-billable provider health endpoint.
        # Constructor validation establishes only local configuration health;
        # request-time authentication and availability errors remain explicit.
        return True

    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        payload = self._submission_payload(job)
        try:
            response = await self._transport.request(
                "POST",
                f"{self._base_url}{_CREATE_TASK_PATH}",
                headers=self._submission_headers(),
                json=payload,
            )
        except JsonTransportError:
            raise self._ambiguous_submission_error() from None
        except Exception:
            # Injected transports are allowed and may expose their own timeout
            # or connection exception classes. Never echo those exceptions: a
            # URL or diagnostic could contain credentials.
            raise self._ambiguous_submission_error() from None

        status, body = self._response_parts(response, operation="submit")
        if not 200 <= status < 300 or "code" in body:
            raise self._provider_response_error(status, body, operation="submit")

        output = body.get("output")
        if not isinstance(output, Mapping):
            raise self._ambiguous_submission_error()
        task_id = self._required_string(output, "task_id")
        task_status = self._required_string(output, "task_status")
        if task_id is None or task_status not in {"PENDING", "RUNNING"}:
            raise self._ambiguous_submission_error()
        if len(task_id) > 256:
            raise self._ambiguous_submission_error()
        self._validate_optional_request_id(body, operation="submit")
        return ProviderSubmission(provider_task_id=task_id)

    async def poll(self, job: GenerationJob) -> ProviderWebhookEvent | None:
        task_id = job.provider_task_id
        if (
            not isinstance(task_id, str)
            or not task_id.strip()
            or len(task_id) > 256
        ):
            raise ProviderError(
                "ALIBABA_WAN_TASK_ID_MISSING",
                "Alibaba Wan task ID is missing or invalid",
                retryable=False,
                fail_job=True,
            )

        encoded_task_id = quote(task_id, safe="")
        query_path = _QUERY_TASK_PATH.format(task_id=encoded_task_id)
        try:
            response = await self._transport.request(
                "GET",
                f"{self._base_url}{query_path}",
                headers=self._query_headers(),
                json=None,
            )
        except JsonTransportError:
            raise self._query_transport_error() from None
        except Exception:
            raise self._query_transport_error() from None

        status, body = self._response_parts(response, operation="query")
        if not 200 <= status < 300 or "code" in body:
            raise self._provider_response_error(status, body, operation="query")
        self._validate_optional_request_id(body, operation="query")
        return self._poll_event(task_id, body)

    async def close(self) -> None:
        await self._transport.close()

    def _submission_payload(self, job: GenerationJob) -> dict[str, Any]:
        if job.model not in self._models:
            raise ProviderError(
                "ALIBABA_WAN_MODEL_UNSUPPORTED",
                "Alibaba Wan does not support the requested model",
                retryable=False,
            )
        is_t2v = job.model in _TEXT_TO_VIDEO_MODELS
        expected_mode = (
            GenerationMode.TEXT_TO_VIDEO
            if is_t2v
            else GenerationMode.IMAGE_TO_VIDEO
        )
        if job.mode != expected_mode:
            raise ProviderError(
                "ALIBABA_WAN_MODE_UNSUPPORTED",
                "Alibaba Wan model and generation mode do not match",
                retryable=False,
            )
        if not 1 <= len(job.inputs.prompt) <= 5_000:
            raise ProviderError(
                "ALIBABA_WAN_INVALID_REQUEST",
                "Alibaba Wan prompt is outside the supported length",
                retryable=False,
            )
        if job.output.duration_seconds not in range(2, 16):
            raise ProviderError(
                "ALIBABA_WAN_INVALID_REQUEST",
                "Alibaba Wan duration must be between 2 and 15 seconds",
                retryable=False,
            )
        if job.output.resolution not in {"720p", "1080p"}:
            raise ProviderError(
                "ALIBABA_WAN_INVALID_REQUEST",
                "Alibaba Wan resolution is unsupported",
                retryable=False,
            )
        if job.output.count != 1:
            raise ProviderError(
                "ALIBABA_WAN_INVALID_REQUEST",
                "Alibaba Wan produces one video per task",
                retryable=False,
            )

        images = [asset for asset in job.inputs.assets if asset.media_type == "image"]
        videos = [asset for asset in job.inputs.assets if asset.media_type == "video"]
        audio = [asset for asset in job.inputs.assets if asset.media_type == "audio"]
        if is_t2v:
            if images or videos or len(audio) > 1:
                raise ProviderError(
                    "ALIBABA_WAN_INVALID_INPUT_MEDIA",
                    "Wan text-to-video accepts at most one audio input",
                    retryable=False,
                )
        elif not 1 <= len(images) <= 2 or videos or len(audio) > 1:
            raise ProviderError(
                "ALIBABA_WAN_INVALID_INPUT_MEDIA",
                "Wan image-to-video requires one or two images and at most one audio input",
                retryable=False,
            )

        input_body: dict[str, Any] = {"prompt": job.inputs.prompt}

        parameters: dict[str, Any] = {
            "resolution": job.output.resolution.upper(),
            "duration": job.output.duration_seconds,
            # Provider-only controls stay fixed adapter policy until the
            # unified request contract explicitly models them. Caller metadata
            # must never bind a request to one failover route.
            "prompt_extend": True,
            "watermark": False,
        }

        if is_t2v:
            if job.output.aspect_ratio not in {"16:9", "9:16", "1:1"}:
                raise ProviderError(
                    "ALIBABA_WAN_INVALID_REQUEST",
                    "Alibaba Wan aspect ratio is unsupported by the relay",
                    retryable=False,
                )
            parameters["ratio"] = job.output.aspect_ratio
            if audio:
                self._validate_media_url(str(audio[0].url))
                input_body["audio_url"] = str(audio[0].url)
        else:
            for asset in (*images, *audio):
                self._validate_media_url(str(asset.url))
            media = [
                {"type": "first_frame", "url": str(images[0].url)}
            ]
            if len(images) == 2:
                media.append(
                    {"type": "last_frame", "url": str(images[1].url)}
                )
            if audio:
                media.append(
                    {"type": "driving_audio", "url": str(audio[0].url)}
                )
            input_body["media"] = media

        return {"model": job.model, "input": input_body, "parameters": parameters}

    def _poll_event(
        self, task_id: str, body: Mapping[str, Any]
    ) -> ProviderWebhookEvent:
        output = body.get("output")
        if not isinstance(output, Mapping):
            raise self._invalid_query_response()
        returned_task_id = self._required_string(output, "task_id")
        status = self._required_string(output, "task_status")
        if returned_task_id != task_id or status not in _KNOWN_TASK_STATUSES:
            raise self._invalid_query_response()

        event_id = self._event_id(task_id, status)
        if status == "PENDING":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task_id,
                status=ProviderWebhookStatus.PROCESSING,
                progress=0,
            )
        if status == "RUNNING":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task_id,
                status=ProviderWebhookStatus.PROCESSING,
                progress=50,
            )
        if status == "SUCCEEDED":
            video_url = self._required_string(output, "video_url")
            if video_url is None:
                raise self._invalid_query_response()
            self._validate_result_url(video_url)
            try:
                asset = ProviderAsset(
                    url=video_url,
                    media_type="video",
                    content_type="video/mp4",
                )
            except ValidationError:
                raise self._invalid_query_response() from None
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task_id,
                status=ProviderWebhookStatus.SUCCEEDED,
                progress=100,
                outputs=[asset],
            )
        if status == "CANCELED":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task_id,
                status=ProviderWebhookStatus.CANCELLED,
            )
        if status == "UNKNOWN":
            return ProviderWebhookEvent(
                event_id=event_id,
                provider_task_id=task_id,
                status=ProviderWebhookStatus.FAILED,
                error=ErrorDetail(
                    code="ALIBABA_WAN_TASK_UNKNOWN",
                    message="Alibaba Wan task is unknown or has expired",
                    retryable=False,
                ),
            )

        provider_code = self._required_string(output, "code")
        provider_message = self._required_string(output, "message")
        if provider_code is None or provider_message is None:
            raise self._invalid_query_response()
        public_code, retryable = self._mapped_error(provider_code, status_code=200)
        return ProviderWebhookEvent(
            event_id=event_id,
            provider_task_id=task_id,
            status=ProviderWebhookStatus.FAILED,
            error=ErrorDetail(
                code=public_code,
                message="Alibaba Wan generation failed",
                retryable=retryable,
            ),
        )

    def _submission_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

    def _query_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _required_string(
        value: Mapping[str, Any], field: str
    ) -> str | None:
        candidate = value.get(field)
        if not isinstance(candidate, str) or not candidate.strip():
            return None
        return candidate

    @staticmethod
    def _validate_optional_request_id(
        body: Mapping[str, Any], *, operation: str
    ) -> None:
        request_id = body.get("request_id")
        if request_id is None:
            return
        if not isinstance(request_id, str) or not request_id.strip():
            if operation == "submit":
                raise AlibabaWanProviderAdapter._ambiguous_submission_error()
            raise AlibabaWanProviderAdapter._invalid_query_response()

    @staticmethod
    def _response_parts(
        response: JsonHttpResponse, *, operation: str
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
                raise AlibabaWanProviderAdapter._ambiguous_submission_error()
            raise AlibabaWanProviderAdapter._invalid_query_response()
        return status, body

    @staticmethod
    def _event_id(task_id: str, status: str) -> str:
        identity = f"alibaba-wan:{task_id}:{status}"
        return f"alibaba-wan-{uuid5(NAMESPACE_URL, identity)}"

    @staticmethod
    def _mapped_error(provider_code: Any, *, status_code: int) -> tuple[str, bool]:
        normalized = provider_code.casefold() if isinstance(provider_code, str) else ""
        public_code = _PUBLIC_ERROR_CODES.get(
            normalized, "ALIBABA_WAN_REQUEST_FAILED"
        )
        retryable = (
            normalized in _RETRYABLE_ERROR_CODES
            or status_code == 429
            or status_code >= 500
        )
        return public_code, retryable

    @classmethod
    def _provider_response_error(
        cls,
        status: int,
        body: Mapping[str, Any],
        *,
        operation: str,
    ) -> ProviderError:
        # A gateway/server response to the creation POST does not prove that
        # the upstream task was never created.  Retrying or switching routes
        # here could create and charge for the same generation twice.
        if operation == "submit" and status >= 500:
            return cls._ambiguous_submission_error()
        provider_code = body.get("code")
        public_code, retryable = cls._mapped_error(
            provider_code, status_code=status
        )
        message = (
            "Alibaba Wan rejected the generation request"
            if operation == "submit"
            else "Alibaba Wan task query failed"
        )
        normalized = (
            provider_code.casefold()
            if isinstance(provider_code, str)
            else ""
        )
        account_unavailable = (
            True
            if status in {401, 403}
            or normalized in _ACCOUNT_UNAVAILABLE_ERROR_CODES
            else (None if operation == "submit" else False)
        )
        return ProviderError(
            public_code,
            message,
            retryable=retryable,
            account_unavailable=account_unavailable,
            disable_account=status in {401, 403},
        )

    @staticmethod
    def _ambiguous_submission_error() -> ProviderError:
        return ProviderError(
            "ALIBABA_WAN_SUBMISSION_OUTCOME_UNKNOWN",
            "Alibaba Wan submission outcome is unknown; automatic retry is unsafe",
            retryable=False,
            submission_outcome_unknown=True,
        )

    @staticmethod
    def _query_transport_error() -> ProviderError:
        return ProviderError(
            "ALIBABA_WAN_QUERY_UNAVAILABLE",
            "Alibaba Wan task query could not be completed",
            retryable=True,
            account_unavailable=False,
        )

    @staticmethod
    def _invalid_query_response() -> ProviderError:
        return ProviderError(
            "ALIBABA_WAN_RESPONSE_INVALID",
            "Alibaba Wan returned an invalid task response",
            retryable=True,
            account_unavailable=False,
        )

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
                "ALIBABA_WAN_INPUT_URL_INVALID",
                "Alibaba Wan input media must use a credential-free HTTPS URL",
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
            raise AlibabaWanProviderAdapter._invalid_query_response()


def create_alibaba_wan_provider() -> ProviderAdapter:
    """Environment factory used by ``RELAY_PROVIDER_FACTORIES`` in staging."""

    api_key = os.getenv("ALIBABA_WAN_API_KEY", "")
    base_url = os.getenv("ALIBABA_WAN_BASE_URL", "")
    raw_models = os.getenv(
        "ALIBABA_WAN_MODELS", "wan2.7-t2v,wan2.7-i2v"
    )
    models = tuple(
        model.strip() for model in raw_models.split(",") if model.strip()
    )
    if not api_key or not base_url:
        raise RuntimeError(
            "Alibaba Wan provider credentials and regional base URL are required"
        )
    try:
        priority = int(os.getenv("ALIBABA_WAN_PRIORITY", "100"))
        timeout_seconds = float(
            os.getenv("ALIBABA_WAN_TIMEOUT_SECONDS", "30")
        )
        raw_max_concurrency = os.getenv(
            "ALIBABA_WAN_MAX_CONCURRENCY", ""
        )
        max_concurrency = (
            int(raw_max_concurrency) if raw_max_concurrency else None
        )
        raw_requests_per_minute = os.getenv(
            "ALIBABA_WAN_REQUESTS_PER_MINUTE", ""
        )
        requests_per_minute = (
            int(raw_requests_per_minute) if raw_requests_per_minute else None
        )
        return AlibabaWanProviderAdapter(
            api_key=api_key,
            base_url=base_url,
            priority=priority,
            timeout_seconds=timeout_seconds,
            models=models,
            account_id=os.getenv("ALIBABA_WAN_ACCOUNT_ID", "default"),
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Alibaba Wan provider configuration is invalid") from exc


__all__ = ["AlibabaWanProviderAdapter", "create_alibaba_wan_provider"]
