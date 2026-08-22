from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from .models import (
    AuditOutcome,
    ChannelCostSource,
    ChannelType,
    CompanyStatus,
    DownloadCompletionSource,
    InputAssetStatus,
    LedgerKind,
    MembershipStatus,
    PermissionEffect,
    PublicationAttemptStatus,
    PublicationJobStatus,
    PublisherConnectionStatus,
    ResourceKind,
    TaskStatus,
)
from .relay_client import RelayArtifact, RelayErrorDetail, RelayReservationAction


MAX_MONEY_CENTS = 9_000_000_000_000_000
MAX_CALL_QUOTA = 9_223_372_036_854_775_807
MAX_CONCURRENCY_LIMIT = 2_147_483_647


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BootstrapRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=160)
    owner_email: EmailStr
    owner_display_name: str = Field(min_length=1, max_length=120)


class BootstrapResponse(BaseModel):
    company_id: str
    user_id: str
    membership_id: str


class CreateMemberRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    primary_role: Literal["operator", "team_lead"] = "operator"


class MemberRoleResponse(BaseModel):
    id: str
    name: str
    is_system: bool
    system_key: str | None = None


class MemberPermissionOverrideSummary(BaseModel):
    permission_code: str
    effect: PermissionEffect


class MemberResponse(BaseModel):
    user_id: str
    membership_id: str
    email: EmailStr
    display_name: str
    status: MembershipStatus
    roles: list[MemberRoleResponse] = Field(default_factory=list)
    inherited_permission_codes: list[str] = Field(default_factory=list)
    effective_permission_codes: list[str] = Field(default_factory=list)
    permission_overrides: list[MemberPermissionOverrideSummary] = Field(
        default_factory=list
    )


class MemberStatusRequest(BaseModel):
    status: MembershipStatus


class CreateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    permission_codes: set[str] = Field(default_factory=set)


class UpdateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    permission_codes: set[str] = Field(default_factory=set)


class RoleResponse(ApiModel):
    id: str
    company_id: str
    name: str
    description: str
    is_system: bool
    system_key: str | None = None
    permission_codes: set[str] = Field(default_factory=set)


class AssignRoleRequest(BaseModel):
    membership_id: str


class ReplaceMemberRolesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: set[str]
    expected_role_ids: set[str]


class CompanyMeResponse(BaseModel):
    company_id: str
    user_id: str
    membership_id: str
    email: EmailStr
    display_name: str
    is_platform_admin: bool = False
    status: MembershipStatus
    permission_codes: list[str]
    roles: list[RoleResponse]


class PermissionOverrideRequest(BaseModel):
    permission_code: str = Field(min_length=1, max_length=80)
    effect: PermissionEffect


class PermissionOverrideResponse(ApiModel):
    id: str
    membership_id: str
    permission_code: str
    effect: PermissionEffect


class PermissionCatalogResponse(BaseModel):
    code: str
    description: str


class MemberPermissionDetailItem(BaseModel):
    code: str
    description: str
    inherited: bool
    override_effect: PermissionEffect | None = None
    effective: bool


class MemberPermissionDetailResponse(BaseModel):
    membership_id: str
    items: list[MemberPermissionDetailItem] = Field(default_factory=list)


class ReplacePermissionOverridesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, PermissionEffect]
    expected_overrides: dict[str, PermissionEffect]


class ReplaceMemberAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: set[str]
    permission_overrides: dict[str, PermissionEffect]
    expected_role_ids: set[str]
    expected_permission_overrides: dict[str, PermissionEffect]


class EntitlementPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_quota: int | None = Field(default=None, gt=0, le=MAX_CALL_QUOTA)
    concurrency_limit: int | None = Field(
        default=None, gt=0, le=MAX_CONCURRENCY_LIMIT
    )
    effective_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_entitlement_schedule(self) -> "EntitlementPolicyRequest":
        for field_name, value in (
            ("effective_at", self.effective_at),
            ("expires_at", self.expires_at),
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must include a UTC offset")
        if (
            self.effective_at is not None
            and self.expires_at is not None
            and self.effective_at >= self.expires_at
        ):
            raise ValueError("effective_at must be before expires_at")
        return self


class CompanyModelGrantRequest(EntitlementPolicyRequest):

    model_id: str
    enabled: bool = True
    price_per_second_cents: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_CENTS
    )
    price_per_item_cents: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_CENTS
    )
    config_override: dict[str, Any] = Field(default_factory=dict)


