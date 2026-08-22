from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from .request_ids import normalize_request_id


PUBLIC_API_VERSION = "v1"
PUBLIC_SCHEMA_VERSION = 1
UNKNOWN_CAPABILITY_REVISION = "sha256:" + ("0" * 64)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GenerationMode(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    VIDEO_TO_VIDEO = "video_to_video"


class JobStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTING = "submitting"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    PROCESSING = "processing"
    TRANSFERRING = "transferring"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReservationAction(StrEnum):
    """The only safe wallet action an upper layer may take for a job."""

    HOLD = "hold"
    SETTLE = "settle"
    RELEASE = "release"


_RESERVATION_ACTIONS = {
    JobStatus.QUEUED: ReservationAction.HOLD,
    JobStatus.SUBMITTING: ReservationAction.HOLD,
    JobStatus.RECONCILIATION_REQUIRED: ReservationAction.HOLD,
    JobStatus.PROCESSING: ReservationAction.HOLD,
    JobStatus.TRANSFERRING: ReservationAction.HOLD,
    JobStatus.SUCCEEDED: ReservationAction.SETTLE,
    JobStatus.FAILED: ReservationAction.RELEASE,
    JobStatus.CANCELLED: ReservationAction.RELEASE,
}


def reservation_action_for(status: JobStatus) -> ReservationAction:
    """Return the closed, versioned wallet action for a public job status."""

    return _RESERVATION_ACTIONS[status]


TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


class AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    media_type: Literal["image", "video", "audio"]


class GenerationInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Annotated[str, Field(min_length=1, max_length=10_000)]
    assets: Annotated[list[AssetInput], Field(max_length=15)] = Field(
        default_factory=list
    )


class OutputOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 5
    aspect_ratio: Annotated[
        str,
        Field(
            min_length=3,
            max_length=16,
            pattern=r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$",
        ),
    ] = "16:9"
    resolution: Annotated[
        str,
        Field(
            min_length=1,
            max_length=32,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$",
        ),
    ] = "720p"
    count: Annotated[int, Field(strict=True, ge=1, le=16)] = 1
    face_enabled: Annotated[bool, Field(strict=True)] = False


class CallbackRequest(BaseModel):
    """A caller-selected endpoint constrained by the tenant server policy."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_reference_id: Annotated[str | None, Field(max_length=128)] = None
    model: Annotated[str, Field(min_length=1, max_length=128)]
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    mode: GenerationMode
    inputs: GenerationInputs
    output: OutputOptions = Field(default_factory=OutputOptions)
    metadata: dict[str, Any] = Field(default_factory=dict)
    callback: CallbackRequest | None = None

    @model_validator(mode="after")
    def validate_assets_for_mode(self) -> "GenerationRequest":
        media_types = {asset.media_type for asset in self.inputs.assets}
        if self.mode == GenerationMode.IMAGE_TO_VIDEO and "image" not in media_types:
            raise ValueError("image_to_video requires at least one image asset")
        if self.mode == GenerationMode.VIDEO_TO_VIDEO and "video" not in media_types:
            raise ValueError("video_to_video requires at least one video asset")
        return self


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: Annotated[bool, Field(strict=True)] = False
    details: dict[str, Any] = Field(default_factory=dict)


class PublicAsyncErrorCode(StrEnum):
    """Closed registry of errors that may be exposed on generation jobs."""

    MODEL_CAPABILITY_UNAVAILABLE = "MODEL_CAPABILITY_UNAVAILABLE"
    CAPABILITY_REVISION_MISMATCH = "CAPABILITY_REVISION_MISMATCH"
    REQUEST_NOT_SUPPORTED_BY_MODEL = "REQUEST_NOT_SUPPORTED_BY_MODEL"
    MODE_NOT_SUPPORTED_BY_MODEL = "MODE_NOT_SUPPORTED_BY_MODEL"
    NO_PROVIDER_AVAILABLE = "NO_PROVIDER_AVAILABLE"
    PROVIDER_ACCOUNT_POOL_BUSY = "PROVIDER_ACCOUNT_POOL_BUSY"
    PROVIDER_ACCOUNT_POOL_RATE_LIMITED = "PROVIDER_ACCOUNT_POOL_RATE_LIMITED"
    PROVIDER_TASK_NOT_ASSIGNED = "PROVIDER_TASK_NOT_ASSIGNED"
    PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
    PROVIDER_CIRCUIT_OPEN = "PROVIDER_CIRCUIT_OPEN"
    PROVIDER_POLL_FAILED = "PROVIDER_POLL_FAILED"
    PROVIDER_TASK_MISMATCH = "PROVIDER_TASK_MISMATCH"
    PROVIDER_TASK_ID_INVALID = "PROVIDER_TASK_ID_INVALID"
    UPSTREAM_FAILED = "UPSTREAM_FAILED"
    CONTENT_POLICY_REJECTED = "CONTENT_POLICY_REJECTED"
    INPUT_ASSET_UNAVAILABLE = "INPUT_ASSET_UNAVAILABLE"
    GENERATION_FAILED = "GENERATION_FAILED"
    GENERATION_TASK_NOT_FOUND_UPSTREAM = "GENERATION_TASK_NOT_FOUND_UPSTREAM"
    GENERATION_CHANNEL_RESPONSE_INVALID = "GENERATION_CHANNEL_RESPONSE_INVALID"
    GENERATION_CHANNEL_UNAVAILABLE = "GENERATION_CHANNEL_UNAVAILABLE"
    ARTIFACT_TRANSFER_RETRYING = "ARTIFACT_TRANSFER_RETRYING"
    ARTIFACT_TRANSFER_FAILED = "ARTIFACT_TRANSFER_FAILED"
    SUBMISSION_RECONCILIATION_REQUIRED = "SUBMISSION_RECONCILIATION_REQUIRED"
    SUBMISSION_CONFIRMED_NOT_CREATED = "SUBMISSION_CONFIRMED_NOT_CREATED"
    PROVIDER_RETRIES_EXHAUSTED = "PROVIDER_RETRIES_EXHAUSTED"
    WORKER_ATTEMPTS_EXHAUSTED = "WORKER_ATTEMPTS_EXHAUSTED"
    PROVIDER_POLL_RECONCILIATION_REQUIRED = (
        "PROVIDER_POLL_RECONCILIATION_REQUIRED"
    )


class PublicErrorDetail(BaseModel):
    """Provider-neutral error shape persisted on a public generation job."""

    model_config = ConfigDict(extra="forbid")

    code: PublicAsyncErrorCode
    message: str
    retryable: Annotated[bool, Field(strict=True)] = False
    details: dict[str, Any] = Field(default_factory=dict)


def _coerce_public_error(value: Any) -> Any:
    if isinstance(value, ErrorDetail):
        return value.model_dump(mode="python")
    return value


PublicError = Annotated[
    PublicErrorDetail,
    BeforeValidator(_coerce_public_error),
]


class ProviderAsset(BaseModel):
    url: HttpUrl
    media_type: Literal["video", "image"]
    content_type: str | None = None


class GeneratedAsset(BaseModel):
    """Private stored artifact metadata. Access requires a short-lived signature."""

    asset_id: UUID
    object_key: Annotated[str, Field(min_length=1, max_length=1024)]
    media_type: Literal["video", "image"]
    content_type: str
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TransferSource(BaseModel):
    asset_id: UUID = Field(default_factory=uuid4)
    source_url: HttpUrl
    media_type: Literal["video", "image"]
    declared_content_type: str | None = None
    object_key: Annotated[str, Field(min_length=1, max_length=1024)]
    artifact: GeneratedAsset | None = None


class GenerationJob(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    source_client_id: Annotated[
        str | None, Field(min_length=1, max_length=128)
    ] = Field(default=None, exclude=True)
    client_reference_id: str | None = None
    model: str
    expected_capability_revision: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$", frozen=True),
    ] = UNKNOWN_CAPABILITY_REVISION
    mode: GenerationMode
    inputs: GenerationInputs
    output: OutputOptions
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Callback destinations are internal routing data and are never returned
    # from the public generation response.
    callback_url: str | None = Field(default=None, exclude=True)
    status: JobStatus = JobStatus.QUEUED
    progress: Annotated[int, Field(ge=0, le=100)] = 0
    provider: str | None = None
    provider_task_id: str | None = None
    provider_poll_failures: Annotated[int, Field(ge=0)] = Field(
        default=0, exclude=True
    )
    provider_next_poll_at: datetime | None = Field(default=None, exclude=True)
    provider_last_poll_error: str | None = Field(default=None, exclude=True)
    outputs: list[GeneratedAsset] = Field(default_factory=list)
    transfer_sources: list[TransferSource] = Field(
        default_factory=list, exclude=True
    )
    error: PublicError | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def capability_revision(self) -> str:
        return self.expected_capability_revision

    @property
    def reservation_action(self) -> ReservationAction:
        return reservation_action_for(self.status)


class GenerationResponse(BaseModel):
    """Caller-facing job state without tenant or provider routing internals."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    object: Literal["generation"] = "generation"
    id: UUID
    client_reference_id: str | None = None
    model: str
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    mode: GenerationMode
    inputs: GenerationInputs
    output: OutputOptions
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus
    reservation_action: ReservationAction
    progress: Annotated[int, Field(ge=0, le=100)]
    outputs: list[GeneratedAsset] = Field(default_factory=list)
    error: PublicError | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_capability_pin(self) -> "GenerationResponse":
        if self.capability_revision != self.expected_capability_revision:
            raise ValueError("capability revisions must match")
        if self.reservation_action != reservation_action_for(self.status):
            raise ValueError("reservation action must match job status")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("generation timestamps must include a timezone")
        if self.status == JobStatus.SUCCEEDED:
            if self.progress != 100 or self.error is not None:
                raise ValueError(
                    "succeeded jobs require 100 progress and no error"
                )
            if len(self.outputs) != self.output.count:
                raise ValueError(
                    "succeeded outputs must match the requested output count"
                )
        else:
            if self.outputs:
                raise ValueError("only succeeded jobs may expose outputs")
            if self.status == JobStatus.FAILED and self.error is None:
                raise ValueError("failed jobs require a public error")
        return self


class GenerationAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    object: Literal["generation"] = "generation"
    id: UUID
    job_id: UUID
    status: JobStatus
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    reservation_action: ReservationAction
    idempotent_replay: bool = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_capability_pin(self) -> "GenerationAccepted":
        if self.capability_revision != self.expected_capability_revision:
            raise ValueError("capability revisions must match")
        if self.id != self.job_id:
            raise ValueError("generation id and job_id must match")
        if self.reservation_action != reservation_action_for(self.status):
            raise ValueError("reservation action must match job status")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return self


class CapabilityLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_prompt_length: Annotated[int, Field(ge=1, le=10_000)]
    max_images: Annotated[int, Field(ge=0, le=15)]
    max_videos: Annotated[int, Field(ge=0, le=15)]
    max_audio: Annotated[int, Field(ge=0, le=15)]
    duration_seconds: Annotated[
        list[Annotated[int, Field(ge=1, le=3600)]], Field(min_length=1)
    ]
    aspect_ratios: Annotated[
        list[
            Annotated[
                str,
                Field(
                    min_length=3,
                    max_length=16,
                    pattern=r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$",
                ),
            ]
        ],
        Field(min_length=1),
    ]
    resolutions: Annotated[
        list[
            Annotated[
                str,
                Field(
                    min_length=1,
                    max_length=32,
                    pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$",
                ),
            ]
        ],
        Field(min_length=1),
    ]
    output_counts: Annotated[
        list[Annotated[int, Field(ge=1, le=16)]], Field(min_length=1)
    ]

    @model_validator(mode="after")
    def validate_total_assets(self) -> "CapabilityLimits":
        if self.max_images + self.max_videos + self.max_audio > 15:
            raise ValueError("combined input media limits must not exceed 15")
        for field_name in (
            "duration_seconds",
            "aspect_ratios",
            "resolutions",
            "output_counts",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(
                    f"{field_name} capability values must be unique"
                )
        return self


class ModelCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        ),
    ]
    modes: Annotated[list[GenerationMode], Field(min_length=1)]
    input_media_types: Annotated[
        list[Literal["image", "video", "audio"]], Field(max_length=3)
    ]
    supports_face: bool = False
    limits: CapabilityLimits
    available_providers: Annotated[
        list[
            Annotated[
                str,
                Field(
                    min_length=1,
                    max_length=64,
                    pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
                ),
            ]
        ],
        Field(min_length=1),
    ]

    @model_validator(mode="after")
    def validate_capability_declaration(self) -> "ModelCapability":
        if len(self.modes) != len(set(self.modes)):
            raise ValueError("generation modes must be unique")
        if len(self.input_media_types) != len(set(self.input_media_types)):
            raise ValueError("input media types must be unique")
        if len(self.available_providers) != len(
            set(self.available_providers)
        ):
            raise ValueError("available providers must be unique")

        declared_media = set(self.input_media_types)
        maxima = {
            "image": self.limits.max_images,
            "video": self.limits.max_videos,
            "audio": self.limits.max_audio,
        }
        for media_type, maximum in maxima.items():
            if (maximum > 0) != (media_type in declared_media):
                raise ValueError(
                    f"{media_type} input declaration contradicts its limit"
                )
        if (
            GenerationMode.IMAGE_TO_VIDEO in self.modes
            and self.limits.max_images < 1
        ):
            raise ValueError("image_to_video requires image input capability")
        if (
            GenerationMode.VIDEO_TO_VIDEO in self.modes
            and self.limits.max_videos < 1
        ):
            raise ValueError("video_to_video requires video input capability")
        return self


