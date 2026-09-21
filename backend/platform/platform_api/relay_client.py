from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_serializer,
    model_validator,
)

from .execution_contract import ExecutionContract
from .request_ids import normalize_request_id, stable_request_id


RELAY_CHANNEL_TEST_TIMEOUT_SECONDS = 11 * 60


def relay_canonical_json(value: Any) -> bytes:
    """Return the frozen Relay revision representation.

    Both the Python behavior oracle and the new-api extension define Relay
    revisions as UTF-8 JSON with recursively sorted object keys and no
    insignificant whitespace.  Keep the calculation on the Platform side as
    an independent trust check instead of accepting a revision asserted by the
    Relay response itself.
    """

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def relay_sha256_revision(value: Any) -> str:
    return "sha256:" + hashlib.sha256(relay_canonical_json(value)).hexdigest()


def validate_relay_utf8_byte_limit(
    value: str,
    *,
    field_name: str,
    max_bytes: int,
) -> str:
    """Keep Platform string limits identical to Go's UTF-8 byte limits."""

    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"{field_name} exceeds the {max_bytes}-byte UTF-8 limit"
        )
    return value


_RELAY_RECONCILIATION_SECRET_MARKERS = (
    "http://",
    "https://",
    "://",
    "bearer ",
    "authorization:",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "credential",
    "token=",
    "token:",
    "key=",
    "key:",
    "password=",
    "password:",
    "secret=",
    "secret:",
    "signature=",
    "signature:",
    "sig=",
    "sig:",
    "x-amz-",
    "x-obs-",
    "x-goog-",
    "x-cos-",
    "x-oss-",
)
_RELAY_RECONCILIATION_SECRET_SHAPE = re.compile(
    r"(?:\bauthorization\s*[:=]|\bbearer\s+|"
    r"\b(?:token|key|password|secret|signature|sig)\s*[:=])",
    re.IGNORECASE,
)


def _validate_relay_channel_reconciliation_text(
    value: str,
    *,
    field_name: str,
    min_length: int,
    max_length: int,
) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not (min_length <= len(value) <= max_length)
    ):
        raise ValueError(f"channel reconciliation {field_name} is invalid")
    validate_relay_utf8_byte_limit(
        value,
        field_name=field_name,
        max_bytes=max_length,
    )
    if any(
        unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    ):
        raise ValueError(
            f"channel reconciliation {field_name} contains control characters"
        )
    normalized = value.casefold()
    if any(marker in normalized for marker in _RELAY_RECONCILIATION_SECRET_MARKERS):
        raise ValueError(
            f"channel reconciliation {field_name} contains sensitive material"
        )
    if _RELAY_RECONCILIATION_SECRET_SHAPE.search(value):
        raise ValueError(
            f"channel reconciliation {field_name} contains sensitive material"
        )
    return value


def validate_relay_channel_reconciliation_actor(value: str) -> str:
    """Validate the server-derived actor using Relay's persisted-text boundary."""

    return _validate_relay_channel_reconciliation_text(
        value,
        field_name="actor",
        min_length=1,
        max_length=128,
    )


def validate_relay_channel_reconciliation_reason(value: str) -> str:
    """Reject evidence text that could persist a provider secret or signed URL."""

    return _validate_relay_channel_reconciliation_text(
        value,
        field_name="reason",
        min_length=3,
        max_length=240,
    )


RelayAssetRole = Literal[
    "reference_image",
    "first_frame",
    "last_frame",
    "reference_video",
    "director_previs",
    "driving_audio",
]
RelayTemporalControl = Literal[
    "first_frame",
    "last_frame",
    "reference_video",
    "director_previs",
]
RelayStructuredInput = Literal[
    "director_shot_v1", "director_shot_v2", "director_motion_v1"
]
_RELAY_ASSET_ROLE_MEDIA_TYPES: dict[str, str] = {
    "reference_image": "image",
    "first_frame": "image",
    "last_frame": "image",
    "reference_video": "video",
    "director_previs": "video",
    "driving_audio": "audio",
}


class RelayAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    media_type: Literal["image", "video", "audio"]
    role: RelayAssetRole | None = None

    @model_validator(mode="after")
    def validate_role_media_type(self) -> "RelayAsset":
        if (
            self.role is not None
            and _RELAY_ASSET_ROLE_MEDIA_TYPES[self.role] != self.media_type
        ):
            raise ValueError(
                f"Relay asset role {self.role} requires "
                f"{_RELAY_ASSET_ROLE_MEDIA_TYPES[self.role]} media"
            )
        return self


class RelayDirectorShotInput(BaseModel):
    """Sealed, URL-free 3D constraints sent through the production Relay."""

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]
    manifest_sha256: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    sealed_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]

    @model_validator(mode="after")
    def validate_sealed_manifest(self) -> "RelayDirectorShotInput":
        # Import locally so the low-level client does not create a module
        # import cycle with the Platform service layer.
        from .services.director_shot_packages import DirectorShotManifest
        from .director_canonical_json import director_canonical_json

        DirectorShotManifest.model_validate(self.manifest)
        manifest_revision = "sha256:" + hashlib.sha256(
            director_canonical_json(self.manifest)
        ).hexdigest()
        if manifest_revision != self.manifest_sha256:
            raise ValueError("director_shot manifest_sha256 does not match manifest")
        return self


class RelayDirectorMotionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["first_frame", "last_frame"]
    asset_id: str = Field(min_length=1, max_length=64)
    sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class RelayDirectorMotionInput(BaseModel):
    """Canonical motion document; local WebM previs is deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any]
    motion_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    temporal_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    frames: list[RelayDirectorMotionFrame] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_motion_document(self) -> "RelayDirectorMotionInput":
        from .services.director_shot_packages import DirectorMotionDocument
        from .director_canonical_json import director_canonical_json

        motion = DirectorMotionDocument.model_validate(self.document)
        revision = "sha256:" + hashlib.sha256(
            director_canonical_json(
                motion.model_dump(mode="json", by_alias=True)
            )
        ).hexdigest()
        if revision != self.motion_sha256:
            raise ValueError("director_motion motion_sha256 does not match document")
        if [frame.role for frame in self.frames] != [
            "first_frame", "last_frame"
        ]:
            raise ValueError("director_motion requires ordered endpoint frames")
        return self


class RelayInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=10_000)
    assets: list[RelayAsset] = Field(default_factory=list, max_length=15)
    director_shot: RelayDirectorShotInput | None = None
    director_motion: RelayDirectorMotionInput | None = None


class RelayOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int = Field(default=5, strict=True, ge=1, le=3600)
    aspect_ratio: Annotated[
        str,
        Field(
            min_length=3, max_length=16, pattern=r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$"
        ),
    ] = "16:9"
    resolution: Annotated[
        str,
        Field(
            min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"
        ),
    ] = "720p"
    count: int = Field(default=1, strict=True, ge=1, le=16)
    face_enabled: Annotated[bool, Field(strict=True)] = False


class RelayCallbackTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


class RelayGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_reference_id: str = Field(max_length=128)
    model: str = Field(min_length=1, max_length=128)
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    mode: Literal[
        "text_to_image",
        "image_to_image",
        "text_to_video",
        "image_to_video",
        "video_to_video",
    ]
    inputs: RelayInputs
    output: RelayOutput
    metadata: dict[str, Any] = Field(default_factory=dict)
    callback: RelayCallbackTarget | None = None
    execution_contract: ExecutionContract | None = None

    @model_validator(mode="after")
    def validate_execution_rectangle(self) -> "RelayGenerationRequest":
        if self.execution_contract is not None and any(
            route.mode != self.mode or route.resolution != self.output.resolution
            for route in self.execution_contract.routes
        ):
            raise ValueError("execution contract does not match requested mode/resolution")
        return self

    @model_serializer(mode="wrap")
    def omit_legacy_execution_contract(self, handler):
        payload = handler(self)
        # Adding a null field would change the first-materialization digest of
        # old unknown submissions and break exact idempotent recovery.
        if self.execution_contract is None:
            payload.pop("execution_contract", None)
        return payload


RelayJobStatus = Literal[
    "queued",
    "submitting",
    "reconciliation_required",
    "processing",
    "transferring",
    "succeeded",
    "failed",
    "cancelled",
]
RelayReservationAction = Literal["hold", "settle", "release"]
RelayGenerationMode = Literal[
    "text_to_image",
    "image_to_image",
    "text_to_video",
    "image_to_video",
    "video_to_video",
]
RelayAsyncErrorCode = Literal[
    "MODEL_CAPABILITY_UNAVAILABLE",
    "CAPABILITY_REVISION_MISMATCH",
    "REQUEST_NOT_SUPPORTED_BY_MODEL",
    "MODE_NOT_SUPPORTED_BY_MODEL",
    "NO_PROVIDER_AVAILABLE",
    "PROVIDER_ACCOUNT_POOL_BUSY",
    "PROVIDER_ACCOUNT_POOL_RATE_LIMITED",
    "PROVIDER_TASK_NOT_ASSIGNED",
    "PROVIDER_NOT_FOUND",
    "PROVIDER_CIRCUIT_OPEN",
    "PROVIDER_POLL_FAILED",
    "PROVIDER_TASK_MISMATCH",
    "PROVIDER_TASK_ID_INVALID",
    "UPSTREAM_FAILED",
    "CONTENT_POLICY_REJECTED",
    "INPUT_ASSET_UNAVAILABLE",
    "GENERATION_FAILED",
    "GENERATION_TASK_NOT_FOUND_UPSTREAM",
    "GENERATION_CHANNEL_RESPONSE_INVALID",
    "GENERATION_CHANNEL_UNAVAILABLE",
    "ARTIFACT_TRANSFER_RETRYING",
    "ARTIFACT_TRANSFER_FAILED",
    "SUBMISSION_RECONCILIATION_REQUIRED",
    "SUBMISSION_CONFIRMED_NOT_CREATED",
    "PROVIDER_RETRIES_EXHAUSTED",
    "WORKER_ATTEMPTS_EXHAUSTED",
    "PROVIDER_POLL_RECONCILIATION_REQUIRED",
]


_RESERVATION_ACTION_BY_STATUS: dict[str, str] = {
    "queued": "hold",
    "submitting": "hold",
    "reconciliation_required": "hold",
    "processing": "hold",
    "transferring": "hold",
    "succeeded": "settle",
    "failed": "release",
    "cancelled": "release",
}


def expected_reservation_action(status: str) -> str:
    try:
        return _RESERVATION_ACTION_BY_STATUS[status]
    except KeyError as exc:
        raise ValueError("Relay job status is not supported") from exc


class RelayErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: RelayAsyncErrorCode
    message: str = Field(min_length=1, max_length=2000)
    retryable: Annotated[bool, Field(strict=True)] = False
    details: dict[str, Any] = Field(default_factory=dict)


class RelayErrorEnvelopeDetail(RelayErrorDetail):
    code: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Z][A-Z0-9_]{0,159}$",
    )
    request_id: str = Field(min_length=1, max_length=128)


class RelayErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    error: RelayErrorEnvelopeDetail


class _RelayVersionedResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]

    @staticmethod
    def _canonical_job_id(value: str) -> str:
        try:
            parsed = UUID(value)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Relay job id must be a UUID") from exc
        canonical = str(parsed)
        if value != canonical:
            raise ValueError("Relay job id must use canonical UUID form")
        return value


class RelayAccepted(_RelayVersionedResource):
    execution_contract_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    object: Literal["generation"]
    id: str
    job_id: str
    status: RelayJobStatus
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capability_revision: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    reservation_action: RelayReservationAction
    idempotent_replay: Annotated[bool, Field(strict=True)]
    created_at: datetime

    @field_validator("id", "job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        return cls._canonical_job_id(value)

    @model_validator(mode="after")
    def validate_identity_and_action(self) -> "RelayAccepted":
        if self.id != self.job_id:
            raise ValueError("Relay accepted id and job_id must match")
        if self.expected_capability_revision != self.capability_revision:
            raise ValueError("Relay capability revisions must match")
        if self.reservation_action != expected_reservation_action(self.status):
            raise ValueError("Relay reservation_action does not match status")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Relay created_at must include a UTC offset")
        return self


class RelayArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=128)
    object_key: str = Field(min_length=1, max_length=1024)
    media_type: Literal["image", "video"]
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(strict=True, ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        try:
            canonical = str(UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Relay artifact id must be a UUID") from exc
        if value != canonical:
            raise ValueError("Relay artifact id must use canonical UUID form")
        return value

    def safe_metadata(self) -> dict[str, Any]:
        return self.model_dump(exclude={"object_key"}, mode="json")


class RelayJobSnapshot(_RelayVersionedResource):
    execution_contract_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    object: Literal["generation"]
    id: str
    client_reference_id: str | None
    model: str = Field(min_length=1, max_length=128)
    expected_capability_revision: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capability_revision: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    mode: RelayGenerationMode
    inputs: RelayInputs
    output: RelayOutput
    metadata: dict[str, Any]
    status: RelayJobStatus
    reservation_action: RelayReservationAction
    progress: int = Field(strict=True, ge=0, le=100)
    outputs: list[RelayArtifact] = Field(default_factory=list, max_length=16)
    error: RelayErrorDetail | None
    created_at: datetime
    updated_at: datetime

    @field_validator("id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        return cls._canonical_job_id(value)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "RelayJobSnapshot":
        if self.expected_capability_revision != self.capability_revision:
            raise ValueError("Relay capability revisions must match")
        if self.reservation_action != expected_reservation_action(self.status):
            raise ValueError("Relay reservation_action does not match status")
        if self.status == "succeeded":
            if self.progress != 100 or self.error is not None:
                raise ValueError(
                    "A succeeded Relay job requires progress=100 and error=null"
                )
            if not self.outputs or len(self.outputs) != self.output.count:
                raise ValueError(
                    "A succeeded Relay job must contain exactly output.count artifacts"
                )
        else:
            if self.outputs:
                raise ValueError("Only a succeeded Relay job may contain outputs")
            if self.status == "failed" and self.error is None:
                raise ValueError("A failed Relay job must contain an error")
        for timestamp in (self.created_at, self.updated_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Relay timestamps must include a UTC offset")
        if self.updated_at < self.created_at:
            raise ValueError("Relay updated_at cannot precede created_at")
        return self


class RelayArtifactStorageBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["huawei_obs"]
    endpoint_host: str = Field(min_length=4, max_length=253)
    bucket: str = Field(
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$",
    )
    object_key: str = Field(min_length=1, max_length=1024)
    issued_at: datetime
    expires_at: datetime
    url_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("endpoint_host")
    @classmethod
    def endpoint_host_is_canonical(cls, value: str) -> str:
        if (
            value != value.casefold().rstrip(".")
            or "://" in value
            or ":" in value
            or ".." in value
            or not re.fullmatch(r"[a-z0-9.-]+", value)
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                for label in value.split(".")
            )
        ):
            raise ValueError("Relay artifact endpoint host must be canonical")
        return value

    @field_validator("bucket")
    @classmethod
    def bucket_is_canonical(cls, value: str) -> str:
        if ".." in value or value.startswith("xn--"):
            raise ValueError("Relay artifact bucket must be canonical")
        return value

    @field_validator("object_key")
    @classmethod
    def object_key_is_canonical(cls, value: str) -> str:
        if (
            value != value.strip()
            or value.startswith("/")
            or value.endswith("/")
            or "\\" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Relay artifact object key must be canonical")
        return value

    @model_validator(mode="after")
    def validity_window_is_canonical(self) -> "RelayArtifactStorageBinding":
        for value in (self.issued_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(
                    "Relay artifact binding timestamps require a UTC offset"
                )
        lifetime = self.expires_at - self.issued_at
        if lifetime < timedelta(seconds=1) or lifetime > timedelta(hours=1):
            raise ValueError("Relay artifact binding validity window is invalid")
        if lifetime != timedelta(seconds=int(lifetime.total_seconds())):
            raise ValueError("Relay artifact binding lifetime must use whole seconds")
        return self


class RelaySignedDownload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    url: HttpUrl
    expires_seconds: int = Field(strict=True, ge=1, le=3600)
    storage_binding: RelayArtifactStorageBinding | None = None

    @model_validator(mode="after")
    def storage_binding_matches_url_and_ttl(self) -> "RelaySignedDownload":
        if self.storage_binding is None:
            return self
        raw_url = str(self.url)
        if hashlib.sha256(raw_url.encode("utf-8")).hexdigest() != (
            self.storage_binding.url_sha256
        ):
            raise ValueError("Relay artifact signed URL digest does not match")
        if self.storage_binding.expires_at - self.storage_binding.issued_at != (
            timedelta(seconds=self.expires_seconds)
        ):
            raise ValueError("Relay artifact signed URL TTL does not match")
        return self


class RelayCapabilityLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    max_prompt_length: int = Field(ge=1, le=10_000)
    max_images: int = Field(ge=0, le=15)
    max_videos: int = Field(ge=0, le=15)
    max_audio: int = Field(ge=0, le=15)
    duration_seconds: list[Annotated[int, Field(ge=1, le=3600)]] = Field(min_length=1)
    aspect_ratios: list[
        Annotated[
            str,
            Field(
                min_length=3,
                max_length=16,
                pattern=r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$",
            ),
        ]
    ] = Field(min_length=1)
    resolutions: list[
        Annotated[
            str,
            Field(
                min_length=1,
                max_length=32,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$",
            ),
        ]
    ] = Field(min_length=1)
    output_counts: list[Annotated[int, Field(ge=1, le=16)]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_total_assets(self) -> "RelayCapabilityLimits":
        if self.max_images + self.max_videos + self.max_audio > 15:
            raise ValueError("combined input media limits must not exceed 15")
        return self


class RelayModeCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_media_types: list[Literal["image", "video", "audio"]]
    input_roles: list[RelayAssetRole] = Field(default_factory=list)
    temporal_controls: list[RelayTemporalControl] = Field(default_factory=list)
    structured_inputs: list[RelayStructuredInput] = Field(default_factory=list)
    supports_face: bool = False
    required_resource_keys: list[str] = Field(default_factory=list)
    conditional_required_resource_keys: dict[
        Literal["face_enabled"], list[str]
    ] = Field(default_factory=dict)
    limits: RelayCapabilityLimits

    @model_validator(mode="after")
    def validate_resource_requirements(self) -> "RelayModeCapability":
        if len(self.input_roles) != len(set(self.input_roles)):
            raise ValueError("input roles must be unique")
        if len(self.temporal_controls) != len(set(self.temporal_controls)):
            raise ValueError("temporal controls must be unique")
        if len(self.structured_inputs) != len(set(self.structured_inputs)):
            raise ValueError("structured inputs must be unique")
        declared_media = set(self.input_media_types)
        for role in self.input_roles:
            if _RELAY_ASSET_ROLE_MEDIA_TYPES[role] not in declared_media:
                raise ValueError(
                    f"input role {role} contradicts input media types"
                )
        for temporal_control in self.temporal_controls:
            if temporal_control not in self.input_roles:
                raise ValueError(
                    f"temporal control {temporal_control} requires its "
                    "matching input role"
                )
        if (
            {"director_shot_v1", "director_shot_v2"}
            & set(self.structured_inputs)
            and not {"reference_image", "first_frame"} & set(self.input_roles)
        ):
            raise ValueError(
                "director shot input requires a composition image role"
            )
        if "director_motion_v1" in self.structured_inputs and (
            not {"first_frame", "last_frame"} <= set(self.input_roles)
            or not {"first_frame", "last_frame"}
            <= set(self.temporal_controls)
            or not {"director_shot_v1", "director_shot_v2"}
            & set(self.structured_inputs)
        ):
            raise ValueError(
                "director_motion_v1 requires a director shot token and "
                "first/last controls"
            )
        conditional_keys: set[str] = set()
        for condition, keys in self.conditional_required_resource_keys.items():
            if not keys:
                raise ValueError(
                    f"{condition} conditional resource keys must not be empty"
                )
            if len(keys) != len(set(keys)):
                raise ValueError(
                    f"{condition} conditional resource keys must be unique"
                )
            conditional_keys.update(keys)
            if condition == "face_enabled" and keys and not self.supports_face:
                raise ValueError(
                    "face_enabled resources require supports_face=true"
                )
        overlap = set(self.required_resource_keys) & conditional_keys
        if overlap:
            raise ValueError(
                "resource keys cannot be both unconditional and conditional"
            )
        return self


class RelayGenerationCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1, 2, 3]
    modes: dict[RelayGenerationMode, RelayModeCapability] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_schema_version_fields(self) -> "RelayGenerationCapabilities":
        for capability in self.modes.values():
            if (
                self.schema_version == 1
                and "conditional_required_resource_keys"
                in capability.model_fields_set
            ):
                raise ValueError(
                    "schema v1 cannot contain v2 capability fields"
                )
            semantic_fields = {
                "input_roles",
                "temporal_controls",
                "structured_inputs",
            }
            if self.schema_version < 3 and (
                semantic_fields & capability.model_fields_set
            ):
                raise ValueError(
                    "schema v1/v2 cannot contain v3 capability fields"
                )
            if self.schema_version == 3:
                missing = semantic_fields - capability.model_fields_set
                if missing:
                    raise ValueError(
                        "schema v3 modes must explicitly contain "
                        + ", ".join(sorted(missing))
                    )
        return self

    def contract_dump(self) -> dict[str, Any]:
        """Serialize without leaking v2 defaults into an accepted v1 document."""

        payload = self.model_dump(mode="json")
        for mode, capability in payload["modes"].items():
            # Go's frozen DTO uses `omitempty` for this v2-only map, so both a
            # missing value and an explicitly empty map are absent from the
            # canonical revision document.
            if not capability.get("conditional_required_resource_keys"):
                capability.pop("conditional_required_resource_keys", None)
            if self.schema_version < 3:
                capability.pop("input_roles", None)
                capability.pop("temporal_controls", None)
                capability.pop("structured_inputs", None)
                continue
        return payload


class RelayModelResource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api_version: Literal["v1"]
    schema_version: Literal[1]
    id: str = Field(min_length=1, max_length=128)
    object: Literal["model"]
    capability_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    lifecycle: Literal["reviewed_candidate", "published_route"]
    managed_route: bool
    customer_callable: bool
    published_route_revision: str = Field(
        pattern=r"^(?:|sha256:[0-9a-f]{64})$"
    )
    capabilities: RelayGenerationCapabilities

    @model_validator(mode="after")
    def validate_publication_state(self) -> "RelayModelResource":
        if self.lifecycle == "reviewed_candidate":
            if (
                self.managed_route
                or self.customer_callable
                or self.published_route_revision != ""
            ):
                raise ValueError(
                    "reviewed Relay candidate cannot claim a published route"
                )
            return self
        if not self.customer_callable or not self.published_route_revision:
            raise ValueError(
                "published Relay route must be customer-callable and versioned"
            )
        return self

    def expected_capability_revision(self) -> str:
        return relay_sha256_revision(self.capabilities.contract_dump())


class RelayModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["list"]
    data: list[RelayModelResource]
    catalog_revision_scope: Literal["transport_snapshot"]
    published_route_revision: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    catalog_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_unique_model_ids(self) -> "RelayModelCatalog":
        model_ids = [model.id for model in self.data]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Relay model catalog contains duplicate model ids")
        return self

    def validate_canonical_revisions(self) -> None:
        model_ids = [model.id for model in self.data]
        if model_ids != sorted(model_ids):
            raise ValueError("Relay model catalog data is not ordered by model id")
        for model in self.data:
            if model.capability_revision != model.expected_capability_revision():
                raise ValueError(
                    f"Relay model {model.id!r} capability revision does not "
                    "match its canonical content"
                )
        revision_input = {
            "models": [
                {
                    "id": model.id,
                    "capability_revision": model.capability_revision,
                    "lifecycle": model.lifecycle,
                    "managed_route": model.managed_route,
                    "customer_callable": model.customer_callable,
                    "published_route_revision": (
                        model.published_route_revision
                    ),
                }
                for model in self.data
            ],
            "published_route_revision": self.published_route_revision,
        }
        if self.catalog_revision != relay_sha256_revision(revision_input):
            raise ValueError(
                "Relay model catalog revision does not match canonical content"
            )


@dataclass(frozen=True, slots=True)
class RelayModelCatalogRead:
    catalog: RelayModelCatalog | None
    etag: str
    not_modified: bool


class RelayProviderCostRectangleEvidence(BaseModel):
    # Optional only for historical/read-only evidence. New commercial task
    # admission requires this exact immutable cost revision.
    cost_revision_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    """One exact route/mode/resolution provider-cost coverage proof."""

    model_config = ConfigDict(extra="forbid", strict=True)

    mode: RelayGenerationMode
    resolution: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$",
    )
    ready: bool
    blocker_code: Literal[
        "provider_cost_runtime_unavailable",
        "provider_cost_sink_unconfigured",
        "provider_contract_rate_missing",
        "provider_contract_rate_invalid",
        "provider_cost_rate_set_missing",
        "provider_cost_rate_set_invalid",
        "provider_usage_cost_materialization_unqualified",
        "provider_customer_unit_cost_ceiling_unqualified",
    ] | None = None
    contract_rate_id: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )
    rate_set_id: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )
    # Legacy single-component contract rates expose the provider metric
    # (output_item/output_second). Versioned multi-component rate sets expose
    # the customer sellable unit (per_item/per_second), because there is no
    # truthful single provider metric for token/media composites.
    billing_unit: Literal[
        "output_item",
        "output_second",
        "per_item",
        "per_second",
    ] | None = None
    unit_amount_cents: int | None = Field(default=None, gt=0, le=10**15)
    currency: Literal["CNY"] | None = None
    effective_from: datetime | None = None
    source_document_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    # Older rate-set evidence remains readable for history, but cannot pass
    # commercial approval or task admission without this executable ceiling.
    customer_unit_cost_ceiling_cny_micros: int | None = Field(
        default=None, gt=0, le=10**15,
    )
    customer_unit_cost_ceiling_revision_sha256: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_cost_rectangle(self) -> "RelayProviderCostRectangleEvidence":
        common_rate_fields = (
            self.billing_unit,
            self.effective_from,
            self.source_document_sha256,
        )
        identity_count = sum(
            value is not None for value in (self.contract_rate_id, self.rate_set_id)
        )
        ceiling_count = sum(value is not None for value in (
            self.customer_unit_cost_ceiling_cny_micros,
            self.customer_unit_cost_ceiling_revision_sha256,
        ))
        if ceiling_count and (
            ceiling_count != 2 or not self.ready or self.rate_set_id is None
            or self.cost_revision_sha256 is None
            or self.billing_unit not in {"per_second", "per_item"}
        ):
            raise ValueError("customer-unit cost ceiling requires a complete ready rate-set proof")
        if self.ready:
            legacy_contract_amount_complete = (
                self.unit_amount_cents is not None and self.currency == "CNY"
            )
            if (
                self.blocker_code is not None
                or identity_count != 1
                or any(value is None for value in common_rate_fields)
                or (
                    self.contract_rate_id is not None
                    and not legacy_contract_amount_complete
                )
                or (
                    self.rate_set_id is not None
                    and (self.unit_amount_cents is not None or self.currency is not None)
                )
            ):
                raise ValueError(
                    "ready provider-cost rectangle requires one complete rate proof"
                )
            if (
                self.effective_from is None
                or self.effective_from.tzinfo is None
                or self.effective_from.utcoffset() != timedelta(0)
            ):
                raise ValueError(
                    "provider-cost rate effective time must be UTC"
                )
        elif (
            self.blocker_code is None
            or self.cost_revision_sha256 is not None
            or identity_count != 0
            or self.unit_amount_cents is not None
            or self.currency is not None
            or any(value is not None for value in common_rate_fields)
        ):
            raise ValueError(
                "blocked provider-cost rectangle must expose only one blocker"
            )
        return self


class RelayModelReleaseRouteEvidence(BaseModel):
    """Secret-free exact route identity exposed for controlled testing only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    route_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    channel_id: int = Field(gt=0)
    provider_name: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_account_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_key_index: int | None = Field(default=None, ge=0)
    provider_key_fingerprint_prefix: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{12}$"
    )
    provider_credential_set_version: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )
    route_binding_sha256: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    upstream_model: str = Field(min_length=1, max_length=256)
    adapter_profile_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    adapter_profile_revision: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    enabled: bool
    accepted: bool
    fresh: bool
    latest_successful_test_at: datetime | None = None
    fresh_until: datetime | None = None
    required_test_modes: list[RelayGenerationMode]
    fresh_test_modes: list[RelayGenerationMode]
    provider_cost_ready: bool
    provider_cost_rectangle_count: int = Field(ge=0)
    provider_cost_ready_rectangle_count: int = Field(ge=0)
    provider_cost_rectangles: list[RelayProviderCostRectangleEvidence]

    @model_validator(mode="after")
    def validate_route_state(self) -> "RelayModelReleaseRouteEvidence":
        account_identity = (
            self.provider_name,
            self.provider_account_id,
            self.provider_key_index,
            self.provider_key_fingerprint_prefix,
            self.provider_credential_set_version,
            self.route_binding_sha256,
        )
        if any(value is not None for value in account_identity) and not all(
            value is not None for value in account_identity
        ):
            raise ValueError("Relay provider account route identity is incomplete")
        if self.accepted and not all(
            value is not None for value in account_identity
        ):
            raise ValueError(
                "accepted Relay route must expose the secret-free account identity"
            )
        # Relay (new-api) expresses freshness as a boolean; timestamps are
        # optional and may be omitted when the relay does not expose them.
        if self.fresh and (not self.enabled or not self.accepted):
            raise ValueError(
                "fresh Relay route evidence must be enabled and accepted"
            )
        if (self.latest_successful_test_at is None) != (self.fresh_until is None):
            raise ValueError("Relay route freshness timestamps must be present together")
        if self.latest_successful_test_at is not None:
            if (
                self.latest_successful_test_at.tzinfo is None
                or self.latest_successful_test_at.utcoffset() is None
                or self.fresh_until is None
                or self.fresh_until.tzinfo is None
                or self.fresh_until.utcoffset() is None
                or self.fresh_until <= self.latest_successful_test_at
            ):
                raise ValueError("Relay route freshness timestamps are invalid")
        if (
            self.required_test_modes
            != sorted(set(self.required_test_modes))
            or self.fresh_test_modes != sorted(set(self.fresh_test_modes))
            or not set(self.fresh_test_modes) <= set(self.required_test_modes)
        ):
            raise ValueError("Relay route test modes must be canonical")
        # Relay (new-api) may report fresh=True with an empty fresh_test_modes
        # list when it no longer reports per-mode freshness. Treat the boolean
        # as authoritative instead of requiring mode-level equality.
        if self.required_test_modes and self.fresh_test_modes:
            all_modes_fresh = self.fresh_test_modes == self.required_test_modes
            if self.fresh != all_modes_fresh:
                raise ValueError("Relay route freshness does not match tested modes")
        rectangle_keys = [
            (rectangle.mode, rectangle.resolution)
            for rectangle in self.provider_cost_rectangles
        ]
        if rectangle_keys != sorted(set(rectangle_keys)):
            raise ValueError("provider-cost rectangles must be canonical")
        if len(self.provider_cost_rectangles) != self.provider_cost_rectangle_count:
            raise ValueError("provider-cost rectangle count does not match")
        if (
            sum(rectangle.ready for rectangle in self.provider_cost_rectangles)
            != self.provider_cost_ready_rectangle_count
        ):
            raise ValueError("ready provider-cost rectangle count does not match")
        expected_cost_ready = (
            self.provider_cost_rectangle_count > 0
            and self.provider_cost_ready_rectangle_count
            == self.provider_cost_rectangle_count
        )
        if self.provider_cost_ready != expected_cost_ready:
            raise ValueError("provider-cost route readiness does not match rectangles")
        return self


