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
    BillingUnit,
    ChannelCostSource,
    ChannelType,
    CompanyStatus,
    DownloadCompletionSource,
    InputAssetStatus,
    LedgerKind,
    MembershipStatus,
    PermissionEffect,
    PointLedgerKind,
    PublicationAttemptStatus,
    PublicationJobStatus,
    PublisherConnectionStatus,
    ResourceKind,
    TaskStatus,
)
from .relay_client import RelayArtifact, RelayErrorDetail, RelayReservationAction
from .services.provider_route_identity import (
    canonical_sha256,
    identity_from_payload,
    validate_provider_route_identity,
)


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
    billing_unit: BillingUnit = BillingUnit.CNY_CENT
    billing_version: int = 1


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
    # Null creates a previously absent grant.  Updating an existing row
    # requires the exact timestamp returned by the last read/PUT response.
    expected_updated_at: datetime | None = None
    enabled: bool = True
    price_per_second_cents: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_CENTS
    )
    price_per_item_cents: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_CENTS
    )
    price_per_second_points: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_CENTS
    )
    price_per_item_points: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_CENTS
    )
    config_override: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_updated_at")
    @classmethod
    def expected_updated_at_requires_offset(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("expected_updated_at must include a UTC offset")
        return value


class CompanyModelGrantResponse(ApiModel):
    id: str
    company_id: str
    model_id: str
    enabled: bool
    price_per_second_cents: int | None
    price_per_item_cents: int | None
    price_per_second_points: int | None
    price_per_item_points: int | None
    point_price_candidate_per_second: int | None
    point_price_candidate_per_item: int | None
    point_price_candidate_revision: str | None
    point_price_candidate_created_at: datetime | None
    point_price_candidate_version_id: str | None
    point_price_active_version_id: str | None
    billing_unit: BillingUnit
    billing_version: int
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None
    updated_at: datetime


class RechargeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(gt=0, le=MAX_MONEY_CENTS)
    idempotency_key: str = Field(min_length=8, max_length=120)
    note: str = Field(default="", max_length=240)


class WalletResponse(ApiModel):
    company_id: str
    billing_unit: BillingUnit
    billing_version: int
    available_cents: int | None
    reserved_cents: int | None
    available_points: int | None
    reserved_points: int | None


class LedgerEntryResponse(ApiModel):
    id: str
    company_id: str
    billing_unit: BillingUnit
    billing_version: int
    kind: LedgerKind | PointLedgerKind
    amount_cents: int | None
    available_delta_cents: int | None
    reserved_delta_cents: int | None
    amount_points: int | None
    available_delta_points: int | None
    reserved_delta_points: int | None
    idempotency_key: str
    task_id: str | None
    note: str
    created_at: datetime


class RechargeRecordPage(BaseModel):
    page: int
    page_size: int
    total: int
    billing_unit: BillingUnit | Literal["MIXED"]
    billing_version: int | None
    total_amount_cents: int
    total_amount_points: int
    items: list[LedgerEntryResponse]


class WalletOperationResponse(BaseModel):
    wallet: WalletResponse
    ledger_entry: LedgerEntryResponse


class CompanyPointsMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_available_cents: int = Field(ge=0, le=MAX_MONEY_CENTS)
    idempotency_key: str = Field(min_length=8, max_length=120)


class CompanyPointsMigrationResponse(BaseModel):
    company_id: str
    billing_unit: Literal[BillingUnit.POINT] = BillingUnit.POINT
    billing_version: Literal[2] = 2
    source_cents: int
    converted_points: int
    legacy_points: int
    rounding_remainder_cents: int
    rounding_grant_points: int
    rounding_subsidy_cents: int
    generation_enabled: Literal[False] = False
    changed: bool
    wallet: WalletResponse


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


class PublishingReadinessResponse(BaseModel):
    feature_auto_publish_enabled: bool
    can_read_accounts: bool
    can_manage_accounts: bool
    can_read_jobs: bool
    can_manage_jobs: bool
    side_effects_enabled: bool
    account_side_effects_enabled: bool
    job_side_effects_enabled: bool
    historical_read_enabled: bool
    historical_safety_actions_enabled: bool
    blocking_reasons: list[str] = Field(default_factory=list)


class InputAssetResponse(ApiModel):
    id: str
    company_id: str | None
    personal_workspace_id: str | None
    uploaded_by_user_id: str
    original_filename: str
    media_type: Literal["image", "video", "audio"]
    content_type: str
    size_bytes: int
    sha256: str
    media_metadata_version: Literal[1] | None = None
    width_px: int | None = None
    height_px: int | None = None
    media_container: str | None = None
    video_codec: str | None = None
    video_fps: float | None = None
    duration_ms: int | None = None
    video_has_audio: bool | None = None
    normalization_profile: Literal["director_previs_mp4_v1"] | None = None
    source_sha256: str | None = None
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
    idempotency_key: str
    company_id: str
    user_id: str
    model_id: str
    status: TaskStatus
    request_payload: dict[str, Any]
    billing_unit: BillingUnit
    billing_version: int
    quote_cents: int | None
    quote_points: int | None
    pricing_snapshot: dict[str, Any]
    capability_snapshot: dict[str, Any]
    reserved_cents: int
    reserved_points: int
    actual_cost_cents: int | None
    actual_cost_points: int | None
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
    capability_schema_downgrade_reason: str | None = Field(
        default=None, min_length=3, max_length=500
    )


class AdminModelResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    provider_key: str
    billing_mode: Literal["per_second", "per_item"]
    capability_version: int
    relay_capability_revision: str | None = None
    relay_capability_synced_at: datetime | None = None
    relay_capability_candidate_revision: str | None = None
    relay_capability_candidate_catalog_revision: str | None = None
    relay_capability_candidate: dict[str, Any] | None = None
    relay_capability_candidate_synced_at: datetime | None = None
    relay_capability_approved_ceiling: dict[str, Any] | None = None
    relay_capability_approved_catalog_revision: str | None = None
    relay_capability_approval_status: Literal[
        "unavailable",
        "unapproved",
        "legacy_approved",
        "approved",
        "pending",
    ] = "unavailable"
    relay_capability_requires_approval: bool = False
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


class GenerationReadinessBlockerResponse(BaseModel):
    code: Literal[
        "resource_not_defined",
        "resource_inactive",
        "resource_not_granted",
        "resource_not_yet_effective",
        "resource_grant_expired",
        "resource_call_quota_exhausted",
        "resource_concurrency_saturated",
        "model_call_quota_exhausted",
        "model_concurrency_saturated",
        "commercial_release_plan_missing",
        "commercial_release_candidate_changed",
        "commercial_release_reconciliation_pending",
        "commercial_release_evidence_unavailable",
        "commercial_release_evidence_expired",
        "commercial_release_route_acceptance_pending",
        "commercial_release_provider_cost_unready",
        "commercial_release_provider_cost_plan_mismatch",
        "commercial_release_candidate_drift",
        "commercial_release_live_catalog_drift",
        "commercial_release_published_route_revision_drift",
        "commercial_release_provider_route_identity_drift",
        "commercial_release_release_invariant_failed",
        "commercial_release_blocked",
        "commercial_release_superseded",
    ]
    message: str
    resource_key: str
    resource_name: str
    retryable: bool


class GenerationReadinessStateResponse(BaseModel):
    ready: bool
    status: Literal["ready", "blocked"]
    blockers: list[GenerationReadinessBlockerResponse]


class GenerationOptionReadinessResponse(BaseModel):
    supported: bool
    ready: bool
    status: Literal["ready", "blocked", "unsupported"]
    blockers: list[GenerationReadinessBlockerResponse]


class GenerationModeReadinessResponse(BaseModel):
    default: GenerationReadinessStateResponse
    options: dict[str, GenerationOptionReadinessResponse]


class AvailableModelResponse(BaseModel):
    id: str
    slug: str
    display_name: str
    capability_version: int
    relay_capability_revision: str | None = None
    relay_capability_synced_at: datetime | None = None
    capabilities: dict[str, dict[str, Any]]
    effective_capabilities: dict[str, Any]
    readiness_checked_at: datetime
    mode_readiness: dict[str, GenerationModeReadinessResponse]
    pricing_mode: str
    billing_unit: BillingUnit
    billing_version: int
    unit_price_cents: int | None
    unit_price_points: int | None
    price_version_id: str
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
    billing_unit: BillingUnit
    billing_version: int
    quote_cents: int | None
    quote_points: int | None
    pricing_snapshot: dict[str, Any]
    capability_snapshot: dict[str, Any]
    reserved_cents: int
    reserved_points: int
    actual_cost_cents: int | None
    actual_cost_points: int | None
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


class ProviderCostComponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal[
        "input_token",
        "uncached_input_token",
        "cached_input_token",
        "output_token",
        "output_text_token",
        "output_video_token",
        "thought_token",
        "total_token",
        "output_second",
        "input_second",
        "output_item",
        "input_image_above_free",
        # Accepted only as compatibility aliases. The service persists the
        # exact Relay metric names above so a commercial plan and runtime cost
        # allocation cannot silently use two vocabularies.
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "output_video_seconds",
        "input_video_seconds",
        "reference_images",
        "generated_images",
        "request",
    ]
    rate_micros: int = Field(gt=0, le=10**15)
    quantity_numerator: int = Field(gt=0, le=10**15)
    quantity_denominator: int = Field(gt=0, le=10**15)


class ProviderCostFormulaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal[
        "token_total",
        "resolution_output_second",
        "token_total_with_video_input",
        "composite_media_seconds_images",
        "output_item",
    ]
    platform_billing_unit: Literal["per_second", "per_item"]
    source_capability_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    assumptions: dict[str, Any] = Field(min_length=1)
    components: list[ProviderCostComponentRequest] = Field(
        min_length=1, max_length=16
    )


class ModelCommercialReleasePlanRequest(BaseModel):
    """Explicit owner approval; reconciliation may not fill any omitted fact."""

    model_config = ConfigDict(extra="forbid")
    supersedes_plan_id: str | None = Field(default=None, min_length=36, max_length=36)

    expected_capability_version: int = Field(ge=1)
    expected_candidate_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_catalog_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_routing_release_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_cost_currency: Literal["CNY", "USD"]
    provider_cost_formula: ProviderCostFormulaRequest
    provider_cost_evidence_kind: Literal[
        "provider_price_list", "contract_rate", "provider_invoice"
    ]
    provider_cost_evidence_reference: str = Field(min_length=3, max_length=500)
    provider_cost_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_cost_effective_at: datetime
    fx_cny_micros_per_currency_unit: int = Field(gt=0, le=10**12)
    fx_source: str = Field(min_length=2, max_length=120)
    fx_version: str = Field(min_length=2, max_length=160)
    fx_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fx_effective_at: datetime
    personal_price_points: int | None = Field(default=None, gt=0, le=10**12)
    enterprise_price_points: int | None = Field(default=None, gt=0, le=10**12)
    personal_config_override: dict[str, Any] = Field(default_factory=dict)
    enterprise_config_override: dict[str, Any] = Field(default_factory=dict)
    approval_reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(
        min_length=8,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$",
    )

    @model_validator(mode="after")
    def validate_evidence_times(self) -> "ModelCommercialReleasePlanRequest":
        for field_name in (
            "provider_cost_effective_at",
            "fx_effective_at",
        ):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a UTC offset")
        if self.provider_cost_currency == "CNY" and (
            self.fx_cny_micros_per_currency_unit != 1_000_000
        ):
            raise ValueError("CNY provider cost must use the exact 1:1 FX rate")
        return self


class ModelCommercialReleasePlanResponse(BaseModel):
    id: str
    revision: int
    supersedes_plan_id: str | None
    batch_id: str | None
    model_id: str
    model_slug: str
    candidate_revision: str
    candidate_catalog_revision: str
    capability_version: int
    billing_mode: Literal["per_second", "per_item"]
    provider_cost_currency: Literal["CNY", "USD"]
    provider_cost_formula: dict[str, Any]
    provider_cost_micros: int
    provider_cost_cny_micros: int
    provider_cost_evidence_kind: Literal[
        "provider_price_list", "contract_rate", "provider_invoice"
    ]
    provider_cost_evidence_reference: str
    provider_cost_evidence_sha256: str
    provider_cost_effective_at: datetime
    fx_cny_micros_per_currency_unit: int
    fx_source: str
    fx_version: str
    fx_evidence_sha256: str
    fx_effective_at: datetime
    points_per_cny: Literal[10]
    target_margin_bps: Literal[3000]
    minimum_price_points: int
    personal_price_points: int
    enterprise_price_points: int
    enterprise_distribution_scope: Literal[
        "all_active_point_companies_at_release"
    ]
    personal_config_override: dict[str, Any]
    enterprise_config_override: dict[str, Any]
    approval_reason: str
    approved_by_user_id: str
    approved_at: datetime
    idempotency_key: str
    content_sha256: str
    approved_route_identity: dict[str, Any] | None
    approved_route_identity_sha256: str | None
    state: Literal["approved", "blocked", "released", "superseded"]
    attempt_count: int
    last_blocker_code: str | None
    last_blocker_message: str | None
    route_release_evidence: dict[str, Any] | None
    released_route_identity_sha256: str | None
    publication_receipt: dict[str, Any] | None
    publication_receipt_sha256: str | None
    personal_grant_id: str | None
    company_grant_count: int
    company_ids: list[str]
    released_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ModelCommercialReleaseBatchPreflightRequest(BaseModel):
    """Read-only: which of these models can release, and what still blocks each."""

    model_config = ConfigDict(extra="forbid")

    model_ids: list[str] = Field(min_length=1, max_length=200)


class ModelCommercialReleaseBatchPreflightItem(BaseModel):
    model_id: str
    model_slug: str | None
    display_name: str | None
    provider_key: str | None
    capability: dict[str, Any] | None
    route_evidence: dict[str, Any] | None
    provider_cost: dict[str, Any] | None
    already_published: bool
    blockers: list[str]


class ModelCommercialReleaseBatchPreflightResponse(BaseModel):
    catalog_revision: str
    model_count: int
    releasable_count: int
    blocked_count: int
    items: list[ModelCommercialReleaseBatchPreflightItem]


class ModelCommercialReleaseBatchItemRequest(ModelCommercialReleasePlanRequest):
    """One model's approval inside a batch.

    Every approval fact stays required exactly as in the single-model request.
    The batch supplies only the grouping: ``idempotency_key`` is derived from
    the batch key plus the model slug, so one batch key still yields one plan
    per model.
    """

    model_id: str = Field(min_length=36, max_length=36)
    idempotency_key: str | None = None
    supersedes_plan_id: str | None = None


class ModelCommercialReleaseBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=8,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$",
    )
    items: list[ModelCommercialReleaseBatchItemRequest] = Field(
        min_length=1, max_length=200
    )