class ModelCapabilityResponse(BaseModel):
    """Public capability contract; provider candidates remain relay-private."""

    model_config = ConfigDict(from_attributes=True)

    model: str
    modes: list[GenerationMode]
    input_media_types: list[Literal["image", "video", "audio"]]
    supports_face: bool = False
    limits: CapabilityLimits


class ModeCapabilityResponse(BaseModel):
    """One provider-neutral, failover-safe generation mode contract."""

    model_config = ConfigDict(extra="forbid")

    input_media_types: list[Literal["image", "video", "audio"]]
    supports_face: bool = False
    required_resource_keys: list[str] = Field(default_factory=list)
    limits: CapabilityLimits


class GenerationCapabilityDocument(BaseModel):
    """Shape consumed by the customer platform's adaptive generation UI."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    modes: dict[GenerationMode, ModeCapabilityResponse]


class ModelResource(BaseModel):
    """OpenAI-style model resource without any provider/channel identity."""

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    id: Annotated[str, Field(min_length=1, max_length=128)]
    object: Literal["model"] = "model"
    capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capabilities: GenerationCapabilityDocument


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    object: Literal["list"] = "list"
    data: list[ModelResource]
    catalog_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]


class ProviderWebhookStatus(StrEnum):
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderWebhookEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: Annotated[str, Field(min_length=1, max_length=256)]
    provider_task_id: Annotated[str, Field(min_length=1, max_length=256)]
    status: ProviderWebhookStatus
    progress: Annotated[int | None, Field(ge=0, le=100)] = None
    outputs: list[ProviderAsset] = Field(default_factory=list)
    error: ErrorDetail | PublicErrorDetail | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "ProviderWebhookEvent":
        if self.status == ProviderWebhookStatus.SUCCEEDED and not self.outputs:
            raise ValueError("succeeded event requires at least one output")
        if self.status == ProviderWebhookStatus.FAILED and self.error is None:
            raise ValueError("failed event requires error")
        return self


class WebhookReceipt(BaseModel):
    accepted: bool = True
    duplicate: bool = False
    job_id: UUID
    status: JobStatus


class SignedDownload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    url: str
    expires_seconds: int


class WorkItem(BaseModel):
    job_id: UUID
    attempt: int = 1


class WorkDelivery(BaseModel):
    item: WorkItem
    receipt: str


class OutboxMessage(BaseModel):
    id: UUID
    topic: str
    item: WorkItem
    attempts: int = 0


class CallbackDeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class CallbackEventJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    id: UUID
    client_reference_id: str | None = None
    status: JobStatus
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    reservation_action: ReservationAction
    progress: Annotated[int, Field(ge=0, le=100)]
    outputs: list[GeneratedAsset] = Field(default_factory=list)
    error: PublicError | None = None

    @model_validator(mode="after")
    def validate_capability_pin(self) -> "CallbackEventJob":
        if self.capability_revision != self.expected_capability_revision:
            raise ValueError("capability revisions must match")
        if self.reservation_action != reservation_action_for(self.status):
            raise ValueError("reservation action must match job status")
        if self.status == JobStatus.SUCCEEDED:
            if self.progress != 100 or self.error is not None:
                raise ValueError(
                    "succeeded callback jobs require 100 progress and no error"
                )
            if not 1 <= len(self.outputs) <= 16:
                raise ValueError("succeeded callback jobs require outputs")
        else:
            if self.outputs:
                raise ValueError("only succeeded callback jobs may expose outputs")
            if self.status == JobStatus.FAILED and self.error is None:
                raise ValueError("failed callback jobs require a public error")
        return self


class CallbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"] = PUBLIC_API_VERSION
    schema_version: Literal[1] = PUBLIC_SCHEMA_VERSION
    event_id: UUID
    type: Literal["generation.status_changed"] = "generation.status_changed"
    occurred_at: datetime
    job: CallbackEventJob

    @model_validator(mode="after")
    def validate_occurred_at(self) -> "CallbackEvent":
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class CallbackDelivery(BaseModel):
    id: UUID
    tenant_id: UUID
    job_id: UUID
    callback_url: str
    request_id: Annotated[str, Field(min_length=1, max_length=80)]
    event: CallbackEvent
    status: CallbackDeliveryStatus = CallbackDeliveryStatus.PENDING
    attempts: Annotated[int, Field(ge=0)] = 0
    available_at: datetime
    locked_at: datetime | None = None
    delivered_at: datetime | None = None
    response_status: int | None = None
    last_error: str | None = None
    created_at: datetime


class SubmissionReconciliationRequest(BaseModel):
    """Operator confirmation for a submission with an unknown outcome.

    ``provider_route`` is only needed when the worker lost its durable claim
    before it could persist the selected account route.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["created", "not_created"]
    provider_task_id: Annotated[
        str | None, Field(min_length=1, max_length=256)
    ] = None
    provider_route: Annotated[
        str | None, Field(min_length=1, max_length=128)
    ] = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "SubmissionReconciliationRequest":
        if self.outcome == "created" and self.provider_task_id is None:
            raise ValueError(
                "provider_task_id is required when the provider created a task"
            )
        if self.outcome == "not_created" and (
            self.provider_task_id is not None or self.provider_route is not None
        ):
            raise ValueError(
                "provider task fields are not allowed when no task was created"
            )
        return self