class CompanyModelGrantResponse(ApiModel):
    id: str
    company_id: str
    model_id: str
    enabled: bool
    price_per_second_cents: int | None
    price_per_item_cents: int | None
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None


class RechargeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(gt=0, le=MAX_MONEY_CENTS)
    idempotency_key: str = Field(min_length=8, max_length=120)
    note: str = Field(default="", max_length=240)


class WalletResponse(ApiModel):
    company_id: str
    available_cents: int
    reserved_cents: int


class LedgerEntryResponse(ApiModel):
    id: str
    company_id: str
    kind: LedgerKind
    amount_cents: int
    available_delta_cents: int
    reserved_delta_cents: int
    idempotency_key: str
    task_id: str | None
    note: str
    created_at: datetime


class RechargeRecordPage(BaseModel):
    page: int
    page_size: int
    total: int
    total_amount_cents: int
    items: list[LedgerEntryResponse]


class WalletOperationResponse(BaseModel):
    wallet: WalletResponse
    ledger_entry: LedgerEntryResponse


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    expected_capability_version: int | None = Field(default=None, ge=1)
    expected_quote_revision: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    idempotency_key: str = Field(min_length=8, max_length=120)
    request_payload: dict[str, Any] = Field(default_factory=dict)


class TaskArtifactResponse(BaseModel):
    artifact_id: str | None = None
    asset_id: str
    media_type: str
    content_type: str
    size_bytes: int
    sha256: str


class CreateDevPublisherConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock"]
    display_name: str = Field(min_length=1, max_length=120)


class PublisherConnectionResponse(ApiModel):
    id: str
    company_id: str
    created_by_user_id: str
    provider: str
    display_name: str
    external_account_id: str
    status: PublisherConnectionStatus
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublisherOAuthProviderResponse(BaseModel):
    provider: str
    display_name: str


class StartPublisherOAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_-]{0,39}$",
    )


class StartPublisherOAuthResponse(BaseModel):
    provider: str
    authorization_url: str
    expires_at: datetime


class CreatePublicationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=36)
    connection_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(min_length=8, max_length=120)
    title: str = Field(default="", max_length=160)
    caption: str = Field(default="", max_length=5000)
    scheduled_at: datetime | None = None
    timezone: str = Field(
        default="Asia/Shanghai",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_+./-]+$",
    )

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("scheduled_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_timezone_offset(self) -> "CreatePublicationJobRequest":
        try:
            requested_zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if self.scheduled_at is None:
            return self
        expected_offset = self.scheduled_at.astimezone(
            requested_zone
        ).utcoffset()
        if self.scheduled_at.utcoffset() != expected_offset:
            raise ValueError(
                "scheduled_at UTC offset does not match timezone at that instant"
            )
        return self


class ReconcilePublicationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["published", "failed"]
    external_post_id: str | None = Field(default=None, max_length=200)
    external_post_url: HttpUrl | None = None
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_.-]{0,119}$",
    )
    error_message: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_outcome_fields(self) -> "ReconcilePublicationJobRequest":
        normalized_post_id = (
            self.external_post_id.strip()
            if self.external_post_id is not None
            else None
        )
        normalized_error_message = (
            self.error_message.strip()
            if self.error_message is not None
            else None
        )
        if self.error_message is not None and not normalized_error_message:
            raise ValueError("error_message must not be blank")
        if self.outcome == "published" and not normalized_post_id:
            raise ValueError(
                "external_post_id is required when outcome=published"
            )
        if self.outcome == "published" and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError(
                "published reconciliation cannot include failure fields"
            )
        if (
            self.external_post_url is not None
            and self.external_post_url.scheme != "https"
        ):
            raise ValueError("external_post_url must use https")
        if self.outcome == "failed" and (
            normalized_post_id is not None or self.external_post_url is not None
        ):
            raise ValueError(
                "failed reconciliation cannot include external publication fields"
            )
        self.external_post_id = normalized_post_id
        self.error_message = normalized_error_message
        return self


class PublicationAttemptResponse(ApiModel):
    id: str
    company_id: str
    job_id: str
    attempt_number: int
    status: PublicationAttemptStatus
    provider_request_id: str | None
    external_post_id: str | None
    external_post_url: str | None
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime


class PublicationJobResponse(ApiModel):
    id: str
    company_id: str
    created_by_user_id: str
    task_artifact_id: str
    connection_id: str
    status: PublicationJobStatus
    title: str
    caption: str
    scheduled_at: datetime | None
    timezone: str
    approved_by_user_id: str | None
    approved_at: datetime | None
    cancelled_by_user_id: str | None
    cancelled_at: datetime | None
    published_at: datetime | None
    external_post_id: str | None
    external_post_url: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    next_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicationJobPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[PublicationJobResponse]