class ModelCommercialReleaseBatchResponse(BaseModel):
    batch_id: str
    idempotency_key: str
    state: Literal["approved", "released", "abandoned"]
    model_count: int
    plan_count: int
    catalog_revision: str
    approved_by_user_id: str
    approved_at: datetime
    activated_by_user_id: str | None
    released_at: datetime | None
    attempt_count: int
    last_failure_code: str
    last_failure_summary: dict[str, Any] | None
    result_payload: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ModelCommercialReleaseBatchActivateResponse(BaseModel):
    batch_id: str
    state: Literal["released"]
    catalog_revision: str
    released_model_ids: list[str]
    receipts: list[dict[str, Any]]


class ModelCommercialReleaseReconcileResponse(BaseModel):
    catalog_revision: str
    planned_count: int
    released_count: int
    blocked_count: int
    unchanged_count: int
    items: list[ModelCommercialReleasePlanResponse]


class ProviderOnboardingRouteIdentityRoute(BaseModel):
    """One secret-free route in the Relay publication identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    route_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    channel_id: int = Field(gt=0)
    provider_name: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_account_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_key_index: int = Field(ge=0)
    provider_key_fingerprint_prefix: str = Field(
        pattern=r"^[0-9a-f]{12}$"
    )
    provider_credential_set_version: str = Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    )
    route_binding_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
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


class ProviderOnboardingRouteIdentity(BaseModel):
    """The complete current Relay model-route snapshot used for matching."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    public_model_id: str = Field(min_length=1, max_length=128)
    capability_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_release_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    model_release_revision: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    published_route_revision: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    routing_release_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_cost_readiness_sha256: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    routes: list[ProviderOnboardingRouteIdentityRoute] = Field(
        min_length=1,
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_canonical_routes(self) -> "ProviderOnboardingRouteIdentity":
        route_keys = [(route.route_id, route.channel_id) for route in self.routes]
        if len(route_keys) != len(set(route_keys)):
            raise ValueError("provider-onboarding route identity contains duplicates")
        if route_keys != sorted(route_keys):
            raise ValueError("provider-onboarding route identity is not canonical")
        return self


class ProviderOnboardingStatusRequestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_key: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_name: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_account_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$",
    )
    provider_channel_id: int = Field(gt=0)
    route_identity: ProviderOnboardingRouteIdentity
    route_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_exact_account_route(self) -> "ProviderOnboardingStatusRequestItem":
        if not any(
            route.provider_name == self.provider_name
            and route.provider_account_id == self.provider_account_id
            and route.channel_id == self.provider_channel_id
            for route in self.route_identity.routes
        ):
            raise ValueError(
                "provider-onboarding account does not belong to route identity"
            )
        actual = canonical_sha256(self.route_identity.model_dump(mode="json"))
        if actual != self.route_identity_sha256:
            raise ValueError("provider-onboarding route identity digest mismatch")
        return self


class ProviderOnboardingStatusBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    items: list[ProviderOnboardingStatusRequestItem] = Field(
        min_length=1,
        max_length=200,
    )

    @field_validator("items")
    @classmethod
    def validate_unique_request_keys(
        cls,
        value: list[ProviderOnboardingStatusRequestItem],
    ) -> list[ProviderOnboardingStatusRequestItem]:
        request_keys = [item.request_key for item in value]
        if len(request_keys) != len(set(request_keys)):
            raise ValueError("provider-onboarding request keys must be unique")
        return value


ProviderOnboardingPublicationStatus = Literal[
    "not_observed",
    "commercial_approval_pending",
    "reconciliation_pending",
    "blocked",
    "released",
    "disabled",
    "drifted",
    "unavailable",
]
ProviderOnboardingPersonalPriceStatus = Literal[
    "not_configured",
    "active",
    "drifted",
    "unavailable",
]
ProviderOnboardingPersonalGrantStatus = Literal[
    "not_granted",
    "active",
    "disabled",
    "drifted",
    "unavailable",
]
ProviderOnboardingCompanyPriceStatus = Literal[
    "not_applicable",
    "not_configured",
    "partial",
    "active",
    "drifted",
    "unavailable",
]
ProviderOnboardingCompanyGrantStatus = Literal[
    "not_applicable",
    "not_granted",
    "partial",
    "active",
    "disabled",
    "drifted",
    "unavailable",
]


class ProviderOnboardingPersonalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price_status: ProviderOnboardingPersonalPriceStatus
    grant_status: ProviderOnboardingPersonalGrantStatus
    price_points: int | None = Field(default=None, gt=0)
    grant_id: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "ProviderOnboardingPersonalStatus":
        if (self.price_status == "active") != (self.price_points is not None):
            raise ValueError(
                "provider-onboarding personal price evidence is inconsistent"
            )
        grant_must_exist = self.grant_status in {"active", "disabled"}
        if grant_must_exist != bool(self.grant_id):
            raise ValueError(
                "provider-onboarding personal grant evidence is inconsistent"
            )
        return self


class ProviderOnboardingCompanyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price_status: ProviderOnboardingCompanyPriceStatus
    grant_status: ProviderOnboardingCompanyGrantStatus
    grant_count: int = Field(ge=0)
    enabled_grant_count: int = Field(ge=0)
    active_grant_count: int = Field(ge=0)
    active_price_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "ProviderOnboardingCompanyStatus":
        if any(
            value > self.grant_count
            for value in (
                self.enabled_grant_count,
                self.active_grant_count,
                self.active_price_count,
            )
        ):
            raise ValueError("provider-onboarding company counts are inconsistent")
        if self.active_grant_count > self.enabled_grant_count:
            raise ValueError(
                "active provider-onboarding company grants must be enabled"
            )

        not_applicable = (
            self.price_status == "not_applicable"
            and self.grant_status == "not_applicable"
        )
        if (
            (self.price_status == "not_applicable")
            != (self.grant_status == "not_applicable")
            or (
                not_applicable
                and any(
                    value != 0
                    for value in (
                        self.grant_count,
                        self.enabled_grant_count,
                        self.active_grant_count,
                        self.active_price_count,
                    )
                )
            )
        ):
            raise ValueError(
                "provider-onboarding company not-applicable evidence is inconsistent"
            )

        if (
            self.price_status == "active"
            and (
                self.grant_count == 0
                or self.active_price_count != self.grant_count
            )
        ) or (
            self.price_status == "partial"
            and (
                self.active_price_count == 0
                or self.active_price_count >= self.grant_count
            )
        ) or (
            self.price_status == "not_configured"
            and (self.grant_count == 0 or self.active_price_count != 0)
        ):
            raise ValueError(
                "provider-onboarding company price evidence is inconsistent"
            )

        if (
            self.grant_status == "active"
            and (
                self.grant_count == 0
                or self.active_grant_count != self.grant_count
            )
        ) or (
            self.grant_status == "partial"
            and (
                self.active_grant_count == 0
                or self.active_grant_count >= self.grant_count
            )
        ) or (
            self.grant_status == "disabled"
            and (self.grant_count == 0 or self.active_grant_count != 0)
        ):
            raise ValueError(
                "provider-onboarding company grant evidence is inconsistent"
            )
        return self


class ProviderOnboardingStatusResponseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_key: str
    public_model_id: str
    route_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_status: ProviderOnboardingPublicationStatus
    blocker_code: str | None = None
    plan_id: str | None = None
    execution_id: str | None = None
    publication_receipt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    personal: ProviderOnboardingPersonalStatus
    company: ProviderOnboardingCompanyStatus

    @model_validator(mode="after")
    def validate_publication_evidence(
        self,
    ) -> "ProviderOnboardingStatusResponseItem":
        if self.publication_status in {"released", "disabled"} and not all(
            (
                self.plan_id,
                self.execution_id,
                self.publication_receipt_sha256,
            )
        ):
            raise ValueError(
                "released provider-onboarding publication must carry immutable evidence"
            )
        return self


class ProviderOnboardingStatusBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    observed_at: datetime
    request_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[ProviderOnboardingStatusResponseItem]

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at_is_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("provider-onboarding observed_at must be UTC")
        return value


class RelayCapabilityAuditItem(BaseModel):
    relay_model_id: str
    lifecycle: Literal["reviewed_candidate", "published_route"]
    managed_route: bool
    customer_callable: bool
    capability_revision: str
    capabilities: dict[str, Any]
    status: Literal[
        "unmapped",
        "platform_unconfigured",
        "unsafe_expansion",
        "compatible_restriction",
        "identical",
        "revision_collision",
    ]
    candidate_revision: str
    platform_model_id: str | None = None
    platform_capability_version: int | None = None
    platform_active: bool | None = None
    approved_revision: str | None = None
    platform_capabilities: dict[str, Any] | None = None
    requires_approval: bool
    approval_status: Literal["approved", "pending", "revision_collision"]
    capability_diff: dict[str, Any]
    approval_history: list[dict[str, Any]] = Field(default_factory=list)
    route_evidence_status: Literal[
        "ready", "blocked", "missing", "revision_drift", "unavailable"
    ]
    route_evidence_blockers: list[str] = Field(default_factory=list)
    routing_release_sha256: str | None = None
    model_release_id: str | None = None
    model_release_revision: str | None = None
    published_route_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    route_count: int | None = None
    enabled_route_count: int | None = None
    accepted_route_count: int | None = None
    fresh_test_count: int | None = None
    provider_cost_readiness_sha256: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    provider_cost_ready: bool = False
    provider_cost_rectangle_count: int | None = Field(default=None, ge=0)
    provider_cost_ready_rectangle_count: int | None = Field(default=None, ge=0)
    routes: list[dict[str, Any]] = Field(default_factory=list)
    latest_successful_test_at: datetime | None = None
    evidence_generated_at: datetime | None = None
    test_freshness_max_age_seconds: int | None = None