class SubmissionReconciliationList(BaseModel):
    items: list[GenerationResponse]


class CallbackDeliveryView(BaseModel):
    """Tenant-safe operations view; destination and payload stay private."""

    event_id: UUID
    request_id: str
    job_id: UUID
    job_status: JobStatus
    delivery_status: CallbackDeliveryStatus
    attempts: Annotated[int, Field(ge=0)]
    available_at: datetime
    delivered_at: datetime | None = None
    response_status: int | None = None
    last_error: str | None = None
    created_at: datetime


class CallbackDeliveryList(BaseModel):
    items: list[CallbackDeliveryView]


CALLBACK_STATUSES = {
    JobStatus.RECONCILIATION_REQUIRED,
    JobStatus.PROCESSING,
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


def callback_delivery_for_job(job: GenerationJob) -> CallbackDelivery | None:
    """Build one stable callback event for a meaningful state transition."""

    if job.callback_url is None or job.status not in CALLBACK_STATUSES:
        return None
    identity = f"relay-callback:{job.id}:{job.status.value}"
    if job.status == JobStatus.PROCESSING:
        # Processing progress can move multiple times without a status change.
        # One stable event per observed progress value preserves useful updates
        # while duplicate/lower-progress provider events remain idempotent.
        identity = f"{identity}:{job.progress}"
    event_id = uuid5(NAMESPACE_URL, identity)
    trace_value = job.metadata.get("relay_request_id")
    request_id = f"relay-callback-{event_id}"
    if (
        isinstance(trace_value, str)
        and normalize_request_id(trace_value) == trace_value
    ):
        request_id = trace_value
    event = CallbackEvent(
        event_id=event_id,
        occurred_at=job.updated_at,
        job=CallbackEventJob(
            id=job.id,
            client_reference_id=job.client_reference_id,
            status=job.status,
            expected_capability_revision=job.expected_capability_revision,
            capability_revision=job.expected_capability_revision,
            reservation_action=reservation_action_for(job.status),
            progress=job.progress,
            outputs=job.outputs,
            error=job.error,
        ),
    )
    return CallbackDelivery(
        id=event_id,
        tenant_id=job.tenant_id,
        job_id=job.id,
        callback_url=job.callback_url,
        request_id=request_id,
        event=event,
        available_at=job.updated_at,
        created_at=job.updated_at,
    )


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class DependencyHealth(BaseModel):
    name: str
    state: HealthState
    details: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    state: HealthState
    dependencies: list[DependencyHealth] = Field(default_factory=list)