class PublicationJobDetailResponse(PublicationJobResponse):
    attempts: list[PublicationAttemptResponse] = Field(default_factory=list)


class InputAssetResponse(ApiModel):
    id: str
    company_id: str
    uploaded_by_user_id: str
    original_filename: str
    media_type: Literal["image", "video", "audio"]
    content_type: str
    size_bytes: int
    sha256: str
    status: InputAssetStatus
    created_at: datetime
    updated_at: datetime


class PromotedInputAssetResponse(InputAssetResponse):
    # This provenance is returned only by the promotion operation, whose
    # caller has already passed source-task visibility checks. Generic company
    # asset reads must not reveal another user's task-artifact identifier.
    source_task_artifact_id: str


class PromoteTaskArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=120)


class InputAssetAccessResponse(BaseModel):
    url: HttpUrl
    expires_seconds: int = Field(ge=1, le=3600)


class RelayErrorSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Z][A-Z0-9_]{0,159}$",
    )
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool
    details: dict[str, Any]
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    http_status: int | None = Field(default=None, ge=400, le=599)
    source: Literal["submit", "poll", "callback"] | None = None


class TaskResponse(ApiModel):
    id: str
    company_id: str
    user_id: str
    model_id: str
    status: TaskStatus
    request_payload: dict[str, Any]
    quote_cents: int
    pricing_snapshot: dict[str, Any]
    capability_snapshot: dict[str, Any]
    reserved_cents: int
    actual_cost_cents: int | None
    relay_job_id: str | None
    output_artifacts: list[TaskArtifactResponse]
    failure_reason: str | None
    relay_error_snapshot: RelayErrorSnapshotResponse | None
    created_at: datetime
    updated_at: datetime


class SettleTaskRequest(BaseModel):
    actual_cost_cents: int = Field(ge=0, le=MAX_MONEY_CENTS)
    idempotency_key: str = Field(min_length=8, max_length=120)


class FailTaskRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    failure_reason: str = Field(min_length=1, max_length=1000)


class DevCapabilityRequest(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    config: dict[str, Any] = Field(default_factory=dict)


class DevModelSeedRequest(BaseModel):
    slug: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$",
    )
    display_name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(min_length=1, max_length=80)
    billing_mode: Literal["per_second", "per_item"] = "per_second"
    capability_version: int = Field(default=1, ge=1)
    capabilities: list[DevCapabilityRequest] = Field(default_factory=list)


class DevModelSeedResponse(ApiModel):
    id: str
    slug: str
    display_name: str
    provider_key: str
    billing_mode: Literal["per_second", "per_item"]
    capability_version: int
    active: bool


class AdminModelCapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$",
    )
    config: dict[str, Any] = Field(default_factory=dict)


class AdminModelCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$",
    )
    display_name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$",
    )
    billing_mode: Literal["per_second", "per_item"] = "per_second"
    capabilities: list[AdminModelCapabilityRequest] = Field(default_factory=list)


class AdminModelUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$",
    )
    billing_mode: Literal["per_second", "per_item"] | None = None
    expected_capability_version: int = Field(ge=1)
    capabilities: list[AdminModelCapabilityRequest] = Field(default_factory=list)


class AdminModelResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    provider_key: str
    billing_mode: Literal["per_second", "per_item"]
    capability_version: int
    relay_capability_revision: str | None = None
    relay_capability_synced_at: datetime | None = None
    active: bool
    status: Literal["draft", "published", "disabled"]
    capabilities: dict[str, dict[str, Any]]
    effective_capabilities: dict[str, Any]
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InternalDispatchResponse(BaseModel):
    processed: bool
    outbox_id: str | None = None
    status: str | None = None
    relay_job_id: str | None = None


class RelayStatusUpdateRequest(BaseModel):
    company_id: str
    task_id: str
    relay_job_id: str
    status: str = Field(
        pattern=(
            r"^(queued|submitting|processing|reconciliation_required|"
            r"transferring|succeeded|failed|cancelled)$"
        )
    )
    outputs: list[RelayArtifact] | None = None
    failure_reason: str = Field(default="", max_length=2000)
    reservation_action: RelayReservationAction | None = None
    error: RelayErrorDetail | None = None