class RelayCapabilityAuditResponse(BaseModel):
    catalog_revision: str
    catalog_revision_scope: Literal["transport_snapshot"]
    published_route_revision: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    etag: str
    items: list[RelayCapabilityAuditItem]
    platform_only_model_ids: list[str]
    route_evidence_available: bool
    route_evidence_error: str | None = None


class RelayModelReconcileResponse(RelayCapabilityAuditResponse):
    created_count: int = Field(ge=0)
    synced_count: int = Field(ge=0)
    invalidated_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    created_model_ids: list[str] = Field(default_factory=list)
    synced_model_ids: list[str] = Field(default_factory=list)
    invalidated_model_ids: list[str] = Field(default_factory=list)
    reconciliation_audit_id: str | None = None


class RelayCapabilityCandidateSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)
    expected_catalog_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    expected_capability_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    reason: str = Field(min_length=3, max_length=500)


class RelayCapabilityApprovalRequest(BaseModel):
    """Approve only a candidate that the operator already reviewed.

    Both Relay revisions are mandatory here.  The sync endpoint is the only
    place where an initial/unknown candidate may be discovered; approval may
    never silently replace it with a newer live Relay response.
    """

    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)
    expected_catalog_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_capability_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_routing_release_sha256: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    reason: str = Field(min_length=3, max_length=500)


class RelayCapabilityApprovalResponse(BaseModel):
    model: AdminModelResponse
    compatibility: Literal["compatible_restriction", "identical"]
    capability_revision: str
    candidate_revision: str
    approved_revision: str
    approval_status: Literal["approved"]
    requires_approval: bool = False
    capability_diff: dict[str, Any]
    # This identifier belongs to the immutable Platform approval audit event;
    # it is deliberately not a Relay routing-release identifier.
    approval_audit_id: str | None = None
    changed: bool


class RelayCapabilityCandidateResponse(BaseModel):
    model: AdminModelResponse
    compatibility: Literal[
        "platform_unconfigured",
        "unsafe_expansion",
        "compatible_restriction",
        "identical",
    ]
    candidate_revision: str
    approved_revision: str | None = None
    approval_status: Literal[
        "unapproved", "legacy_approved", "approved", "pending"
    ]
    requires_approval: bool
    capability_diff: dict[str, Any]
    changed: bool


class RelayCapabilityHistoryItem(BaseModel):
    id: str
    event_type: Literal["candidate_sync", "approval"]
    actor_user_id: str | None
    actor_kind: Literal["user", "system"]
    actor_key: str | None
    reason: str
    before_revision: str | None = None
    after_revision: str | None = None
    catalog_revision: str | None = None
    capability_diff: dict[str, Any]
    request_id: str
    created_at: datetime