class RelayModelReleaseEvidenceItem(BaseModel):
    """Secret-free proof that the current Relay route release was tested.

    This projection is intentionally model-level. Credential fingerprints and
    provider endpoints remain inside Relay; Platform only needs enough durable
    identity to reject stale or partially tested routing releases.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    public_model_id: str = Field(min_length=1, max_length=128)
    capability_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    # Legacy or otherwise unbound routes are a legitimate *blocked* Relay
    # projection.  Relay intentionally omits this pair for those rows.  The
    # pair becomes mandatory only when Relay claims the release is ready.
    model_release_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    model_release_revision: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    published_route_revision: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    routing_release_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_cost_readiness_sha256: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    provider_cost_ready: bool
    provider_cost_rectangle_count: int = Field(ge=0)
    provider_cost_ready_rectangle_count: int = Field(ge=0)
    route_count: int = Field(ge=0)
    enabled_route_count: int = Field(ge=0)
    accepted_route_count: int = Field(ge=0)
    fresh_test_count: int = Field(ge=0)
    latest_successful_test_at: datetime | None = None
    status: Literal["ready", "blocked"]
    routes: list[RelayModelReleaseRouteEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_release_evidence(self) -> "RelayModelReleaseEvidenceItem":
        if (self.model_release_id is None) != (
            self.model_release_revision is None
        ):
            raise ValueError(
                "Relay model release id and revision must be present together"
            )
        if self.enabled_route_count > self.route_count:
            raise ValueError("enabled Relay routes cannot exceed route count")
        if self.accepted_route_count > self.route_count:
            raise ValueError("accepted Relay routes cannot exceed route count")
        # fresh_test_count may transiently exceed enabled_route_count while
        # the relay updates route state; downstream blockers handle the
        # actual enabled/accepted/fresh values, so do not fail the whole
        # evidence on this transient inconsistency.
        if self.latest_successful_test_at is not None and (
            self.latest_successful_test_at.tzinfo is None
            or self.latest_successful_test_at.utcoffset() is None
        ):
            raise ValueError("Relay release test time must include a UTC offset")
        route_ids = [route.route_id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("Relay release evidence contains duplicate route ids")
        if self.routes:
            if len(self.routes) != self.route_count:
                raise ValueError("Relay route summaries do not match route count")
            if sum(route.enabled for route in self.routes) != self.enabled_route_count:
                raise ValueError("Relay route summaries do not match enabled count")
            if sum(route.accepted for route in self.routes) != self.accepted_route_count:
                raise ValueError("Relay route summaries do not match accepted count")
            if sum(route.fresh for route in self.routes) != self.fresh_test_count:
                raise ValueError("Relay route summaries do not match fresh test count")
            if (
                sum(
                    route.provider_cost_rectangle_count for route in self.routes
                )
                != self.provider_cost_rectangle_count
                or sum(
                    route.provider_cost_ready_rectangle_count
                    for route in self.routes
                )
                != self.provider_cost_ready_rectangle_count
            ):
                raise ValueError(
                    "Relay provider-cost summaries do not match rectangle counts"
                )
        expected_cost_ready = (
            self.route_count > 0
            and self.provider_cost_rectangle_count > 0
            and self.provider_cost_ready_rectangle_count
            == self.provider_cost_rectangle_count
            and bool(self.routes)
            and all(route.provider_cost_ready for route in self.routes)
        )
        if self.provider_cost_ready != expected_cost_ready:
            raise ValueError("Relay provider-cost readiness is inconsistent")
        if self.status == "ready" and (
            self.model_release_id is None
            or self.model_release_revision is None
            or self.published_route_revision is None
            or self.route_count < 1
            or self.enabled_route_count != self.route_count
            or self.accepted_route_count != self.route_count
            or self.fresh_test_count != self.route_count
        ):
            raise ValueError("ready Relay release evidence is incomplete")
        return self


class RelayModelReleaseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    object: Literal["relay.model_release_evidence"]
    catalog_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    catalog_revision_scope: Literal["transport_snapshot"]
    published_route_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generated_at: datetime
    test_freshness_max_age_seconds: int = Field(ge=30, le=604800)
    models: list[RelayModelReleaseEvidenceItem]

    @model_validator(mode="after")
    def validate_projection(self) -> "RelayModelReleaseEvidence":
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("Relay release evidence generated_at requires UTC offset")
        model_ids = [item.public_model_id for item in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Relay release evidence contains duplicate model ids")
        if model_ids != sorted(model_ids):
            raise ValueError("Relay release evidence is not ordered by model id")
        for item in self.models:
            if (
                item.latest_successful_test_at is not None
                and item.latest_successful_test_at
                > self.generated_at + timedelta(seconds=30)
            ):
                raise ValueError("Relay release evidence test time is in the future")
            if item.status == "ready" and item.latest_successful_test_at is not None:
                age = self.generated_at - item.latest_successful_test_at
                if age > timedelta(seconds=self.test_freshness_max_age_seconds):
                    raise ValueError("Relay marked stale release evidence ready")
            for route in item.routes:
                if (
                    route.latest_successful_test_at is not None
                    and route.latest_successful_test_at
                    > self.generated_at + timedelta(seconds=30)
                ):
                    raise ValueError("Relay route test time is in the future")
                if (
                    route.fresh
                    and route.fresh_until is not None
                    and route.fresh_until <= self.generated_at
                ):
                    raise ValueError("fresh Relay route evidence is already expired")
                for rectangle in route.provider_cost_rectangles:
                    if (
                        rectangle.effective_from is not None
                        and rectangle.effective_from
                        > self.generated_at + timedelta(seconds=30)
                    ):
                        raise ValueError(
                            "Relay provider-cost rate is not yet effective"
                        )
        return self


class RelayUnknownSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["generation.reconciliation"]
    job_id: UUID
    tenant_id: UUID
    client_reference_id: str | None = None
    model: str
    mode: Literal[
        "text_to_image",
        "image_to_image",
        "text_to_video",
        "image_to_video",
        "video_to_video",
    ]
    status: Literal["reconciliation_required"]
    provider_route_id: int = Field(gt=0)
    provider_route_key: str = Field(min_length=1, max_length=120)
    provider_name: str = Field(min_length=1, max_length=64)
    provider_account_id: str = Field(min_length=1, max_length=128)
    provider_channel_id: int = Field(gt=0)
    provider_key_index: int = Field(ge=0)
    provider_channel_class: Literal["reverse", "third_party_api", "official"]
    provider_upstream_model: str = Field(min_length=1, max_length=128)
    provider_submission_attempt: int = Field(gt=0)
    unknown_at: datetime
    reconciliation_token: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    error_code: str
    error_message: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> "RelayUnknownSubmission":
        for value in (self.unknown_at, self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Relay reconciliation timestamps require a UTC offset")
        if self.updated_at < self.created_at:
            raise ValueError("Relay reconciliation updated_at cannot precede created_at")
        return self


class RelayUnknownSubmissionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["list"]
    data: list[RelayUnknownSubmission]
    page: int = Field(ge=1, le=1_000_000)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class RelayProviderResultReconciliation(BaseModel):
    """Secret-free read-only projection of retained provider evidence.

    The Relay intentionally does not expose the native task payload, provider
    URL, failure text, or credential binding through this operations surface.
    Keeping ``extra="forbid"`` here makes that boundary fail closed if a Relay
    regression adds one of those fields to either list or detail responses.
    """

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["generation.provider_result_reconciliation"]
    job_id: UUID
    tenant_id: UUID
    client_reference_id: str | None = Field(default=None, strict=True, max_length=128)
    model: str = Field(strict=True, min_length=1, max_length=128)
    mode: RelayGenerationMode
    status: RelayJobStatus
    progress: int = Field(strict=True, ge=0, le=100)
    provider_route_id: int = Field(strict=True, gt=0)
    provider_channel_id: int = Field(strict=True, gt=0)
    provider_submission_attempt: int = Field(strict=True, gt=0)
    upstream_task_id: str = Field(strict=True, max_length=191)
    reconciliation_kind: Literal[
        "missing_native_task",
        "provider_result_proof",
        "provider_material",
        "unknown",
    ]
    resolution_supported: bool = Field(strict=True)
    evidence_retained: Annotated[bool, Field(strict=True)]
    error_code: str = Field(strict=True, max_length=160)
    error_message: str = Field(strict=True, min_length=1, max_length=512)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def validate_timestamp_wire_type(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError(
                "Relay provider-result reconciliation timestamps must be RFC3339 strings"
            )
        return value

    @field_validator(
        "model",
        "upstream_task_id",
        "error_code",
        "error_message",
    )
    @classmethod
    def validate_secret_free_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        ):
            raise ValueError(
                "Relay provider-result reconciliation text contains unsafe whitespace or controls"
            )
        lowered = value.casefold()
        if "://" in lowered or lowered.startswith("//"):
            raise ValueError(
                "Relay provider-result reconciliation text must not contain a URL"
            )
        return value

    @field_validator("client_reference_id")
    @classmethod
    def validate_client_reference_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        ):
            raise ValueError(
                "Relay provider-result client reference contains unsafe whitespace or controls"
            )
        return value

    @model_validator(mode="after")
    def validate_projection(self) -> "RelayProviderResultReconciliation":
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(
                    "Relay provider-result reconciliation timestamps require UTC"
                )
        if self.updated_at < self.created_at:
            raise ValueError(
                "Relay provider-result reconciliation updated_at cannot precede created_at"
            )
        if self.resolution_supported:
            raise ValueError(
                "Relay provider-result reconciliation must remain read only"
            )
        if self.reconciliation_kind == "provider_material":
            if self.status not in {"reconciliation_required", "succeeded"}:
                raise ValueError(
                    "Relay provider-material reconciliation status is invalid"
                )
        elif self.status != "reconciliation_required":
            raise ValueError(
                "Relay provider-result reconciliation must remain fail closed"
            )
        expected_messages = {
            "missing_native_task": (
                "Native task evidence is unavailable and requires manual reconciliation"
            ),
            "provider_result_proof": (
                "Provider terminal proof requires manual reconciliation"
            ),
            "provider_material": (
                "Retained provider material requires manual reconciliation"
            ),
            "unknown": (
                "Provider result evidence requires manual reconciliation"
            ),
        }
        if self.error_message != expected_messages[self.reconciliation_kind]:
            raise ValueError(
                "Relay provider-result reconciliation message is not server owned"
            )
        expected_error_code = (
            "" if self.status == "succeeded" else "PROVIDER_POLL_RECONCILIATION_REQUIRED"
        )
        if self.error_code != expected_error_code:
            raise ValueError(
                "Relay provider-result reconciliation error code is inconsistent"
            )
        return self


class RelayProviderResultReconciliationPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["list"]
    data: list[RelayProviderResultReconciliation]
    page: int = Field(strict=True, ge=1, le=1_000_000)
    page_size: int = Field(strict=True, ge=1, le=100)
    total: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_page(self) -> "RelayProviderResultReconciliationPage":
        if len(self.data) > self.page_size or len(self.data) > self.total:
            raise ValueError("Relay provider-result reconciliation page is inconsistent")
        job_ids = [item.job_id for item in self.data]
        if len(job_ids) != len(set(job_ids)):
            raise ValueError(
                "Relay provider-result reconciliation page contains duplicate jobs"
            )
        return self


class RelaySynchronousResultEvidence(BaseModel):
    """Private evidence for a lost synchronous image-generation response.

    ``artifact_url`` may contain bearer-like query material.  It is accepted
    only long enough to bind the Platform approval signature and send it to
    Relay; audit summaries and Relay receipts use its SHA-256 digest instead.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    provider_model_id: str = Field(min_length=1, max_length=128)
    provider_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_url: str = Field(min_length=1, max_length=8192)
    provider_created_at: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    )
    generated_images: int = Field(ge=1, le=16)
    output_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)
    total_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)

    @model_validator(mode="after")
    def validate_evidence(self) -> "RelaySynchronousResultEvidence":
        if self.provider_model_id != self.provider_model_id.strip():
            raise ValueError("provider_model_id must not contain edge whitespace")
        if len(self.provider_model_id.encode("utf-8")) > 128:
            raise ValueError("provider_model_id exceeds the UTF-8 byte limit")
        parsed_url = urlsplit(self.artifact_url)
        if (
            self.artifact_url != self.artifact_url.strip()
            or len(self.artifact_url.encode("utf-8")) > 8192
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.hostname is None
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.fragment
        ):
            raise ValueError("artifact_url must be an absolute credential-free HTTPS URL")
        try:
            parsed_created_at = datetime.strptime(
                self.provider_created_at,
                "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError("provider_created_at must be canonical UTC RFC3339") from exc
        if parsed_created_at.strftime("%Y-%m-%dT%H:%M:%SZ") != self.provider_created_at:
            raise ValueError("provider_created_at must be canonical UTC RFC3339")
        if self.total_tokens < self.output_tokens:
            raise ValueError("total_tokens must be greater than or equal to output_tokens")
        return self

    def receipt_summary(self) -> dict[str, str | int]:
        return {
            "provider_model_id": self.provider_model_id,
            "provider_response_sha256": self.provider_response_sha256,
            "artifact_url_sha256": hashlib.sha256(
                self.artifact_url.encode("utf-8")
            ).hexdigest(),
            "provider_created_at": self.provider_created_at,
            "generated_images": self.generated_images,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


class RelaySynchronousResultReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider_model_id: str = Field(min_length=1, max_length=128)
    provider_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_url_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_created_at: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    )
    generated_images: int = Field(ge=1, le=16)
    output_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)
    total_tokens: int = Field(ge=0, le=9_223_372_036_854_775_807)

    @model_validator(mode="after")
    def validate_receipt(self) -> "RelaySynchronousResultReceipt":
        if self.provider_model_id != self.provider_model_id.strip():
            raise ValueError("provider_model_id must not contain edge whitespace")
        if len(self.provider_model_id.encode("utf-8")) > 128:
            raise ValueError("provider_model_id exceeds the UTF-8 byte limit")
        try:
            parsed_created_at = datetime.strptime(
                self.provider_created_at,
                "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError("provider_created_at must be canonical UTC RFC3339") from exc
        if parsed_created_at.strftime("%Y-%m-%dT%H:%M:%SZ") != self.provider_created_at:
            raise ValueError("provider_created_at must be canonical UTC RFC3339")
        if self.total_tokens < self.output_tokens:
            raise ValueError("total_tokens must be greater than or equal to output_tokens")
        return self


class RelayUnknownSubmissionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["generation.reconciliation_result"]
    event_id: UUID
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
    tenant_id: UUID
    job_id: UUID
    outcome: Literal["created", "not_created"]
    upstream_task_id: str = Field(max_length=191)
    expected_route_id: int = Field(gt=0)
    expected_submission_attempt: int = Field(gt=0)
    expected_reconciliation_token: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    verification_reference: str = Field(min_length=1, max_length=191)
    approved_by: str = Field(min_length=1, max_length=128)
    approval_reason: str = Field(min_length=3, max_length=240)
    approval_key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
    approval_signature: str = Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")
    synchronous_result: RelaySynchronousResultReceipt | None = None
    resolved_status: Literal["processing", "failed"]
    current_status: RelayJobStatus
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_at: datetime

    @model_validator(mode="after")
    def validate_result(self) -> "RelayUnknownSubmissionResult":
        for value in (
            self.upstream_task_id,
            self.verification_reference,
            self.approved_by,
            self.approval_reason,
        ):
            if value != value.strip():
                raise ValueError("Relay reconciliation evidence has edge whitespace")
        for field_name, value, max_bytes in (
            ("verification_reference", self.verification_reference, 191),
            ("approved_by", self.approved_by, 128),
            ("approval_reason", self.approval_reason, 240),
        ):
            validate_relay_utf8_byte_limit(
                value,
                field_name=field_name,
                max_bytes=max_bytes,
            )
        if self.outcome == "created":
            if not self.upstream_task_id or self.resolved_status != "processing":
                raise ValueError("created reconciliation result is inconsistent")
            if self.synchronous_result is not None and self.upstream_task_id != (
                "seedream:" + self.synchronous_result.provider_response_sha256
            ):
                raise ValueError("synchronous reconciliation identity is inconsistent")
        elif self.upstream_task_id or self.resolved_status != "failed":
            raise ValueError("not_created reconciliation result is inconsistent")
        elif self.synchronous_result is not None:
            raise ValueError("not_created reconciliation forbids synchronous result")
        if self.resolved_at.tzinfo is None or self.resolved_at.utcoffset() is None:
            raise ValueError("Relay reconciliation timestamp requires a UTC offset")
        return self


RelayCallbackDeliveryState = Literal["pending", "claimed", "delivered", "dead_letter"]


class RelayCallbackRedriveEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=3, max_length=240)
    previous_state: Literal["dead_letter"]
    previous_attempts: int = Field(strict=True, ge=0)
    previous_max_attempts: int = Field(strict=True, ge=1, le=100)
    previous_response_status: int = Field(strict=True, ge=0, le=599)
    previous_last_error: str = Field(max_length=64)
    previous_dead_lettered_at: datetime
    callback_url_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_callback_request_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$"
    )
    result_state: Literal["pending"]
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redriven_at: datetime

    @model_validator(mode="after")
    def validate_evidence(self) -> "RelayCallbackRedriveEvidence":
        if self.actor != self.actor.strip() or self.reason != self.reason.strip():
            raise ValueError("Relay callback redrive evidence has edge whitespace")
        for value in (self.previous_dead_lettered_at, self.redriven_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Relay callback redrive timestamps require a UTC offset")
        return self


class RelayCallbackDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["generation.callback_delivery"]
    event_id: UUID
    tenant_id: UUID
    job_id: UUID
    source_client_id: str = Field(min_length=1, max_length=128)
    original_request_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$"
    )
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    callback_url_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: RelayCallbackDeliveryState
    attempts: int = Field(strict=True, ge=0)
    max_attempts: int = Field(strict=True, ge=1, le=100)
    available_at: datetime
    response_status: int = Field(strict=True, ge=0, le=599)
    last_error: str = Field(max_length=64)
    delivered_at: datetime | None
    dead_lettered_at: datetime | None
    created_at: datetime
    updated_at: datetime
    redrives: list[RelayCallbackRedriveEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_delivery(self) -> "RelayCallbackDelivery":
        timestamps = [self.available_at, self.created_at, self.updated_at]
        timestamps.extend(
            value
            for value in (self.delivered_at, self.dead_lettered_at)
            if value is not None
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("Relay callback delivery timestamps require a UTC offset")
        if self.updated_at < self.created_at:
            raise ValueError("Relay callback delivery updated_at precedes created_at")
        if self.state == "dead_letter" and self.dead_lettered_at is None:
            raise ValueError("Relay callback dead letter requires dead_lettered_at")
        if self.state == "delivered" and self.delivered_at is None:
            raise ValueError("Relay callback delivered state requires delivered_at")
        return self


class RelayCallbackDeliveryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["list"]
    data: list[RelayCallbackDelivery]
    page: int = Field(ge=1, le=1_000_000)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class RelayCallbackRedriveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    object: Literal["generation.callback_redrive_result"]
    delivery_event_id: UUID
    tenant_id: UUID
    current_state: RelayCallbackDeliveryState
    evidence: RelayCallbackRedriveEvidence


RelayChannelStatus = Literal[
    "enabled", "manually_disabled", "auto_disabled"
]
RelayChannelOperationKind = Literal["test", "status"]
RelayChannelOperationState = Literal["pending", "succeeded", "failed"]
RelayChannelTestSubmissionState = Literal[
    "not_started",
    "submission_unknown",
    "submitted",
    "artifact_verified",
    "provider_terminal",
    "provider_rejected",
    "pre_submit_failed",
    "reconciled_no_creation",
]


class RelayChannelCredentialState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: Annotated[bool, Field(strict=True)]
    key_count: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_credential_state(self) -> "RelayChannelCredentialState":
        if self.configured != (self.key_count > 0):
            raise ValueError("Relay channel credential state is inconsistent")
        return self


class RelayChannel(BaseModel):
    """Secret-free channel control-plane projection returned by Relay."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(strict=True, gt=0)
    name: str = Field(min_length=1, max_length=191)
    type: int = Field(strict=True, ge=0)
    type_label: str = Field(min_length=1, max_length=128)
    test_supported: Annotated[bool, Field(strict=True)]
    status: RelayChannelStatus
    configured_models: list[str] = Field(default_factory=list, max_length=512)
    test_model: str | None = Field(default=None, max_length=191)
    # Provider-onboarding routing policy is shared with Relay and its UI.  A
    # negative priority is valid (lower preference), while managed weights are
    # strictly positive.  Keep zero weight readable for historical unmanaged
    # native channels, then enforce the managed-account bound below.
    weight: int = Field(strict=True, ge=0, le=1_000_000)
    priority: int = Field(strict=True, ge=-1_000_000, le=1_000_000)
    auto_ban: Annotated[bool, Field(strict=True)]
    tag: str = Field(default="", max_length=64)
    provider_onboarding_managed: Annotated[bool, Field(strict=True)] = False
    created_at: datetime
    last_tested_at: datetime | None
    response_time_ms: int | None = Field(default=None, strict=True, ge=0)
    credential: RelayChannelCredentialState
    revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("created_at", "last_tested_at", mode="before")
    @classmethod
    def require_rfc3339_timestamp(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Relay channel timestamps must use RFC3339 strings")
        return value

    @model_validator(mode="after")
    def validate_channel(self) -> "RelayChannel":
        if self.provider_onboarding_managed and self.weight < 1:
            raise ValueError("Managed Relay channel weight must be positive")
        if (
            self.name != self.name.strip()
            or self.type_label != self.type_label.strip()
            or self.tag != self.tag.strip()
        ):
            raise ValueError("Relay channel text has edge whitespace")
        if self.test_model is not None and (
            not self.test_model or self.test_model != self.test_model.strip()
        ):
            raise ValueError("Relay channel test_model is invalid")
        if len(set(self.configured_models)) != len(self.configured_models):
            raise ValueError("Relay channel models must be unique")
        if any(
            not model or model != model.strip() or len(model) > 191
            for model in self.configured_models
        ):
            raise ValueError("Relay channel model is invalid")
        timestamps = [self.created_at]
        if self.last_tested_at is not None:
            timestamps.append(self.last_tested_at)
        if any(
            value.tzinfo is None
            or value.utcoffset() is None
            or value.utcoffset() != timedelta(0)
            for value in timestamps
        ):
            raise ValueError("Relay channel timestamps must be UTC")
        return self


class RelayChannelPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(
        min_length=1, max_length=16, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$"
    )
    schema_version: Literal[1]
    object: Literal["list"]
    data: list[RelayChannel]
    page: int = Field(strict=True, ge=1, le=1_000_000)
    page_size: int = Field(strict=True, ge=1, le=100)
    total: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_page(self) -> "RelayChannelPage":
        if len({item.id for item in self.data}) != len(self.data):
            raise ValueError("Relay channel page contains duplicate identities")
        if len(self.data) > self.page_size or self.total < len(self.data):
            raise ValueError("Relay channel page metadata is inconsistent")
        return self


RelayChannelTestErrorCode = Literal[
    "CHANNEL_TEST_FAILED",
    "CHANNEL_TEST_UNAVAILABLE",
    "CHANNEL_TEST_PROVIDER_VALIDATION",
    "CHANNEL_TEST_PROVIDER_AUTH",
    "CHANNEL_TEST_PROVIDER_QUOTA",
    "CHANNEL_TEST_PROVIDER_TERMINAL",
    "CHANNEL_TEST_ARTIFACT_INVALID",
    "CHANNEL_TEST_ROUTE_DRIFT",
    "CHANNEL_TEST_RECONCILED_NO_CREATION",
]

RelayChannelTestProviderBlockerCode = Literal[
    "CHANNEL_TEST_FAILED",
    "CHANNEL_TEST_UNAVAILABLE",
    "CHANNEL_TEST_PROVIDER_VALIDATION",
    "CHANNEL_TEST_PROVIDER_AUTH",
    "CHANNEL_TEST_PROVIDER_QUOTA",
    "CHANNEL_TEST_PROVIDER_TERMINAL",
    "CHANNEL_TEST_ARTIFACT_INVALID",
    "CHANNEL_TEST_ROUTE_DRIFT",
]


class RelayChannelTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Annotated[bool, Field(strict=True)]
    response_time_ms: int = Field(strict=True, ge=0)
    error_code: RelayChannelTestErrorCode | None = None

    @model_validator(mode="after")
    def validate_test_result(self) -> "RelayChannelTestResult":
        if self.success == (self.error_code is not None):
            raise ValueError("Relay channel test result is inconsistent")
        return self


class RelayChannelStatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_status: RelayChannelStatus
    current_status: RelayChannelStatus
    changed: Annotated[bool, Field(strict=True)]
    error_code: Literal["CHANNEL_REVISION_CONFLICT"] | None = None

    @model_validator(mode="after")
    def validate_status_result(self) -> "RelayChannelStatusResult":
        if self.changed == (self.previous_status == self.current_status):
            raise ValueError("Relay channel status result is inconsistent")
        if self.error_code is not None and (
            self.changed or self.previous_status != self.current_status
        ):
            raise ValueError("Relay channel status conflict result is inconsistent")
        return self


class RelayChannelOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: str = Field(
        min_length=1, max_length=16, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$"
    )
    schema_version: Literal[1]
    object: Literal["relay.channel_control_operation"]
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    tenant_id: UUID
    channel_id: int = Field(strict=True, gt=0)
    kind: RelayChannelOperationKind
    state: RelayChannelOperationState
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=3, max_length=240)
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    result_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    expected_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    target_status: Literal["enabled", "manually_disabled"] | None = None
    result: RelayChannelTestResult | RelayChannelStatusResult | None = None
    provider_submission_state: RelayChannelTestSubmissionState | None = None
    provider_blocker_code: RelayChannelTestProviderBlockerCode | None = None
    reconciliation_actor: str | None = Field(default=None, min_length=1, max_length=128)
    reconciliation_reason: str | None = Field(default=None, min_length=3, max_length=240)
    reconciled_at: datetime | None = None
    created_at: datetime
    completed_at: datetime | None = None
    idempotent_replay: Annotated[bool, Field(strict=True)] = False

    @field_validator("created_at", "completed_at", "reconciled_at", mode="before")
    @classmethod
    def require_rfc3339_timestamp(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Relay channel operation timestamps must use RFC3339 strings")
        return value

    @model_validator(mode="after")
    def validate_operation(self) -> "RelayChannelOperation":
        if self.actor != self.actor.strip() or self.reason != self.reason.strip():
            raise ValueError("Relay channel operation evidence has edge whitespace")
        for field_name, value, max_bytes in (
            ("actor", self.actor, 128),
            ("reason", self.reason, 240),
            ("reconciliation_actor", self.reconciliation_actor, 128),
            ("reconciliation_reason", self.reconciliation_reason, 240),
        ):
            if value is None:
                continue
            if value != value.strip():
                raise ValueError(
                    "Relay channel operation reconciliation evidence has edge whitespace"
                )
            validate_relay_utf8_byte_limit(
                value,
                field_name=field_name,
                max_bytes=max_bytes,
            )
        if self.reconciliation_actor is not None:
            validate_relay_channel_reconciliation_actor(self.reconciliation_actor)
        if self.reconciliation_reason is not None:
            validate_relay_channel_reconciliation_reason(self.reconciliation_reason)
        timestamps = [self.created_at]
        if self.completed_at is not None:
            timestamps.append(self.completed_at)
        if self.reconciled_at is not None:
            timestamps.append(self.reconciled_at)
        if any(
            value.tzinfo is None
            or value.utcoffset() is None
            or value.utcoffset() != timedelta(0)
            for value in timestamps
        ):
            raise ValueError("Relay channel operation timestamps must be UTC")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("Relay channel operation completed_at precedes created_at")
        reconciliation_fields = (
            self.reconciliation_actor,
            self.reconciliation_reason,
            self.reconciled_at,
        )
        if any(value is not None for value in reconciliation_fields) and not all(
            value is not None for value in reconciliation_fields
        ):
            raise ValueError("Relay channel reconciliation evidence is incomplete")
        if self.state == "pending":
            if self.completed_at is not None or self.result is not None:
                raise ValueError("Pending Relay channel operation has terminal data")
        elif self.completed_at is None or self.result is None:
            raise ValueError("Terminal Relay channel operation is incomplete")
        if self.kind == "test":
            if self.expected_revision is not None or self.target_status is not None:
                raise ValueError("Relay channel test operation has status intent fields")
            if self.result is not None and not isinstance(
                self.result, RelayChannelTestResult
            ):
                raise ValueError("Relay channel test operation result is invalid")
            if isinstance(self.result, RelayChannelTestResult) and (
                (self.state == "succeeded") != self.result.success
            ):
                raise ValueError("Relay channel test operation state is inconsistent")
            if (
                isinstance(self.result, RelayChannelTestResult)
                and self.result.error_code
                == "CHANNEL_TEST_RECONCILED_NO_CREATION"
                and self.provider_submission_state != "reconciled_no_creation"
            ):
                raise ValueError(
                    "Relay no-creation result lacks reconciliation evidence"
                )
            if self.provider_submission_state is not None:
                allowed_submission_states = {
                    "pending": {
                        "not_started",
                        "submission_unknown",
                        "submitted",
                    },
                    "succeeded": {"artifact_verified"},
                    "failed": {
                        "pre_submit_failed",
                        "provider_terminal",
                        "provider_rejected",
                        "reconciled_no_creation",
                    },
                }
                if self.provider_submission_state not in allowed_submission_states[
                    self.state
                ]:
                    raise ValueError(
                        "Relay route-test submission state conflicts with its receipt state"
                    )
            if self.provider_blocker_code is not None and not (
                self.state == "pending"
                and self.provider_submission_state == "submitted"
            ):
                raise ValueError(
                    "Relay route-test provider blocker conflicts with its receipt state"
                )
            # A missing provider_submission_state remains readable only for
            # legacy generic channel-test receipts. New route-bound write
            # responses are required to carry a state by the HTTP client.
            if self.provider_submission_state == "reconciled_no_creation":
                if (
                    self.state != "failed"
                    or not isinstance(self.result, RelayChannelTestResult)
                    or self.result.success
                    or self.result.response_time_ms != 0
                    or self.result.error_code
                    != "CHANNEL_TEST_RECONCILED_NO_CREATION"
                    or not all(value is not None for value in reconciliation_fields)
                    or self.reconciled_at != self.completed_at
                ):
                    raise ValueError(
                        "Relay no-creation reconciliation receipt is inconsistent"
                    )
            elif any(value is not None for value in reconciliation_fields):
                raise ValueError(
                    "Relay channel reconciliation evidence lacks its terminal state"
                )
        else:
            if (
                self.provider_submission_state is not None
                or self.provider_blocker_code is not None
                or any(value is not None for value in reconciliation_fields)
            ):
                raise ValueError(
                    "Relay channel status operation has test-submission evidence"
                )
            if self.expected_revision is None or self.target_status is None:
                raise ValueError("Relay channel status operation is missing intent fields")
            if self.result is not None and not isinstance(
                self.result, RelayChannelStatusResult
            ):
                raise ValueError("Relay channel status operation result is invalid")
            if isinstance(self.result, RelayChannelStatusResult) and (
                (self.state == "failed") != (self.result.error_code is not None)
            ):
                raise ValueError("Relay channel status operation state is inconsistent")
            if (
                self.state == "succeeded"
                and isinstance(self.result, RelayChannelStatusResult)
                and self.result.current_status != self.target_status
            ):
                raise ValueError("Relay channel status result conflicts with its intent")
            if self.state != "pending" and isinstance(
                self.result, RelayChannelStatusResult
            ):
                if self.previous_revision is None or self.result_revision is None:
                    raise ValueError("Relay channel status receipt lacks revisions")
                if self.result.changed != (
                    self.previous_revision != self.result_revision
                ):
                    raise ValueError("Relay channel status revisions are inconsistent")
        return self


class RelayClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        relay_error: RelayErrorEnvelopeDetail | None = None,
        response_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.relay_error = relay_error
        self.response_status = response_status

    def diagnostic_snapshot(self) -> dict[str, Any] | None:
        if self.relay_error is None:
            return None
        return {
            **self.relay_error.model_dump(mode="json"),
            "http_status": self.response_status,
        }


class RelayTemporaryError(RelayClientError):
    def __init__(
        self,
        message: str,
        *,
        submission_outcome_unknown: bool = True,
        relay_error: RelayErrorEnvelopeDetail | None = None,
        response_status: int | None = None,
    ) -> None:
        super().__init__(
            message,
            relay_error=relay_error,
            response_status=response_status,
        )
        self.submission_outcome_unknown = submission_outcome_unknown


class RelayPermanentError(RelayClientError):
    pass


def validate_model_catalog_release_evidence_pair(
    *,
    catalog: RelayModelCatalog,
    evidence: RelayModelReleaseEvidence,
) -> None:
    """Reject independently valid Relay projections from different snapshots."""

    if (
        evidence.catalog_revision_scope != catalog.catalog_revision_scope
        or evidence.catalog_revision != catalog.catalog_revision
        or evidence.published_route_revision
        != catalog.published_route_revision
    ):
        raise RelayPermanentError(
            "Relay model catalog and release evidence snapshots do not match"
        )

    catalog_models = {item.id: item for item in catalog.data}
    evidence_models = {item.public_model_id: item for item in evidence.models}
    if catalog_models.keys() != evidence_models.keys():
        raise RelayPermanentError(
            "Relay model catalog and release evidence model sets do not match"
        )
    for model_id, catalog_item in catalog_models.items():
        evidence_item = evidence_models[model_id]
        expected_route_revision = catalog_item.published_route_revision or None
        if (
            evidence_item.capability_revision
            != catalog_item.capability_revision
            or evidence_item.published_route_revision
            != expected_route_revision
        ):
            raise RelayPermanentError(
                "Relay model catalog and release evidence model versions do not match"
            )
        if catalog_item.lifecycle == "reviewed_candidate" and (
            evidence_item.status != "blocked"
            or evidence_item.route_count != 0
        ):
            raise RelayPermanentError(
                "Relay reviewed candidate unexpectedly exposes release routes"
            )


def validate_protected_model_catalog_routes(
    *,
    catalog: RelayModelCatalog,
) -> None:
    """Reject customer-callable compatibility routes in protected runtimes."""

    unmanaged = sorted(
        item.id
        for item in catalog.data
        if item.lifecycle == "published_route"
        and item.customer_callable
        and not item.managed_route
    )
    if unmanaged:
        raise RelayPermanentError(
            "Protected Platform runtime received an unmanaged Relay route"
        )


class RelayIdempotencyConflictError(RelayPermanentError):
    """Relay already accepted a different payload under this stable key."""


def validate_signed_download_url(
    url: str | HttpUrl, *, allow_local_http: bool = False
) -> None:
    parsed = httpx.URL(str(url))
    if parsed.userinfo:
        raise RelayPermanentError("Relay returned a credential-bearing artifact URL")
    if parsed.scheme == "https":
        return
    if (
        allow_local_http
        and parsed.scheme == "http"
        and parsed.host in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise RelayPermanentError("Relay returned an unsafe artifact download URL")


def _public_artifact_hostname(hostname: str) -> bool:
    normalized = hostname.casefold().rstrip(".")
    if (
        not normalized
        or normalized in {"localhost", "localhost.localdomain"}
        or normalized.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "." in normalized
    return address.is_global


def validate_bound_artifact_download(
    download: RelaySignedDownload,
    *,
    production: bool,
    allow_legacy: bool,
    now: datetime | None = None,
) -> RelayArtifactStorageBinding | None:
    """Validate that a signed URL names the exact structured OBS object.

    The URL is never persisted by this helper.  Production accepts only a
    Huawei OBS binding and a freshly issued URL whose host/path are a canonical
    representation of the declared bucket and object key.
    """

    binding = download.storage_binding
    if binding is None:
        if production or not allow_legacy:
            raise RelayPermanentError(
                "Relay artifact download response is missing its storage binding"
            )
        validate_signed_download_url(download.url, allow_local_http=True)
        return None

    parsed = urlsplit(str(download.url))
    try:
        port = parsed.port
    except ValueError as exc:
        raise RelayPermanentError("Relay returned an invalid artifact URL") from exc
    source_host = (parsed.hostname or "").casefold().rstrip(".")
    endpoint_host = binding.endpoint_host
    virtual_host = f"{binding.bucket}.{endpoint_host}"
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or not parsed.query
        or source_host not in {endpoint_host, virtual_host}
    ):
        raise RelayPermanentError(
            "Relay artifact URL does not match its storage endpoint"
        )
    if production and (
        not _public_artifact_hostname(endpoint_host)
        or not endpoint_host.endswith(".myhuaweicloud.com")
    ):
        raise RelayPermanentError(
            "Production artifact storage endpoint is not a public Huawei OBS host"
        )

    encoded_path = parsed.path
    folded_path = encoded_path.casefold()
    if "%2f" in folded_path or "%5c" in folded_path or "%25" in folded_path:
        raise RelayPermanentError("Relay artifact URL path encoding is ambiguous")
    try:
        decoded_path = unquote(encoded_path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RelayPermanentError("Relay artifact URL path is invalid") from exc
    expected_path = (
        f"/{binding.object_key}"
        if source_host == virtual_host
        else f"/{binding.bucket}/{binding.object_key}"
    )
    if decoded_path != expected_path:
        raise RelayPermanentError(
            "Relay artifact URL does not match its bucket and object key"
        )

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Artifact download validation clock requires a UTC offset")
    issued_at = binding.issued_at.astimezone(timezone.utc)
    expires_at = binding.expires_at.astimezone(timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    if (
        issued_at > observed_at + timedelta(seconds=30)
        or issued_at < observed_at - timedelta(minutes=5)
        or expires_at <= observed_at
    ):
        raise RelayPermanentError(
            "Relay artifact signed URL is outside its issuance window"
        )
    return binding


class RelayClient(Protocol):
    def get_model_catalog(
        self,
        *,
        if_none_match: str | None = None,
        request_id: str | None = None,
    ) -> RelayModelCatalogRead: ...

    def get_model_release_evidence(
        self,
        *,
        request_id: str | None = None,
    ) -> RelayModelReleaseEvidence: ...

    def submit(
        self,
        payload: RelayGenerationRequest,
        *,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> RelayAccepted: ...

    def get(
        self, relay_job_id: str, *, request_id: str | None = None
    ) -> RelayJobSnapshot: ...

    def get_artifact_download(
        self,
        relay_job_id: str,
        asset_id: str,
        *,
        request_id: str | None = None,
    ) -> RelaySignedDownload: ...


class RelayOperationsClient(Protocol):
    def list_channels(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: RelayChannelStatus | None = None,
        request_id: str | None = None,
    ) -> RelayChannelPage: ...

    def get_channel(
        self, channel_id: int, *, request_id: str | None = None
    ) -> RelayChannel: ...

    def get_channel_operation(
        self,
        channel_id: int,
        *,
        operation_id: str,
        request_id: str | None = None,
    ) -> RelayChannelOperation: ...

    def test_channel(
        self,
        channel_id: int,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        public_model_id: str,
        route_id: str,
        mode: Literal[
            "text_to_image",
            "image_to_image",
            "text_to_video",
            "image_to_video",
            "video_to_video",
        ] | None = None,
        request_id: str | None = None,
    ) -> RelayChannelOperation: ...

    def reconcile_channel_test_no_creation(
        self,
        channel_id: int,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        confirmed_no_provider_creation: Literal[True],
        request_id: str | None = None,
    ) -> RelayChannelOperation: ...

    def set_channel_status(
        self,
        channel_id: int,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        expected_revision: str,
        target_status: Literal["enabled", "manually_disabled"],
        request_id: str | None = None,
    ) -> RelayChannelOperation: ...

    def list_submission_unknown(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> RelayUnknownSubmissionPage: ...

    def get_submission_unknown(
        self, job_id: str, *, request_id: str | None = None
    ) -> RelayUnknownSubmission: ...

    def list_provider_result_reconciliations(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> RelayProviderResultReconciliationPage: ...

    def get_provider_result_reconciliation(
        self, job_id: str, *, request_id: str | None = None
    ) -> RelayProviderResultReconciliation: ...

    def get_reconciliation_result(
        self,
        job_id: str,
        *,
        operation_id: str,
        request_id: str | None = None,
    ) -> RelayUnknownSubmissionResult: ...

    def resolve_submission_unknown(
        self,
        job_id: str,
        *,
        operation_id: str,
        outcome: Literal["created", "not_created"],
        upstream_task_id: str,
        expected_route_id: int,
        expected_submission_attempt: int,
        expected_reconciliation_token: str,
        verification_reference: str,
        approved_by: str,
        approval_reason: str,
        synchronous_result: RelaySynchronousResultEvidence | None = None,
        request_id: str | None = None,
    ) -> RelayJobSnapshot: ...

    def list_callback_dead_letters(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> RelayCallbackDeliveryPage: ...

    def get_callback_dead_letter(
        self, event_id: str, *, request_id: str | None = None
    ) -> RelayCallbackDelivery: ...

    def get_callback_redrive_result(
        self,
        event_id: str,
        *,
        operation_id: str,
        request_id: str | None = None,
    ) -> RelayCallbackRedriveResult: ...

    def redrive_callback_dead_letter(
        self,
        event_id: str,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        request_id: str | None = None,
    ) -> RelayCallbackRedriveResult: ...


class HttpxRelayClient:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        api_key: str,
        internal_admission_token: str | None = None,
        timeout_seconds: float = 15,
        transport: httpx.BaseTransport | None = None,
        allow_local_http: bool = False,
    ):
        self._client_id = client_id
        self._api_key = api_key
        self._internal_admission_token = internal_admission_token
        self._allow_local_http = allow_local_http
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
        )

    @staticmethod
    def _catalog_etag(value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
            normalized = f'"{normalized}"'
        if not re.fullmatch(r'"sha256:[0-9a-f]{64}"', normalized):
            raise RelayPermanentError("Relay model catalog ETag is invalid")
        return normalized

    @staticmethod
    def _error_detail(response: httpx.Response) -> RelayErrorEnvelopeDetail | None:
        try:
            return RelayErrorEnvelope.model_validate(response.json()).error
        except Exception:
            return None

    @staticmethod
    def _error_message(
        *,
        operation: str,
        response: httpx.Response,
        detail: RelayErrorEnvelopeDetail | None,
    ) -> str:
        if detail is None:
            return f"Relay {operation} failed, HTTP {response.status_code}"
        return (
            f"Relay {operation} failed: {detail.code} "
            f"(request_id={detail.request_id}, HTTP {response.status_code})"
        )

    def get_model_catalog(
        self,
        *,
        if_none_match: str | None = None,
        request_id: str | None = None,
    ) -> RelayModelCatalogRead:
        conditional_etag = (
            self._catalog_etag(if_none_match) if if_none_match is not None else None
        )
        headers = {
            "Accept": "application/json",
            "X-Client-ID": self._client_id,
            "X-API-Key": self._api_key,
            "X-Request-ID": normalize_request_id(
                request_id
                or stable_request_id(
                    "platform-model-catalog", conditional_etag or "initial"
                )
            ),
        }
        if conditional_etag is not None:
            headers["If-None-Match"] = conditional_etag
        try:
            response = self._client.get("/v1/models", headers=headers)
        except httpx.RequestError as exc:
            raise RelayTemporaryError("Relay model catalog request failed") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise RelayTemporaryError(
                "Relay model catalog is temporarily unavailable, "
                f"HTTP {response.status_code}"
            )
        if response.status_code == 304:
            if conditional_etag is None:
                raise RelayPermanentError(
                    "Relay returned 304 without a conditional catalog request"
                )
            response_etag = response.headers.get("etag")
            if response_etag is None:
                raise RelayPermanentError(
                    "Relay model catalog 304 response is missing ETag"
                )
            normalized_etag = self._catalog_etag(response_etag)
            if normalized_etag != conditional_etag:
                raise RelayPermanentError(
                    "Relay model catalog 304 ETag does not match the request"
                )
            return RelayModelCatalogRead(
                catalog=None,
                etag=normalized_etag,
                not_modified=True,
            )
        if response.status_code != 200:
            raise RelayPermanentError(
                f"Relay rejected model catalog request, HTTP {response.status_code}"
            )

        response_etag = response.headers.get("etag")
        if response_etag is None:
            raise RelayPermanentError("Relay model catalog response is missing ETag")
        normalized_etag = self._catalog_etag(response_etag)
        try:
            catalog = RelayModelCatalog.model_validate(response.json())
            catalog.validate_canonical_revisions()
        except Exception as exc:
            raise RelayPermanentError(
                "Relay model catalog response is invalid"
            ) from exc
        expected_etag = f'"{catalog.catalog_revision}"'
        if normalized_etag != expected_etag:
            raise RelayPermanentError(
                "Relay model catalog revision does not match ETag"
            )
        return RelayModelCatalogRead(
            catalog=catalog,
            etag=normalized_etag,
            not_modified=False,
        )

    def get_model_release_evidence(
        self,
        *,
        request_id: str | None = None,
    ) -> RelayModelReleaseEvidence:
        if not self._internal_admission_token:
            raise RelayPermanentError(
                "Relay model release evidence admission is not configured"
            )
        try:
            response = self._client.get(
                "/internal/platform-relay/model-release-evidence",
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    "X-Relay-Internal-Admission": self._internal_admission_token,
                    "X-Request-ID": normalize_request_id(
                        request_id
                        or stable_request_id(
                            "platform-model-release-evidence", "current"
                        )
                    ),
                },
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay model release evidence request failed"
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RelayTemporaryError(
                "Relay model release evidence is temporarily unavailable, "
                f"HTTP {response.status_code}"
            )
        if response.status_code in {401, 403}:
            raise RelayPermanentError(
                "Relay rejected model release evidence admission"
            )
        if response.status_code != 200:
            raise RelayPermanentError(
                "Relay rejected model release evidence request, "
                f"HTTP {response.status_code}"
            )
        try:
            evidence = RelayModelReleaseEvidence.model_validate_json(
                response.content
            )
        except Exception as exc:
            raise RelayPermanentError(
                "Relay model release evidence response is invalid"
            ) from exc
        observed_at = datetime.now(timezone.utc)
        generated_at = evidence.generated_at.astimezone(timezone.utc)
        if (
            generated_at > observed_at + timedelta(seconds=30)
            or observed_at - generated_at > timedelta(minutes=2)
        ):
            raise RelayPermanentError(
                "Relay model release evidence projection is stale"
            )
        return evidence

    def submit(
        self,
        payload: RelayGenerationRequest,
        *,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> RelayAccepted:
        try:
            response = self._client.post(
                "/v1/generations",
                json=payload.model_dump(mode="json", exclude_none=True),
                headers={
                    "X-Client-ID": self._client_id,
                    "X-API-Key": self._api_key,
                    "Idempotency-Key": idempotency_key,
                    "X-Request-ID": normalize_request_id(
                        request_id
                        or stable_request_id(
                            "platform-submit", payload.client_reference_id
                        )
                    ),
                },
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay generation submission network request failed",
                submission_outcome_unknown=True,
            ) from exc
        detail = self._error_detail(response)
        message = self._error_message(
            operation="generation submission",
            response=response,
            detail=detail,
        )
        if response.status_code == 409:
            raise RelayIdempotencyConflictError(
                message,
                relay_error=detail,
                response_status=response.status_code,
            )
        if response.status_code == 429:
            raise RelayTemporaryError(
                message,
                submission_outcome_unknown=False,
                relay_error=detail,
                response_status=response.status_code,
            )
        if response.status_code >= 500:
            raise RelayTemporaryError(
                message,
                submission_outcome_unknown=True,
                relay_error=detail,
                response_status=response.status_code,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RelayPermanentError(
                message,
                relay_error=detail,
                response_status=response.status_code,
            )
        try:
            return RelayAccepted.model_validate(response.json())
        except Exception as exc:
            # A 2xx response proves the POST may have committed. Retrying the
            # exact persisted request and idempotency key is the safe recovery.
            raise RelayTemporaryError(
                "Relay accepted the submission but returned an invalid response",
                submission_outcome_unknown=True,
            ) from exc

    def get(
        self, relay_job_id: str, *, request_id: str | None = None
    ) -> RelayJobSnapshot:
        try:
            response = self._client.get(
                f"/v1/generations/{relay_job_id}",
                headers={
                    "X-Client-ID": self._client_id,
                    "X-API-Key": self._api_key,
                    "X-Request-ID": normalize_request_id(
                        request_id or stable_request_id("platform-status", relay_job_id)
                    ),
                },
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay status query network request failed"
            ) from exc
        detail = self._error_detail(response)
        message = self._error_message(
            operation="status query",
            response=response,
            detail=detail,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RelayTemporaryError(
                message,
                relay_error=detail,
                response_status=response.status_code,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RelayPermanentError(
                message,
                relay_error=detail,
                response_status=response.status_code,
            )
        try:
            return RelayJobSnapshot.model_validate(response.json())
        except Exception as exc:
            raise RelayPermanentError("Relay status response is invalid") from exc

    def get_artifact_download(
        self,
        relay_job_id: str,
        asset_id: str,
        *,
        request_id: str | None = None,
    ) -> RelaySignedDownload:
        try:
            response = self._client.get(
                f"/v1/generations/{relay_job_id}/artifacts/{asset_id}/download",
                headers={
                    "X-Client-ID": self._client_id,
                    "X-API-Key": self._api_key,
                    "X-Request-ID": normalize_request_id(
                        request_id
                        or stable_request_id(
                            "platform-download", f"{relay_job_id}:{asset_id}"
                        )
                    ),
                },
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError("Relay artifact download request failed") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RelayTemporaryError(
                f"Relay artifact download temporarily failed, HTTP {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RelayPermanentError(
                f"Relay rejected artifact download, HTTP {response.status_code}"
            )
        try:
            download = RelaySignedDownload.model_validate(response.json())
            validate_signed_download_url(
                download.url, allow_local_http=self._allow_local_http
            )
            return download
        except Exception as exc:
            if isinstance(exc, RelayPermanentError):
                raise
            raise RelayPermanentError(
                "Relay artifact download response is invalid"
            ) from exc

    def close(self) -> None:
        self._client.close()


class HttpxRelayOperationsClient:
    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        operations_token: str,
        approval_key_id: str,
        approval_secret: str,
        timeout_seconds: float = 15,
        channel_test_timeout_seconds: float = RELAY_CHANNEL_TEST_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._tenant_id = str(UUID(tenant_id))
        self._operations_token = operations_token
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", approval_key_id):
            raise ValueError("approval_key_id is invalid")
        if (
            len(approval_secret.encode("utf-8")) < 32
            or approval_secret != approval_secret.strip()
            or hmac.compare_digest(approval_secret, operations_token)
        ):
            raise ValueError(
                "approval_secret must be an independent secret of at least 32 UTF-8 bytes"
            )
        self._approval_key_id = approval_key_id
        self._approval_secret = approval_secret.encode("utf-8")
        try:
            self._channel_test_timeout_seconds = float(
                channel_test_timeout_seconds
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "channel_test_timeout_seconds must be a positive number"
            ) from exc
        if not (0 < self._channel_test_timeout_seconds <= 3600):
            raise ValueError(
                "channel_test_timeout_seconds must be between 0 and 3600 seconds"
            )
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
        )

    @staticmethod
    def _append_approval_field(payload: bytearray, value: str) -> None:
        encoded = value.encode("utf-8")
        payload.extend(str(len(encoded)).encode("ascii"))
        payload.extend(b":")
        payload.extend(encoded)

    def _approval_signature(
        self,
        *,
        job_id: str,
        operation_id: str,
        outcome: str,
        upstream_task_id: str,
        expected_route_id: int,
        expected_submission_attempt: int,
        expected_reconciliation_token: str,
        verification_reference: str,
        approved_by: str,
        approval_reason: str,
        synchronous_result: RelaySynchronousResultEvidence | None = None,
    ) -> str:
        for field_name, value, max_bytes in (
            ("verification_reference", verification_reference, 191),
            ("approved_by", approved_by, 128),
            ("approval_reason", approval_reason, 240),
        ):
            validate_relay_utf8_byte_limit(
                value,
                field_name=field_name,
                max_bytes=max_bytes,
            )
        payload = bytearray(b"platform-generation-reconciliation-approval-v1\x00")
        for value in (
            self._tenant_id,
            job_id,
            operation_id,
            outcome,
            upstream_task_id,
            str(expected_route_id),
            str(expected_submission_attempt),
            expected_reconciliation_token,
            verification_reference,
            approved_by,
            approval_reason,
            self._approval_key_id,
        ):
            self._append_approval_field(payload, value)
        if synchronous_result is not None:
            for value in (
                "synchronous-result-v1",
                synchronous_result.provider_model_id,
                synchronous_result.provider_response_sha256,
                synchronous_result.artifact_url,
                synchronous_result.provider_created_at,
                str(synchronous_result.generated_images),
                str(synchronous_result.output_tokens),
                str(synchronous_result.total_tokens),
            ):
                self._append_approval_field(payload, value)
        digest = hmac.new(
            self._approval_secret,
            bytes(payload),
            hashlib.sha256,
        ).hexdigest()
        return f"hmac-sha256:{digest}"

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Relay-Operations-Token": self._operations_token,
            "X-Request-ID": normalize_request_id(request_id),
        }

    @staticmethod
    def _canonical_channel_id(channel_id: int) -> int:
        if isinstance(channel_id, bool) or not isinstance(channel_id, int) or channel_id <= 0:
            raise ValueError("channel_id must be a positive integer")
        return channel_id

    @staticmethod
    def _channel_actor_reason(*, actor: str, reason: str) -> tuple[str, str]:
        if actor != actor.strip() or not (1 <= len(actor) <= 128):
            raise ValueError("channel operation actor is invalid")
        if reason != reason.strip() or not (3 <= len(reason) <= 240):
            raise ValueError("channel operation reason is invalid")
        return actor, reason

    @staticmethod
    def _raise_for_channel_write_response(
        response: httpx.Response, operation: str
    ) -> None:
        try:
            HttpxRelayOperationsClient._raise_for_response(response, operation)
        except RelayTemporaryError as exc:
            raise RelayTemporaryError(
                str(exc),
                submission_outcome_unknown=True,
                relay_error=exc.relay_error,
                response_status=exc.response_status,
            ) from exc

    def _validate_channel_operation(
        self,
        payload: Any,
        *,
        channel_id: int,
        operation_id: str,
        kind: RelayChannelOperationKind | None = None,
        actor: str | None = None,
        reason: str | None = None,
        expected_revision: str | None = None,
        target_status: Literal["enabled", "manually_disabled"] | None = None,
        write_response: bool = False,
        require_route_test_state: bool = False,
    ) -> RelayChannelOperation:
        try:
            result = RelayChannelOperation.model_validate(payload)
            if (
                str(result.tenant_id) != self._tenant_id
                or result.channel_id != channel_id
                or result.operation_id != operation_id
                or (kind is not None and result.kind != kind)
                or (actor is not None and result.actor != actor)
                or (reason is not None and result.reason != reason)
                or (
                    expected_revision is not None
                    and result.expected_revision != expected_revision
                )
                or (
                    target_status is not None
                    and result.target_status != target_status
                )
                or (
                    require_route_test_state
                    and (
                        result.kind != "test"
                        or result.provider_submission_state is None
                    )
                )
            ):
                raise ValueError("Relay channel operation identity is invalid")
            return result
        except Exception as exc:
            error_type = RelayTemporaryError if write_response else RelayPermanentError
            if error_type is RelayTemporaryError:
                raise RelayTemporaryError(
                    "Relay accepted the channel operation but returned an invalid response",
                    submission_outcome_unknown=True,
                ) from exc
            raise RelayPermanentError(
                "Relay channel operation response is invalid"
            ) from exc

    def list_channels(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: RelayChannelStatus | None = None,
        request_id: str | None = None,
    ) -> RelayChannelPage:
        params: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "page": page,
            "page_size": page_size,
        }
        if status is not None:
            params["status"] = status
        try:
            response = self._client.get(
                "/internal/platform-generation-operations/channels",
                params=params,
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-channel-list",
                        f"{self._tenant_id}:{page}:{page_size}:{status or 'all'}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay channel list request failed", submission_outcome_unknown=False
            ) from exc
        self._raise_for_response(response, "channel list")
        try:
            return RelayChannelPage.model_validate(response.json())
        except Exception as exc:
            raise RelayPermanentError("Relay channel list response is invalid") from exc

    def get_channel(
        self, channel_id: int, *, request_id: str | None = None
    ) -> RelayChannel:
        canonical_channel_id = self._canonical_channel_id(channel_id)
        try:
            response = self._client.get(
                f"/internal/platform-generation-operations/channels/{canonical_channel_id}",
                params={"tenant_id": self._tenant_id},
                headers=self._headers(
                    request_id
                    or stable_request_id("relay-channel-detail", str(canonical_channel_id))
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay channel detail request failed", submission_outcome_unknown=False
            ) from exc
        self._raise_for_response(response, "channel detail")
        try:
            result = RelayChannel.model_validate(response.json())
            if result.id != canonical_channel_id:
                raise ValueError("Relay channel identity is invalid")
            return result
        except Exception as exc:
            raise RelayPermanentError("Relay channel detail response is invalid") from exc

    def get_channel_operation(
        self,
        channel_id: int,
        *,
        operation_id: str,
        request_id: str | None = None,
    ) -> RelayChannelOperation:
        canonical_channel_id = self._canonical_channel_id(channel_id)
        canonical_operation_id = self._canonical_operation_id(operation_id)
        try:
            response = self._client.get(
                "/internal/platform-generation-operations/channels/"
                f"{canonical_channel_id}/operations/{canonical_operation_id}",
                params={"tenant_id": self._tenant_id},
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-channel-operation-read",
                        f"{canonical_channel_id}:{canonical_operation_id}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay channel operation read failed", submission_outcome_unknown=False
            ) from exc
        self._raise_for_response(response, "channel operation read")
        try:
            payload = response.json()
        except Exception as exc:
            raise RelayPermanentError(
                "Relay channel operation response is invalid"
            ) from exc
        return self._validate_channel_operation(
            payload,
            channel_id=canonical_channel_id,
            operation_id=canonical_operation_id,
        )

    def test_channel(
        self,
        channel_id: int,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        public_model_id: str,
        route_id: str,
        mode: Literal[
            "text_to_image",
            "image_to_image",
            "text_to_video",
            "image_to_video",
            "video_to_video",
        ] | None = None,
        request_id: str | None = None,
    ) -> RelayChannelOperation:
        canonical_channel_id = self._canonical_channel_id(channel_id)
        canonical_operation_id = self._canonical_operation_id(operation_id)
        actor, reason = self._channel_actor_reason(actor=actor, reason=reason)
        body: dict[str, Any] = {
            "operation_id": canonical_operation_id,
            "tenant_id": self._tenant_id,
            "actor": actor,
            "reason": reason,
        }
        identity_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
        if not identity_pattern.fullmatch(
            public_model_id
        ) or not identity_pattern.fullmatch(route_id):
            raise RelayPermanentError("Relay exact route test identity is invalid")
        body.update(
            public_model_id=public_model_id,
            route_id=route_id,
        )
        if mode is not None:
            body["mode"] = mode
        try:
            response = self._client.post(
                f"/internal/platform-generation-operations/channels/{canonical_channel_id}/test",
                json=body,
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-channel-test",
                        f"{canonical_channel_id}:{canonical_operation_id}",
                    )
                ),
                timeout=self._channel_test_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay channel test outcome is unknown",
                submission_outcome_unknown=True,
            ) from exc
        self._raise_for_channel_write_response(response, "channel test")
        try:
            payload = response.json()
        except Exception as exc:
            raise RelayTemporaryError(
                "Relay accepted the channel test but returned an invalid response",
                submission_outcome_unknown=True,
            ) from exc
        return self._validate_channel_operation(
            payload,
            channel_id=canonical_channel_id,
            operation_id=canonical_operation_id,
            kind="test",
            actor=actor,
            reason=reason,
            write_response=True,
            require_route_test_state=True,
        )

    def reconcile_channel_test_no_creation(
        self,
        channel_id: int,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        confirmed_no_provider_creation: Literal[True],
        request_id: str | None = None,
    ) -> RelayChannelOperation:
        canonical_channel_id = self._canonical_channel_id(channel_id)
        canonical_operation_id = self._canonical_operation_id(operation_id)
        actor, reason = self._channel_actor_reason(actor=actor, reason=reason)
        actor = validate_relay_channel_reconciliation_actor(actor)
        reason = validate_relay_channel_reconciliation_reason(reason)
        if confirmed_no_provider_creation is not True:
            raise ValueError("confirmed_no_provider_creation must be true")
        try:
            response = self._client.post(
                "/internal/platform-generation-operations/channels/"
                f"{canonical_channel_id}/operations/{canonical_operation_id}/"
                "reconcile-no-creation",
                json={
                    "tenant_id": self._tenant_id,
                    "actor": actor,
                    "reason": reason,
                    "confirmed_no_provider_creation": True,
                },
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-channel-test-reconcile-no-creation",
                        f"{canonical_channel_id}:{canonical_operation_id}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay channel test reconciliation outcome is unknown",
                submission_outcome_unknown=True,
            ) from exc
        self._raise_for_channel_write_response(
            response, "channel test no-creation reconciliation"
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise RelayTemporaryError(
                "Relay accepted the channel test reconciliation but returned an invalid response",
                submission_outcome_unknown=True,
            ) from exc
        result = self._validate_channel_operation(
            payload,
            channel_id=canonical_channel_id,
            operation_id=canonical_operation_id,
            kind="test",
            write_response=True,
            require_route_test_state=True,
        )
        if (
            result.api_version != "v1"
            or result.state != "failed"
            or not isinstance(result.result, RelayChannelTestResult)
            or result.result.success
            or result.result.response_time_ms != 0
            or result.result.error_code
            != "CHANNEL_TEST_RECONCILED_NO_CREATION"
            or result.provider_submission_state != "reconciled_no_creation"
            or result.provider_blocker_code is not None
            or result.reconciliation_actor != actor
            or result.reconciliation_reason != reason
            or result.reconciled_at is None
            or result.reconciled_at != result.completed_at
            or result.previous_revision is not None
            or result.result_revision is not None
            or result.idempotent_replay
        ):
            raise RelayTemporaryError(
                "Relay accepted the channel test reconciliation but returned an invalid response",
                submission_outcome_unknown=True,
            )
        return result

    def set_channel_status(
        self,
        channel_id: int,
        *,
        operation_id: str,
        actor: str,
        reason: str,
        expected_revision: str,
        target_status: Literal["enabled", "manually_disabled"],
        request_id: str | None = None,
    ) -> RelayChannelOperation:
        canonical_channel_id = self._canonical_channel_id(channel_id)
        canonical_operation_id = self._canonical_operation_id(operation_id)
        actor, reason = self._channel_actor_reason(actor=actor, reason=reason)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_revision):
            raise ValueError("expected_revision is invalid")
        if target_status not in {"enabled", "manually_disabled"}:
            raise ValueError("target_status is invalid")
        try:
            response = self._client.post(
                f"/internal/platform-generation-operations/channels/{canonical_channel_id}/status",
                json={
                    "operation_id": canonical_operation_id,
                    "tenant_id": self._tenant_id,
                    "actor": actor,
                    "reason": reason,
                    "expected_revision": expected_revision,
                    "target_status": target_status,
                },
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-channel-status",
                        f"{canonical_channel_id}:{canonical_operation_id}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay channel status outcome is unknown",
                submission_outcome_unknown=True,
            ) from exc
        self._raise_for_channel_write_response(response, "channel status")
        try:
            payload = response.json()
        except Exception as exc:
            raise RelayTemporaryError(
                "Relay accepted the channel status operation but returned an invalid response",
                submission_outcome_unknown=True,
            ) from exc
        return self._validate_channel_operation(
            payload,
            channel_id=canonical_channel_id,
            operation_id=canonical_operation_id,
            kind="status",
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
            target_status=target_status,
            write_response=True,
        )

    @staticmethod
    def _raise_for_response(response: httpx.Response, operation: str) -> None:
        if 200 <= response.status_code < 300:
            return
        detail = HttpxRelayClient._error_detail(response)
        message = HttpxRelayClient._error_message(
            operation=operation,
            response=response,
            detail=detail,
        )
        error_type = (
            RelayTemporaryError
            if response.status_code == 429 or response.status_code >= 500
            else RelayPermanentError
        )
        if error_type is RelayTemporaryError:
            raise RelayTemporaryError(
                message,
                submission_outcome_unknown=False,
                relay_error=detail,
                response_status=response.status_code,
            )
        raise RelayPermanentError(
            message,
            relay_error=detail,
            response_status=response.status_code,
        )

    def list_submission_unknown(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> RelayUnknownSubmissionPage:
        try:
            response = self._client.get(
                "/internal/platform-generation-operations/submission-unknown",
                params={
                    "tenant_id": self._tenant_id,
                    "page": page,
                    "page_size": page_size,
                },
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-unknown-list", f"{self._tenant_id}:{page}:{page_size}"
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay unknown-submission list request failed",
                submission_outcome_unknown=False,
            ) from exc
        self._raise_for_response(response, "unknown-submission list")
        try:
            return RelayUnknownSubmissionPage.model_validate(response.json())
        except Exception as exc:
            raise RelayPermanentError(
                "Relay unknown-submission list response is invalid"
            ) from exc

    def get_submission_unknown(
        self, job_id: str, *, request_id: str | None = None
    ) -> RelayUnknownSubmission:
        canonical_job_id = str(UUID(job_id))
        try:
            response = self._client.get(
                f"/internal/platform-generation-operations/{canonical_job_id}/reconciliation",
                params={"tenant_id": self._tenant_id},
                headers=self._headers(
                    request_id
                    or stable_request_id("relay-unknown-detail", canonical_job_id)
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay unknown-submission detail request failed",
                submission_outcome_unknown=False,
            ) from exc
        self._raise_for_response(response, "unknown-submission detail")
        try:
            return RelayUnknownSubmission.model_validate(response.json())
        except Exception as exc:
            raise RelayPermanentError(
                "Relay unknown-submission detail response is invalid"
            ) from exc

    def list_provider_result_reconciliations(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> RelayProviderResultReconciliationPage:
        try:
            response = self._client.get(
                "/internal/platform-generation-operations/provider-result-reconciliation",
                params={
                    "tenant_id": self._tenant_id,
                    "page": page,
                    "page_size": page_size,
                },
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-provider-result-reconciliation-list",
                        f"{self._tenant_id}:{page}:{page_size}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay provider-result reconciliation list request failed",
                submission_outcome_unknown=False,
            ) from exc
        self._raise_for_response(response, "provider-result reconciliation list")
        try:
            result = RelayProviderResultReconciliationPage.model_validate(
                response.json()
            )
            if any(str(item.tenant_id) != self._tenant_id for item in result.data):
                raise ValueError(
                    "Relay provider-result reconciliation tenant is invalid"
                )
            return result
        except Exception as exc:
            raise RelayPermanentError(
                "Relay provider-result reconciliation list response is invalid"
            ) from exc

    def get_provider_result_reconciliation(
        self, job_id: str, *, request_id: str | None = None
    ) -> RelayProviderResultReconciliation:
        canonical_job_id = str(UUID(job_id))
        try:
            response = self._client.get(
                f"/internal/platform-generation-operations/{canonical_job_id}/provider-result-reconciliation",
                params={"tenant_id": self._tenant_id},
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-provider-result-reconciliation-detail",
                        canonical_job_id,
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay provider-result reconciliation detail request failed",
                submission_outcome_unknown=False,
            ) from exc
        self._raise_for_response(response, "provider-result reconciliation detail")
        try:
            result = RelayProviderResultReconciliation.model_validate(response.json())
            if (
                str(result.job_id) != canonical_job_id
                or str(result.tenant_id) != self._tenant_id
            ):
                raise ValueError(
                    "Relay provider-result reconciliation identity is invalid"
                )
            return result
        except Exception as exc:
            raise RelayPermanentError(
                "Relay provider-result reconciliation detail response is invalid"
            ) from exc

    def get_reconciliation_result(
        self,
        job_id: str,
        *,
        operation_id: str,
        request_id: str | None = None,
    ) -> RelayUnknownSubmissionResult:
        canonical_job_id = str(UUID(job_id))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", operation_id):
            raise ValueError("operation_id is invalid")
        try:
            response = self._client.get(
                f"/internal/platform-generation-operations/{canonical_job_id}/reconciliation-result",
                params={
                    "tenant_id": self._tenant_id,
                    "operation_id": operation_id,
                },
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-unknown-result",
                        f"{canonical_job_id}:{operation_id}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay reconciliation-result request failed",
                submission_outcome_unknown=False,
            ) from exc
        self._raise_for_response(response, "reconciliation result")
        try:
            result = RelayUnknownSubmissionResult.model_validate(response.json())
            if (
                str(result.job_id) != canonical_job_id
                or str(result.tenant_id) != self._tenant_id
                or result.operation_id != operation_id
            ):
                raise ValueError("Relay reconciliation-result identity is invalid")
            return result
        except Exception as exc:
            raise RelayPermanentError(
                "Relay reconciliation-result response is invalid"
            ) from exc

    def resolve_submission_unknown(
        self,
        job_id: str,
        *,
        operation_id: str,
        outcome: Literal["created", "not_created"],
        upstream_task_id: str,
        expected_route_id: int,
        expected_submission_attempt: int,
        expected_reconciliation_token: str,
        verification_reference: str,
        approved_by: str,
        approval_reason: str,
        synchronous_result: RelaySynchronousResultEvidence | None = None,
        request_id: str | None = None,
    ) -> RelayJobSnapshot:
        canonical_job_id = str(UUID(job_id))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", operation_id):
            raise ValueError("operation_id is invalid")
        if synchronous_result is not None:
            if outcome != "created":
                raise ValueError("synchronous_result requires a created outcome")
            if upstream_task_id != (
                "seedream:" + synchronous_result.provider_response_sha256
            ):
                raise ValueError(
                    "upstream_task_id must bind the synchronous provider response"
                )
        approval_signature = self._approval_signature(
            job_id=canonical_job_id,
            operation_id=operation_id,
            outcome=outcome,
            upstream_task_id=upstream_task_id,
            expected_route_id=expected_route_id,
            expected_submission_attempt=expected_submission_attempt,
            expected_reconciliation_token=expected_reconciliation_token,
            verification_reference=verification_reference,
            approved_by=approved_by,
            approval_reason=approval_reason,
            synchronous_result=synchronous_result,
        )
        payload = {
            "operation_id": operation_id,
            "tenant_id": self._tenant_id,
            "outcome": outcome,
            "upstream_task_id": upstream_task_id,
            "expected_route_id": expected_route_id,
            "expected_submission_attempt": expected_submission_attempt,
            "expected_reconciliation_token": expected_reconciliation_token,
            "verification_reference": verification_reference,
            "approved_by": approved_by,
            "approval_reason": approval_reason,
            "approval_key_id": self._approval_key_id,
            "approval_signature": approval_signature,
        }
        if synchronous_result is not None:
            payload["synchronous_result"] = synchronous_result.model_dump(mode="json")
        try:
            response = self._client.post(
                f"/internal/platform-generation-operations/{canonical_job_id}/reconciliation",
                json=payload,
                headers=self._headers(
                    request_id
                    or stable_request_id(
                        "relay-unknown-resolve",
                        f"{canonical_job_id}:{operation_id}",
                    )
                ),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError(
                "Relay unknown-submission resolve outcome is unknown",
                submission_outcome_unknown=True,
            ) from exc
        self._raise_for_response(response, "unknown-submission resolve")
        try:
            return RelayJobSnapshot.model_validate(response.json())
        except Exception as exc:
            raise RelayTemporaryError(
                "Relay resolved the unknown submission but returned an invalid response",
                submission_outcome_unknown=True,
            ) from exc

    @staticmethod
    def _canonical_callback_event_id(event_id: str) -> str:
        canonical = str(UUID(event_id))
        if canonical != event_id:
            raise ValueError("callback event_id must use canonical UUID form")
        return canonical

    @staticmethod
    def _canonical_operation_id(operation_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", operation_id):
            raise ValueError("operation_id is invalid")
        return operation_id

    def list_callback_dead_letters(self, *, page: int = 1, page_size: int = 50, request_id: str | None = None) -> RelayCallbackDeliveryPage:
        try:
            response = self._client.get(
                "/internal/platform-generation-operations/callback-deliveries",
                params={"tenant_id": self._tenant_id, "state": "dead_letter", "page": page, "page_size": page_size},
                headers=self._headers(request_id or stable_request_id("relay-callback-dlq-list", f"{self._tenant_id}:{page}:{page_size}")),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError("Relay callback dead-letter list request failed", submission_outcome_unknown=False) from exc
        self._raise_for_response(response, "callback dead-letter list")
        try:
            result = RelayCallbackDeliveryPage.model_validate(response.json())
            if any(str(item.tenant_id) != self._tenant_id for item in result.data):
                raise ValueError("Relay callback dead-letter tenant identity is invalid")
            return result
        except Exception as exc:
            raise RelayPermanentError("Relay callback dead-letter list response is invalid") from exc

    def get_callback_dead_letter(self, event_id: str, *, request_id: str | None = None) -> RelayCallbackDelivery:
        canonical_event_id = self._canonical_callback_event_id(event_id)
        try:
            response = self._client.get(
                f"/internal/platform-generation-operations/callback-deliveries/{canonical_event_id}",
                params={"tenant_id": self._tenant_id},
                headers=self._headers(request_id or stable_request_id("relay-callback-dlq-detail", canonical_event_id)),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError("Relay callback dead-letter detail request failed", submission_outcome_unknown=False) from exc
        self._raise_for_response(response, "callback dead-letter detail")
        try:
            result = RelayCallbackDelivery.model_validate(response.json())
            if str(result.event_id) != canonical_event_id or str(result.tenant_id) != self._tenant_id:
                raise ValueError("Relay callback dead-letter identity is invalid")
            return result
        except Exception as exc:
            raise RelayPermanentError("Relay callback dead-letter detail response is invalid") from exc

    def get_callback_redrive_result(self, event_id: str, *, operation_id: str, request_id: str | None = None) -> RelayCallbackRedriveResult:
        canonical_event_id = self._canonical_callback_event_id(event_id)
        canonical_operation_id = self._canonical_operation_id(operation_id)
        try:
            response = self._client.get(
                f"/internal/platform-generation-operations/callback-deliveries/{canonical_event_id}/redrive-result",
                params={"tenant_id": self._tenant_id, "operation_id": canonical_operation_id},
                headers=self._headers(request_id or stable_request_id("relay-callback-redrive-result", f"{canonical_event_id}:{canonical_operation_id}")),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError("Relay callback redrive-result request failed", submission_outcome_unknown=False) from exc
        self._raise_for_response(response, "callback redrive result")
        try:
            result = RelayCallbackRedriveResult.model_validate(response.json())
            if (str(result.delivery_event_id) != canonical_event_id or str(result.tenant_id) != self._tenant_id or result.evidence.operation_id != canonical_operation_id):
                raise ValueError("Relay callback redrive-result identity is invalid")
            return result
        except Exception as exc:
            raise RelayPermanentError("Relay callback redrive-result response is invalid") from exc

    def redrive_callback_dead_letter(self, event_id: str, *, operation_id: str, actor: str, reason: str, request_id: str | None = None) -> RelayCallbackRedriveResult:
        canonical_event_id = self._canonical_callback_event_id(event_id)
        canonical_operation_id = self._canonical_operation_id(operation_id)
        if actor != actor.strip() or not (1 <= len(actor) <= 128):
            raise ValueError("callback redrive actor is invalid")
        if reason != reason.strip() or not (3 <= len(reason) <= 240):
            raise ValueError("callback redrive reason is invalid")
        try:
            response = self._client.post(
                f"/internal/platform-generation-operations/callback-deliveries/{canonical_event_id}/redrive",
                json={"operation_id": canonical_operation_id, "tenant_id": self._tenant_id, "actor": actor, "reason": reason},
                headers=self._headers(request_id or stable_request_id("relay-callback-redrive", f"{canonical_event_id}:{canonical_operation_id}")),
            )
        except httpx.RequestError as exc:
            raise RelayTemporaryError("Relay callback redrive outcome is unknown", submission_outcome_unknown=True) from exc
        self._raise_for_response(response, "callback redrive")
        try:
            result = RelayCallbackRedriveResult.model_validate(response.json())
            if (str(result.delivery_event_id) != canonical_event_id or str(result.tenant_id) != self._tenant_id or result.evidence.operation_id != canonical_operation_id or result.evidence.actor != actor or result.evidence.reason != reason):
                raise ValueError("Relay callback redrive response identity is invalid")
            return result
        except Exception as exc:
            raise RelayTemporaryError("Relay redrove the callback but returned an invalid response", submission_outcome_unknown=True) from exc

    def close(self) -> None:
        self._client.close()