class RelayCallbackEventResponse(ApiModel):
    id: str
    company_id: str
    task_id: str
    relay_job_id: str
    relay_status: str
    occurred_at: datetime
    request_id: str
    received_at: datetime


class RelayCallbackEventPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[RelayCallbackEventResponse]


class AvailableModelResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    capability_version: int
    relay_capability_revision: str | None = None
    relay_capability_synced_at: datetime | None = None
    capabilities: dict[str, dict[str, Any]]
    effective_capabilities: dict[str, Any]
    pricing_mode: str
    unit_price_cents: int
    quote_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None


class ArtifactDownloadResponse(BaseModel):
    url: HttpUrl
    expires_seconds: int
    download_record_id: str
    download_status: Literal["issued"] = "issued"


class ArtifactPreviewResponse(BaseModel):
    """Short-lived inline media access that is not download evidence."""

    url: HttpUrl
    expires_seconds: int = Field(ge=1, le=3600)
    media_type: Literal["image", "video"]
    content_type: Literal[
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/webm",
    ]
    preview_status: Literal["issued"] = "issued"


class DownloadRecordResponse(BaseModel):
    id: str
    task_id: str
    asset_id: str
    requested_by_user_id: str
    requested_by_display_name: str
    expires_seconds: int
    expires_at: datetime
    request_id: str
    created_at: datetime
    status: Literal["issued", "completed"]
    downloaded: bool
    completed_at: datetime | None = None
    bytes_sent: int | None = None
    completion_source: DownloadCompletionSource | None = None


class DownloadRecordPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[DownloadRecordResponse]


class DownloadCompletionReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    download_record_id: str = Field(min_length=1, max_length=36)
    company_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    asset_id: str = Field(min_length=1, max_length=160)
    external_event_id: str = Field(min_length=8, max_length=160)
    bytes_sent: int = Field(ge=0)
    completed_at: datetime
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_size_bytes: int = Field(ge=0)
    http_status: Literal[200]
    transfer_scope: Literal["full_body"]

    @field_validator("completed_at")
    @classmethod
    def completed_at_requires_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def completed_transfer_matches_expected_size(
        self,
    ) -> "DownloadCompletionReceiptRequest":
        if self.bytes_sent != self.expected_size_bytes:
            raise ValueError(
                "bytes_sent must equal expected_size_bytes for a full-body transfer"
            )
        return self


class EdgeGatewayDownloadCompletionRequest(DownloadCompletionReceiptRequest):
    gateway_request_id: str = Field(min_length=1, max_length=160)
    gateway_transfer_reference: str = Field(min_length=1, max_length=160)

    @field_validator("gateway_request_id", "gateway_transfer_reference")
    @classmethod
    def gateway_references_are_canonical(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("gateway references must not contain whitespace")
        return value


class ObsAccessLogDownloadCompletionRequest(DownloadCompletionReceiptRequest):
    obs_bucket: str = Field(
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$",
    )
    obs_object_key: str = Field(min_length=1, max_length=1024)
    obs_version_id: str | None = Field(default=None, min_length=1, max_length=256)
    obs_request_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def obs_evidence_has_immutable_reference(
        self,
    ) -> "ObsAccessLogDownloadCompletionRequest":
        if not self.obs_version_id and not self.obs_request_id:
            raise ValueError(
                "OBS evidence requires obs_version_id or obs_request_id"
            )
        return self


class DownloadCompletionResponse(ApiModel):
    id: str
    download_record_id: str
    external_event_id: str
    source: DownloadCompletionSource
    bytes_sent: int
    completed_at: datetime
    verification_version: int
    artifact_sha256: str
    expected_size_bytes: int
    http_status: int
    transfer_scope: str
    source_evidence: dict[str, str]
    signed_event_id: str
    signed_event_timestamp: datetime
    signed_payload_sha256: str
    verified_at: datetime
    created_at: datetime

    @field_validator(
        "completed_at",
        "signed_event_timestamp",
        "verified_at",
        "created_at",
        mode="before",
    )
    @classmethod
    def timestamps_are_returned_as_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TaskHistoryItem(BaseModel):
    id: str
    company_id: str
    user_id: str
    user_display_name: str
    user_email: EmailStr
    model_id: str
    model_display_name: str
    status: TaskStatus
    request_payload: dict[str, Any]
    quote_cents: int
    pricing_snapshot: dict[str, Any]
    capability_snapshot: dict[str, Any]
    reserved_cents: int
    actual_cost_cents: int | None
    output_artifacts: list[TaskArtifactResponse]
    artifact_count: int
    download_issue_count: int
    download_completed_count: int
    downloaded: bool
    last_download_issued_at: datetime | None = None
    last_download_completed_at: datetime | None = None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class RelayCapabilityAuditItem(BaseModel):
    relay_model_id: str
    capability_revision: str
    capabilities: dict[str, Any]
    status: Literal[
        "unmapped",
        "platform_unconfigured",
        "unsafe_expansion",
        "compatible_restriction",
        "identical",
    ]
    platform_model_id: str | None = None
    platform_capability_version: int | None = None
    platform_active: bool | None = None
    approved_revision: str | None = None
    platform_capabilities: dict[str, Any] | None = None


class RelayCapabilityAuditResponse(BaseModel):
    catalog_revision: str
    etag: str
    items: list[RelayCapabilityAuditItem]
    platform_only_model_ids: list[str]


class RelayCapabilityApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)