class RelayCapabilityHistoryResponse(BaseModel):
    model_id: str
    candidate_revision: str | None = None
    approved_revision: str | None = None
    approval_status: Literal[
        "unavailable",
        "unapproved",
        "legacy_approved",
        "approved",
        "pending",
    ]
    requires_approval: bool
    capability_diff: dict[str, Any]
    items: list[RelayCapabilityHistoryItem]


class AdminPersonalModelGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)
    expected_quote_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    enabled: bool
    price_per_second_points: int | None = Field(default=None, ge=1)
    price_per_item_points: int | None = Field(default=None, ge=1)
    # These nullable fields are PATCH-like within the otherwise full grant
    # mutation: omission preserves the current policy, while explicit null
    # removes the corresponding limit.
    call_quota: int | None = Field(default=None, gt=0, le=MAX_CALL_QUOTA)
    concurrency_limit: int | None = Field(
        default=None, gt=0, le=MAX_CONCURRENCY_LIMIT
    )
    config_override: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=500)


class AdminPersonalModelGrantBatchMutation(BaseModel):
    """One concurrency-fenced personal retail distribution mutation."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=36)
    expected_capability_version: int = Field(ge=1)
    expected_quote_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    enabled: bool
    price_per_second_points: int | None = Field(default=None, ge=1)
    price_per_item_points: int | None = Field(default=None, ge=1)
    call_quota: int | None = Field(default=None, gt=0, le=MAX_CALL_QUOTA)
    concurrency_limit: int | None = Field(
        default=None, gt=0, le=MAX_CONCURRENCY_LIMIT
    )
    config_override: dict[str, Any] = Field(default_factory=dict)


class AdminPersonalModelGrantBatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[AdminPersonalModelGrantBatchMutation] = Field(
        min_length=1, max_length=100
    )

    @field_validator("changes")
    @classmethod
    def unique_models(
        cls, value: list[AdminPersonalModelGrantBatchMutation]
    ) -> list[AdminPersonalModelGrantBatchMutation]:
        model_ids = [item.model_id for item in value]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("personal model batch contains duplicate model_id values")
        return value


class AdminPersonalModelGrantBatchExecuteRequest(
    AdminPersonalModelGrantBatchPreviewRequest
):
    expected_snapshot: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(
        min_length=8,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$",
    )


class AdminPersonalModelGrantResponse(BaseModel):
    model_id: str
    model_slug: str
    model_display_name: str
    model_status: Literal["draft", "published", "disabled"]
    capability_version: int
    relay_capability_revision: str | None = None
    grant_id: str | None = None
    enabled: bool
    price_per_second_points: int | None = None
    price_per_item_points: int | None = None
    call_quota: int | None = None
    concurrency_limit: int | None = None
    config_override: dict[str, Any]
    quote_revision: str | None = None
    effective_capabilities: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskHistoryPage(BaseModel):
    page: int
    page_size: int
    total: int
    billing_unit: BillingUnit | Literal["MIXED"]
    billing_version: int | None
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
    billing_unit: BillingUnit
    billing_version: int
    actual_cost_cents: int | None
    actual_cost_points: int | None
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
    billing_unit: BillingUnit | Literal["MIXED"]
    billing_version: int | None
    items: list[ArtworkResponse]


class TaskReportRow(BaseModel):
    task_id: str
    employee_user_id: str
    employee_display_name: str
    employee_email: EmailStr
    model_id: str
    model_display_name: str
    status: TaskStatus
    billing_unit: BillingUnit
    billing_version: int
    quote_cents: int | None
    quote_points: int | None
    actual_cost_cents: int | None
    actual_cost_points: int | None
    request_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TaskReportPage(BaseModel):
    page: int
    page_size: int
    total: int
    billing_unit: BillingUnit | Literal["MIXED"]
    billing_version: int | None
    total_actual_cost_cents: int
    total_actual_cost_points: int
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
    billing_unit: BillingUnit
    billing_version: int
    pricing_mode: Literal["per_second", "per_item"] | None = None
    unit_price_cents: int | None = None
    unit_price_points: int | None = None
    quantity: int | None = None
    amount_cents: int | None
    amount_points: int | None
    consumed_at: datetime


class ConsumptionReportPage(BaseModel):
    page: int
    page_size: int
    total: int
    billing_unit: BillingUnit | Literal["MIXED"]
    billing_version: int | None
    total_amount_cents: int
    total_amount_points: int
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
    billing_unit: BillingUnit
    billing_version: int
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
    price_per_second_points: int | None
    price_per_item_points: int | None
    point_price_candidate_per_second: int | None
    point_price_candidate_per_item: int | None
    point_price_candidate_revision: str | None
    point_price_candidate_created_at: datetime | None
    point_price_candidate_version_id: str | None
    point_price_active_version_id: str | None
    billing_unit: BillingUnit
    billing_version: int
    config_override: dict[str, Any]
    call_quota: int | None
    concurrency_limit: int | None
    effective_at: datetime | None
    expires_at: datetime | None
    grant_updated_at: datetime | None


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
    billing_unit: BillingUnit
    billing_version: int
    models: list[CompanyModelEntitlementResponse]
    resources: list[CompanyResourceEntitlementResponse]


class ChannelCostCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_contract_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    provider_cost_revision_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    schema_version: Literal[1, 2] = 1
    amount_cents: int = Field(ge=-MAX_MONEY_CENTS, le=MAX_MONEY_CENTS)
    idempotency_key: str = Field(min_length=8, max_length=160)
    channel_key: str = Field(min_length=1, max_length=120)
    channel_type: ChannelType
    route_id: int | None = Field(default=None, strict=True, gt=0)
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
    identity_status: Literal["unassigned", "bound", "legacy_unknown"] | None = None
    provider_name: str | None = Field(default=None, max_length=160)
    provider_account_id: str | None = Field(default=None, max_length=160)
    provider_channel_id: int | None = Field(default=None, strict=True)
    provider_route_id: int | None = Field(default=None, strict=True)
    provider_key_index: int | None = Field(default=None, strict=True)
    provider_key_fingerprint: str | None = Field(default=None, max_length=64)
    provider_credential_version: str | None = Field(default=None, max_length=36)
    route_key: str | None = Field(default=None, max_length=160)
    routing_release_sha256: str | None = Field(default=None, max_length=71)

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_requires_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_provider_identity(self) -> "ChannelCostCreateRequest":
        if ((self.execution_contract_sha256 is None) != (self.provider_cost_revision_sha256 is None)
                or (self.execution_contract_sha256 is not None and (self.schema_version != 2 or self.task_id is None))):
            raise ValueError("execution cost proof requires a bound v2 task and both digests")
        identity = identity_from_payload(self)
        if self.schema_version == 1:
            if any(value is not None for value in identity.values()):
                raise ValueError(
                    "legacy channel cost cannot carry provider route identity"
                )
        else:
            validate_provider_route_identity(
                identity,
                route_assigned=self.task_id is not None,
                route_id=self.route_id,
                route_key=self.channel_key,
            )
        return self


class ChannelCostEntryResponse(ApiModel):
    id: str
    schema_version: int
    amount_cents: int
    idempotency_key: str
    channel_key: str
    channel_type: ChannelType
    route_id: int | None
    provider_identity_status: Literal["unassigned", "bound", "legacy_unknown"]
    provider_name: str | None
    provider_account_id: str | None
    provider_channel_id: int | None
    provider_route_id: int | None
    provider_key_index: int | None
    provider_key_fingerprint_prefix: str | None
    provider_credential_version: str | None
    routing_release_sha256: str | None
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
    actor_user_id: str | None
    actor_kind: Literal["user", "system"]
    actor_key: str | None
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
    billing_unit: BillingUnit
    billing_version: int
    recharge_cents: int
    consumption_cents: int
    available_cents: int
    reserved_cents: int
    recharge_points: int
    consumption_points: int
    available_points: int
    reserved_points: int
    task_count: int
    succeeded_count: int
    failed_count: int


class ChannelCostBreakdown(BaseModel):
    channel_key: str
    channel_type: ChannelType
    amount_cents: int


class PlatformDashboardResponse(BaseModel):
    billing_unit: BillingUnit | Literal["MIXED"]
    billing_version: int | None
    platform_income_cents: int
    platform_recharge_cents: int
    platform_recharge_points: int
    platform_consumption_points: int
    channel_cost_cents: int
    known_gross_profit_cents: int
    gross_profit_cents: int | None
    channel_costs: list[ChannelCostBreakdown]
    unreconciled_succeeded_count: int
    channel_cost_status: Literal["complete", "incomplete"]
    unattributed_point_settlement_count: int
    revenue_reconciliation_status: Literal["complete", "incomplete"]
    finance_status: Literal["complete", "incomplete"]
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
    company_point_ledger_entry_id: str | None
    personal_ledger_entry_id: str | None
    relay_job_id: str | None
    created_at: datetime


class TaskTimeoutEventPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[TaskTimeoutEventResponse]