class RelayCapabilityApprovalResponse(BaseModel):
    model: AdminModelResponse
    compatibility: Literal["compatible_restriction", "identical"]
    capability_revision: str
    changed: bool


class TaskHistoryPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[TaskHistoryItem]


class ArtworkResponse(BaseModel):
    artifact_id: str
    task_id: str
    company_id: str
    asset_id: str
    output_index: int
    media_type: Literal["image", "video"]
    content_type: str
    size_bytes: int
    sha256: str
    created_by_user_id: str
    created_by_display_name: str
    created_by_email: EmailStr
    model_id: str
    model_display_name: str
    request_payload: dict[str, Any]
    actual_cost_cents: int
    download_issue_count: int
    download_completed_count: int
    downloaded: bool
    last_download_issued_at: datetime | None = None
    last_download_completed_at: datetime | None = None
    created_at: datetime


class ArtworkPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[ArtworkResponse]


class TaskReportRow(BaseModel):
    task_id: str
    employee_user_id: str
    employee_display_name: str
    employee_email: EmailStr
    model_id: str
    model_display_name: str
    status: TaskStatus
    quote_cents: int
    actual_cost_cents: int | None
    request_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TaskReportPage(BaseModel):
    page: int
    page_size: int
    total: int
    total_actual_cost_cents: int
    items: list[TaskReportRow]


class ConsumptionReportRow(BaseModel):
    ledger_entry_id: str
    company_id: str
    company_name: str
    task_id: str
    employee_user_id: str
    employee_display_name: str
    employee_email: EmailStr
    model_id: str
    model_display_name: str
    task_status: TaskStatus
    pricing_mode: Literal["per_second", "per_item"] | None = None
    unit_price_cents: int | None = None
    quantity: int | None = None
    amount_cents: int
    consumed_at: datetime


class ConsumptionReportPage(BaseModel):
    page: int
    page_size: int
    total: int
    total_amount_cents: int
    items: list[ConsumptionReportRow]


class BootstrapPlatformAdminRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)


class PlatformAdminIdentityResponse(BaseModel):
    user_id: str


class PlatformAdminMeResponse(BaseModel):
    user_id: str
    email: EmailStr
    display_name: str
    is_platform_admin: bool
    is_platform_owner: bool = False
    permission_codes: list[str] = Field(default_factory=list)


class AdminCreateCompanyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    owner_email: EmailStr
    owner_display_name: str = Field(min_length=1, max_length=120)


class AdminCompanyResponse(ApiModel):
    id: str
    name: str
    status: CompanyStatus
    created_at: datetime
    updated_at: datetime
    owner_activation_required: bool = False
    owner_user_id: str | None = None
    owner_membership_id: str | None = None
    owner_invitation_url: str | None = None
    owner_invitation_expires_at: datetime | None = None


class AdminCompanyPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[AdminCompanyResponse]


class AdminCompanyStatusRequest(BaseModel):
    status: CompanyStatus


class ResourceDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,118}[a-z0-9]$")
    kind: ResourceKind
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    active: bool = True


class ResourceDefinitionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    active: bool


class ResourceDefinitionResponse(ApiModel):
    id: str
    key: str
    kind: ResourceKind
    display_name: str
    description: str
    active: bool


class CompanyResourceGrantRequest(EntitlementPolicyRequest):
    enabled: bool
    config_override: dict[str, Any] = Field(default_factory=dict)


class CompanyResourceGrantResponse(ApiModel):
    id: str
    company_id: str
    resource_id: str
    enabled: bool
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None


class AvailableResourceResponse(BaseModel):
    id: str
    key: str
    kind: ResourceKind
    display_name: str
    description: str
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None


class CompanyModelEntitlementResponse(BaseModel):
    model_id: str
    slug: str
    display_name: str
    status: Literal["draft", "published", "disabled"]
    billing_mode: Literal["per_second", "per_item"]
    grant_id: str | None
    enabled: bool
    price_per_second_cents: int | None
    price_per_item_cents: int | None
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None


class CompanyResourceEntitlementResponse(BaseModel):
    resource_id: str
    key: str
    kind: ResourceKind
    display_name: str
    active: bool
    grant_id: str | None
    enabled: bool
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None


class CompanyEntitlementsResponse(BaseModel):
    company_id: str
    models: list[CompanyModelEntitlementResponse]
    resources: list[CompanyResourceEntitlementResponse]


class ChannelCostCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(ge=-MAX_MONEY_CENTS, le=MAX_MONEY_CENTS)
    idempotency_key: str = Field(min_length=8, max_length=160)
    channel_key: str = Field(min_length=1, max_length=120)
    channel_type: ChannelType
    occurred_at: datetime
    external_reference: str = Field(min_length=1, max_length=240)
    company_id: str | None = None
    personal_workspace_id: str | None = None
    task_id: str | None = None
    relay_job_id: str | None = Field(default=None, max_length=36)
    note: str = Field(default="", max_length=240)
    evidence_source: str | None = Field(default=None, max_length=32)
    evidence_reference: str | None = Field(default=None, max_length=240)
    source_document_sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_requires_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a UTC offset")
        return value


class ChannelCostEntryResponse(ApiModel):
    id: str
    amount_cents: int
    idempotency_key: str
    channel_key: str
    channel_type: ChannelType
    occurred_at: datetime
    external_reference: str
    company_id: str | None
    personal_workspace_id: str | None
    task_id: str | None
    relay_job_id: str | None
    relay_event_id: str | None
    relay_event_timestamp: datetime | None
    relay_payload_sha256: str | None
    note: str
    evidence_source: str | None
    evidence_reference: str | None
    source_document_sha256: str | None
    source: ChannelCostSource
    recorded_by_user_id: str | None
    created_at: datetime


class ChannelCostPage(BaseModel):
    page: int
    page_size: int
    total: int
    total_amount_cents: int
    items: list[ChannelCostEntryResponse]


class AuditLogResponse(ApiModel):
    id: str
    actor_user_id: str
    action: str
    target_type: str
    target_id: str
    before_summary: dict[str, Any]
    after_summary: dict[str, Any]
    outcome: AuditOutcome
    request_id: str
    created_at: datetime


class AuditLogPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[AuditLogResponse]


class CompanyDashboardRow(BaseModel):
    company_id: str
    company_name: str
    company_status: CompanyStatus
    recharge_cents: int
    consumption_cents: int
    available_cents: int
    reserved_cents: int
    task_count: int
    succeeded_count: int
    failed_count: int


class ChannelCostBreakdown(BaseModel):
    channel_key: str
    channel_type: ChannelType
    amount_cents: int


class PlatformDashboardResponse(BaseModel):
    platform_income_cents: int
    platform_recharge_cents: int
    channel_cost_cents: int
    known_gross_profit_cents: int
    gross_profit_cents: int | None
    channel_costs: list[ChannelCostBreakdown]
    unreconciled_succeeded_count: int
    channel_cost_status: Literal["complete", "incomplete"]
    active_company_count: int
    total_task_count: int
    succeeded_task_count: int
    failed_task_count: int
    page: int
    page_size: int
    total_companies: int
    companies: list[CompanyDashboardRow]


class TimeoutScanItemResponse(BaseModel):
    task_id: str
    previous_status: str
    outcome: str
    reason: str
    final_status: str | None = None
    released_cents: int = 0
    released_points: int = 0


class InternalTimeoutScanResponse(BaseModel):
    scanned: int
    compensated: int
    reconciled: int
    deferred: int
    items: list[TimeoutScanItemResponse]


class TaskTimeoutEventResponse(ApiModel):
    id: str
    company_id: str | None
    personal_workspace_id: str | None
    task_id: str
    previous_status: str
    final_status: str
    outcome: str
    reason: str
    released_cents: int
    released_points: int
    ledger_entry_id: str | None
    personal_ledger_entry_id: str | None
    relay_job_id: str | None
    created_at: datetime


class TaskTimeoutEventPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[TaskTimeoutEventResponse]
