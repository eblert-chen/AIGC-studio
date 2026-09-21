from __future__ import annotations

from datetime import datetime
import os
import socket
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import Settings, get_settings, runtime_settings_are_protected
from .asset_storage import (
    FilesystemInputAssetSigner,
    InputAssetStore,
    build_input_asset_store,
    build_showcase_media_store,
)
from .artifact_copy import HttpArtifactContentSource
from .database import Base, build_engine, build_session_factory
from .database_privileges import (
    attest_platform_database,
    attest_platform_database_connection,
)
from .platform_admin_access_router import (
    router as platform_admin_access_router,
)
from .routers.admin_operations import router as admin_operations_router
from .routers.admin_relay_native_console import (
    router as admin_relay_native_console_router,
)
from .routers.admin_task_content import router as admin_task_content_router
from .routers.relay_telemetry import router as relay_telemetry_router
from .routers.personal import router as personal_workspace_router
from .routers.authentication import router as authentication_router
from .routers.showcase import router as showcase_router
from .routers.company_access import router as company_access_router
from .routers.company_input_assets import create_company_input_assets_router
from .routers.payments import router as payments_router
from .routers.finance import router as finance_router
from .routers.enterprise_billing import router as enterprise_billing_router
from .routers.director_shot_packages import router as director_shot_packages_router
from .download_gateway import (
    DownloadGatewayClient,
    DownloadGatewayPermanentError,
    DownloadGatewayTemporaryError,
)
from .services.download_gateway_registrations import (
    DownloadGatewayAttemptCipher,
    DownloadGatewayRegistrationService,
)
from .dependencies import (
    TenantContext,
    PlatformAdminContext,
    get_db,
    get_tenant_context,
    require_bootstrap_token,
    require_download_edge_completion_service,
    require_internal_service,
    require_platform_admin,
    require_permission,
)
from .models import (
    BillingUnit,
    ChannelCostSource,
    ChannelType,
    Company,
    CompanyMembership,
    CompanyResourceGrant,
    CompanyPointLedgerEntry,
    CompanyPointWalletAccount,
    DownloadCompletionSource,
    LedgerEntry,
    ModelDefinition,
    MembershipRole,
    MembershipStatus,
    TaskArtifact,
    PublicationJobStatus,
    ResourceDefinition,
    ResourceKind,
    Role,
    TaskStatus,
    User,
    WalletAccount,
)
from .schemas import (
    ArtifactDownloadResponse,
    ArtifactPreviewResponse,
    ArtworkPage,
    AdminCompanyPage,
    AdminCompanyResponse,
    AdminCompanyStatusRequest,
    AdminCreateCompanyRequest,
    AdminModelCreateRequest,
    AdminModelResponse,
    AdminModelUpdateRequest,
    ModelCommercialReleaseBatchActivateResponse,
    ModelCommercialReleaseBatchCreateRequest,
    ModelCommercialReleaseBatchPreflightRequest,
    ModelCommercialReleaseBatchPreflightResponse,
    ModelCommercialReleaseBatchResponse,
    ModelCommercialReleasePlanRequest,
    ModelCommercialReleasePlanResponse,
    ModelCommercialReleaseReconcileResponse,
    RelayCapabilityApprovalRequest,
    RelayCapabilityApprovalResponse,
    RelayCapabilityAuditResponse,
    RelayCapabilityCandidateResponse,
    RelayCapabilityCandidateSyncRequest,
    RelayCapabilityHistoryResponse,
    RelayModelReconcileResponse,
    AdminPersonalModelGrantBatchExecuteRequest,
    AdminPersonalModelGrantBatchPreviewRequest,
    AdminPersonalModelGrantRequest,
    AdminPersonalModelGrantResponse,
    AuditLogPage,
    AvailableModelResponse,
    AvailableResourceResponse,
    BootstrapPlatformAdminRequest,
    BootstrapRequest,
    BootstrapResponse,
    ChannelCostCreateRequest,
    ChannelCostEntryResponse,
    ChannelCostPage,
    CompanyEntitlementsResponse,
    CompanyModelGrantRequest,
    CompanyModelGrantResponse,
    CompanyResourceGrantRequest,
    CompanyResourceGrantResponse,
    CompanyPointsMigrationRequest,
    CompanyPointsMigrationResponse,
    CreateDevPublisherConnectionRequest,
    CreatePublicationJobRequest,
    CreateTaskRequest,
    DevModelSeedRequest,
    DevModelSeedResponse,
    DownloadRecordPage,
    EdgeGatewayDownloadCompletionRequest,
    ObsAccessLogDownloadCompletionRequest,
    DownloadCompletionResponse,
    InternalDispatchResponse,
    InternalTimeoutScanResponse,
    PromotedInputAssetResponse,
    PromoteTaskArtifactRequest,
    LedgerEntryResponse,
    PublicationJobPage,
    PlatformAdminIdentityResponse,
    PlatformAdminMeResponse,
    PlatformDashboardResponse,
    ProviderOnboardingStatusBatchRequest,
    ProviderOnboardingStatusBatchResponse,
    PublicationJobDetailResponse,
    PublicationJobResponse,
    PublishingReadinessResponse,
    PublisherConnectionResponse,
    PublisherOAuthProviderResponse,
    RechargeRequest,
    RechargeRecordPage,
    ReconcilePublicationJobRequest,
    StartPublisherOAuthRequest,
    StartPublisherOAuthResponse,
    ConsumptionReportPage,
    RelayCallbackEventPage,
    RelayStatusUpdateRequest,
    ResourceDefinitionRequest,
    ResourceDefinitionResponse,
    ResourceDefinitionUpdateRequest,
    TaskResponse,
    TaskHistoryPage,
    TaskReportPage,
    TaskTimeoutEventPage,
    TimeoutScanItemResponse,
    WalletOperationResponse,
    WalletResponse,
)
from .relay_client import (
    HttpxRelayOperationsClient,
    RelayClient,
    RelayOperationsClient,
    RelayPermanentError,
    RelayTemporaryError,
    validate_bound_artifact_download,
    validate_model_catalog_release_evidence_pair,
    validate_protected_model_catalog_routes,
)
from .relay_backends import (
    LEGACY_RELAY_BACKEND_ID,
    RelayBackendRegistry,
    RelayBackendResolutionError,
    build_relay_backend_registry,
    relay_callback_url_for_backend,
)
from .request_ids import normalize_request_id
from .services.billing import WalletService
from .services.company_points_billing import CompanyPointBillingService
from .services.channel_costs import ChannelCostService
from .services.channel_cost_events import ChannelCostEventVerifier
from .services.download_completion_events import DownloadCompletionEventVerifier
from .services.admin import PlatformAdminService
from .platform_admin_access_catalog import PLATFORM_ADMIN_PERMISSION_CODES
from .services.platform_admin_access import PlatformAdminAccessService
from .services.audit import AuditService
from .services.access_lifecycle import AccessLifecycleService
from .services.authentication import (
    INVITATION_HANDOFF_COOKIE_NAME,
    InvitationService,
    OIDC_STATE_COOKIE_NAME,
)
from .services.companies import CompanyService
from .services.dashboard import DashboardService
from .services.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)
from .services.models import ModelCatalogService, ModelGrantService
from .services.model_release_guard import (
    ModelReleaseReadiness,
    build_model_release_readiness,
)
from .services.model_commercial_release import ModelCommercialReleaseService
from .services.personal import PersonalRetailGrantService
from .services.provider_onboarding_status import (
    ProviderOnboardingStatusProjectionService,
)
from .services.publishing import PublishingService
from .publishing_adapters import (
    PublisherAdapterRegistry,
    build_publisher_registry,
    load_publisher_adapter,
)
from .payment_providers import PaymentProviderRegistry
from .services.commercial_pricing import CommercialPricingPolicy
from .services.payment_webhooks import PaymentWebhookVerifierRegistry
from .services.relay_capabilities import RelayCapabilityService
from .services.relay_catalog_reconciliation import (
    ReconciliationAuditActor,
    RelayCatalogReconciliationService,
)
from .services.input_assets import (
    InputAssetRelayResolver,
    InputAssetService,
)
from .services.director_shot_packages import DirectorShotPackageService
from .services.permissions import PermissionService
from .services.relay_outbox import RelayOutboxDispatcher, RelayOutboxService
from .services.relay_status import RelayStatusService
from .services.relay_callbacks import (
    RelayCallbackService,
    RelayCallbackVerifier,
    RelayCallbackVerifierRegistry,
)
from .services.relay_telemetry import RelayTelemetryVerifier
from .services.provider_alerts import ProviderAlertForwarder
from .services.reports import (
    DownloadCompletionService,
    DownloadRecordService,
    ReportService,
)
from .services.artifacts import TaskArtifactService
from .services.resources import ResourceGrantService
from .services.tasks import TaskService
from .services.task_timeouts import TaskTimeoutService
from .services.task_cancellation import GenerationCancellationService

MAX_INTERNAL_EVENT_BODY_BYTES = 64 * 1024


async def _read_limited_request_body(
    request: Request,
    *,
    max_bytes: int = MAX_INTERNAL_EVENT_BODY_BYTES,
) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Content-Length must be a non-negative integer",
            ) from None
        if declared_length < 0:
            raise HTTPException(
                status_code=400,
                detail="Content-Length must be a non-negative integer",
            )
        if declared_length > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Internal event body exceeds the maximum size",
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Internal event body exceeds the maximum size",
            )
        body.extend(chunk)
    return bytes(body)


def _validate_report_time_range(
    start_time: datetime | None, end_time: datetime | None
) -> None:
    for value in (start_time, end_time):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(
                status_code=422,
                detail="报表时间必须包含 UTC 偏移，例如 2026-08-01T00:00:00+08:00",
            )
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise HTTPException(status_code=422, detail="start_time 必须早于 end_time")


def _visible_user_id_for_scope(
    session: Session,
    *,
    context: TenantContext,
    scope: Literal["mine", "company"],
) -> str | None:
    permissions = PermissionService.effective_permissions(
        session, membership_id=context.membership_id
    )
    if scope == "mine":
        if "tasks.read" not in permissions:
            raise HTTPException(
                status_code=403,
                detail="本人任务范围需要 tasks.read 权限",
            )
        return context.user_id
    if "reports.read" not in permissions:
        raise HTTPException(
            status_code=403,
            detail="公司范围需要 reports.read 权限",
        )
    return None


def _model_audit_summary(snapshot: dict) -> dict:
    return {
        key: snapshot[key]
        for key in (
            "slug",
            "display_name",
            "provider_key",
            "billing_mode",
            "capability_version",
            "relay_capability_revision",
            "relay_capability_candidate_revision",
            "relay_capability_candidate_catalog_revision",
            "relay_capability_approved_catalog_revision",
            "relay_capability_approval_status",
            "active",
            "status",
            "capabilities",
        )
    }


def _publisher_connection_audit_summary(connection) -> dict:
    return {
        "company_id": connection.company_id,
        "provider": connection.provider,
        "display_name": connection.display_name,
        "external_account_id": connection.external_account_id,
        "status": connection.status.value,
        "disabled_at": (
            connection.disabled_at.isoformat()
            if connection.disabled_at is not None
            else None
        ),
    }


def _validate_publisher_authorization_url(
    value: str,
    *,
    expected_state: str,
    production: bool,
) -> str:
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError("Publisher authorization URL is invalid")
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (production and parsed.scheme != "https")
    ):
        raise ValueError("Publisher authorization URL is invalid")
    states = parse_qs(parsed.query, keep_blank_values=True).get("state", [])
    if states != [expected_state]:
        raise ValueError("Publisher authorization URL did not preserve OAuth state")
    return value


def _publisher_oauth_result_url(
    success_url: str,
    *,
    status: Literal["connected", "failed"],
    provider: str = "",
    reason: str = "",
) -> str:
    parsed = urlsplit(success_url)
    query = {"publishing_oauth": status}
    if provider:
        query["provider"] = provider
    if reason:
        query["reason"] = reason
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
    )


def _publication_job_audit_summary(job) -> dict:
    return {
        "company_id": job.company_id,
        "task_artifact_id": job.task_artifact_id,
        "connection_id": job.connection_id,
        "status": job.status.value,
        "scheduled_at": (
            job.scheduled_at.isoformat() if job.scheduled_at is not None else None
        ),
        "timezone": job.timezone,
        "attempt_count": job.attempt_count,
        "external_post_id": job.external_post_id,
        "error_code": job.error_code,
    }


def _can_publish_company_artifacts(session: Session, *, context: TenantContext) -> bool:
    user = session.get(User, context.user_id)
    if user is not None and user.is_platform_admin:
        return True
    roles = AccessLifecycleService.roles_for_membership(
        session,
        company_id=context.company_id,
        membership_id=context.membership_id,
    )
    if any(role.system_key == "owner" for role in roles):
        return True
    return "reports.read" in PermissionService.effective_permissions(
        session, membership_id=context.membership_id
    )


def _company_wallet_payload(session: Session, *, company_id: str) -> dict:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="公司不存在")
    if company.billing_version == 2:
        wallet = session.get(CompanyPointWalletAccount, company_id)
        if wallet is None:
            raise HTTPException(status_code=409, detail="公司积分钱包状态不完整")
        return {
            "company_id": company_id,
            "billing_unit": BillingUnit.POINT,
            "billing_version": 2,
            "available_cents": None,
            "reserved_cents": None,
            "available_points": wallet.available_points,
            "reserved_points": wallet.reserved_points,
        }
    if company.billing_version != 1:
        raise HTTPException(status_code=409, detail="公司计费版本无效")
    wallet = session.get(WalletAccount, company_id)
    if wallet is None:
        raise HTTPException(status_code=404, detail="公司钱包不存在")
    return {
        "company_id": company_id,
        "billing_unit": BillingUnit.CNY_CENT,
        "billing_version": 1,
        "available_cents": wallet.available_cents,
        "reserved_cents": wallet.reserved_cents,
        "available_points": None,
        "reserved_points": None,
    }


def _legacy_ledger_payload(entry: LedgerEntry) -> dict:
    return {
        "id": entry.id,
        "company_id": entry.company_id,
        "billing_unit": BillingUnit.CNY_CENT,
        "billing_version": 1,
        "kind": entry.kind,
        "amount_cents": entry.amount_cents,
        "available_delta_cents": entry.available_delta_cents,
        "reserved_delta_cents": entry.reserved_delta_cents,
        "amount_points": None,
        "available_delta_points": None,
        "reserved_delta_points": None,
        "idempotency_key": entry.idempotency_key,
        "task_id": entry.task_id,
        "note": entry.note,
        "created_at": entry.created_at,
    }


def _point_ledger_payload(entry: CompanyPointLedgerEntry) -> dict:
    return {
        "id": entry.id,
        "company_id": entry.company_id,
        "billing_unit": BillingUnit.POINT,
        "billing_version": 2,
        "kind": entry.kind,
        "amount_cents": None,
        "available_delta_cents": None,
        "reserved_delta_cents": None,
        "amount_points": entry.amount_points,
        "available_delta_points": entry.available_delta_points,
        "reserved_delta_points": entry.reserved_delta_points,
        "idempotency_key": entry.idempotency_key,
        "task_id": entry.task_id,
        "note": entry.note,
        "created_at": entry.created_at,
    }


def _report_billing_metadata(
    session: Session,
    *,
    company_id: str | None,
    unit_versions: set[tuple[str, int]],
) -> tuple[BillingUnit | Literal["MIXED"], int | None]:
    """Describe report units without guessing from a possibly empty page."""

    if len(unit_versions) > 1:
        return "MIXED", None
    if len(unit_versions) == 1:
        unit_value, version = next(iter(unit_versions))
        try:
            return BillingUnit(unit_value), version
        except ValueError as error:
            raise HTTPException(
                status_code=409,
                detail="报表包含未知计费单位",
            ) from error
    if company_id is None:
        return "MIXED", None
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="公司不存在")
    if company.billing_version == 1:
        return BillingUnit.CNY_CENT, 1
    if company.billing_version == 2:
        return BillingUnit.POINT, 2
    raise HTTPException(status_code=409, detail="公司计费版本无效")


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    relay_client: RelayClient | None = None,
    relay_backend_registry: RelayBackendRegistry | None = None,
    relay_operations_client: RelayOperationsClient | None = None,
    input_asset_store: InputAssetStore | None = None,
    showcase_media_store: InputAssetStore | None = None,
    download_gateway_client: DownloadGatewayClient | None = None,
    download_gateway_registration_service: (
        DownloadGatewayRegistrationService | None
    ) = None,
    provider_alert_forwarder: ProviderAlertForwarder | None = None,
    publisher_registry: PublisherAdapterRegistry | None = None,
    payment_provider_registry: PaymentProviderRegistry | None = None,
    payment_webhook_verifier_registry: PaymentWebhookVerifierRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings("platform-api")
    engine = engine or build_engine(settings.database_url)
    attest_platform_database(engine, "platform-api")
    if settings.auto_create_tables:
        Base.metadata.create_all(engine)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    # Commercial payment integrations are opt-in.  An empty registry is an
    # intentional fail-closed default: no test double or unsigned webhook can
    # become a real-money path merely because the API process is running.
    app.state.payment_provider_registry = (
        payment_provider_registry or PaymentProviderRegistry()
    )
    app.state.payment_webhook_verifier_registry = (
        payment_webhook_verifier_registry or PaymentWebhookVerifierRegistry()
    )
    if publisher_registry is None:
        configured_publisher_adapters = [
            load_publisher_adapter(
                spec.strip(),
                credential_manifest=(
                    settings.publishing_plugin_secret_manifest(
                        "adapters", spec.strip()
                    )
                    if runtime_settings_are_protected(settings)
                    else None
                ),
                require_credential_manifest=runtime_settings_are_protected(settings),
            )
            for spec in settings.publishing_adapters.split(",")
            if spec.strip()
        ]
        publisher_registry = build_publisher_registry(
            environment=settings.environment,
            adapters=configured_publisher_adapters,
            include_mock=False,
        )
    app.state.publisher_registry = publisher_registry
    app.state.development_platform_owner_user_ids = set()
    app.state.input_asset_store = input_asset_store or build_input_asset_store(settings)
    app.state.showcase_media_store = (
        showcase_media_store
        or build_showcase_media_store(
            settings,
            base_store=app.state.input_asset_store,
        )
    )
    app.state.input_asset_signer = (
        FilesystemInputAssetSigner(
            public_base_url=settings.input_asset_public_base_url,
            signing_secret=settings.input_asset_signing_secret,
        )
        if app.state.input_asset_store.kind == "filesystem"
        else None
    )
    app.state.input_asset_relay_signer = (
        FilesystemInputAssetSigner(
            public_base_url=(
                settings.input_asset_relay_base_url
                or settings.input_asset_public_base_url
            ),
            signing_secret=settings.input_asset_signing_secret,
        )
        if app.state.input_asset_store.kind == "filesystem"
        else None
    )
    app.state.input_asset_relay_resolver = InputAssetRelayResolver(
        app.state.session_factory,
        store=app.state.input_asset_store,
        signer=app.state.input_asset_relay_signer,
        expires_seconds=settings.input_asset_relay_signed_url_seconds,
    )
    legacy_relay_callback_verifier = (
        RelayCallbackVerifier(
            settings.relay_callback_signing_secret,
            max_age_seconds=settings.relay_callback_max_age_seconds,
        )
        if settings.relay_legacy_compatibility_enabled
        and settings.relay_callback_signing_secret
        else None
    )
    app.state.relay_callback_verifier = legacy_relay_callback_verifier
    relay_callback_verifiers = {
        backend_id: RelayCallbackVerifier(
            secret.get_secret_value(),
            max_age_seconds=settings.relay_callback_max_age_seconds,
        )
        for backend_id, secret in settings.relay_callback_signing_secrets.items()
    }
    if legacy_relay_callback_verifier is not None:
        relay_callback_verifiers[LEGACY_RELAY_BACKEND_ID] = (
            legacy_relay_callback_verifier
        )
    app.state.relay_callback_verifier_registry = (
        RelayCallbackVerifierRegistry(relay_callback_verifiers)
        if relay_callback_verifiers
        else None
    )
    app.state.channel_cost_event_verifier = (
        ChannelCostEventVerifier(
            settings.channel_cost_signing_secret,
            signature_required=settings.require_channel_cost_signature,
            max_age_seconds=settings.channel_cost_signature_max_age_seconds,
        )
        if settings.channel_cost_signing_secret
        else None
    )
    app.state.relay_telemetry_verifier = (
        RelayTelemetryVerifier(
            settings.relay_telemetry_signing_secret,
            max_age_seconds=settings.relay_telemetry_signature_max_age_seconds,
        )
        if settings.relay_telemetry_signing_secret
        else None
    )
    app.state.provider_alert_verifier = (
        RelayTelemetryVerifier(
            settings.provider_alert_signing_secret,
            max_age_seconds=settings.provider_alert_signature_max_age_seconds,
        )
        if settings.provider_alert_signing_secret
        else None
    )
    owns_provider_alert_forwarder = False
    if provider_alert_forwarder is None and settings.provider_alert_forward_webhook_url:
        provider_alert_forwarder = ProviderAlertForwarder(
            settings.provider_alert_forward_webhook_url,
            settings.provider_alert_forward_signing_secret or "",
            timeout_seconds=settings.provider_alert_forward_timeout_seconds,
            production=runtime_settings_are_protected(settings),
        )
        owns_provider_alert_forwarder = True
    app.state.provider_alert_forwarder = provider_alert_forwarder
    if owns_provider_alert_forwarder:
        assert provider_alert_forwarder is not None
        app.router.on_shutdown.append(provider_alert_forwarder.aclose)
    app.state.download_completion_event_verifier = (
        DownloadCompletionEventVerifier(
            edge_gateway_signing_secret=(
                settings.download_completion_edge_gateway_signing_secret
            ),
            obs_access_log_signing_secret=(
                settings.download_completion_obs_access_log_signing_secret
            ),
            max_age_seconds=(settings.download_completion_signature_max_age_seconds),
        )
        if settings.download_completion_edge_gateway_signing_secret
        and settings.download_completion_obs_access_log_signing_secret
        else None
    )
    if download_gateway_client is None and settings.download_gateway_configured:
        download_gateway_client = DownloadGatewayClient(
            registration_url=settings.download_gateway_registration_url or "",
            public_base_url=settings.download_gateway_public_base_url or "",
            service_token=settings.download_gateway_service_token or "",
            signing_secret=(
                settings.download_gateway_registration_signing_secret or ""
            ),
            timeout_seconds=settings.download_gateway_timeout_seconds,
            max_ticket_ttl_seconds=settings.download_gateway_ticket_ttl_seconds,
            source_ttl_margin_seconds=(
                settings.download_gateway_source_ttl_margin_seconds
            ),
        )
    app.state.download_gateway_client = download_gateway_client
    app.state.download_gateway_registration_service = (
        download_gateway_registration_service
    )
    app.state.download_gateway_registration_lease_owner = (
        f"platform-api:{socket.gethostname()}:{os.getpid()}:{uuid4()}"
    )

    def resolve_download_gateway_registration_service() -> (
        DownloadGatewayRegistrationService | None
    ):
        gateway_client = app.state.download_gateway_client
        if gateway_client is None:
            return None
        current = app.state.download_gateway_registration_service
        if current is not None and current.gateway_client is gateway_client:
            return current
        encryption_key = settings.download_gateway_attempt_encryption_key_base64
        if not encryption_key:
            raise DownloadGatewayTemporaryError(
                "Download Gateway attempt encryption key is not configured"
            )
        current = DownloadGatewayRegistrationService(
            app.state.session_factory,
            gateway_client,
            DownloadGatewayAttemptCipher.from_base64(encryption_key),
            lease_owner=app.state.download_gateway_registration_lease_owner,
            lease_seconds=settings.download_gateway_registration_lease_seconds,
            max_attempts=settings.download_gateway_registration_max_attempts,
            retry_base_seconds=(
                settings.download_gateway_registration_retry_base_seconds
            ),
            retry_cap_seconds=(
                settings.download_gateway_registration_retry_cap_seconds
            ),
            gateway_ticket_ttl_seconds=(settings.download_gateway_ticket_ttl_seconds),
            source_ttl_margin_seconds=(
                settings.download_gateway_source_ttl_margin_seconds
            ),
        )
        app.state.download_gateway_registration_service = current
        return current

    app.state.resolve_download_gateway_registration_service = (
        resolve_download_gateway_registration_service
    )
    if download_gateway_client is not None:
        resolve_download_gateway_registration_service()
    owns_relay_backend_registry = relay_backend_registry is None
    if relay_backend_registry is None:
        relay_backend_registry = build_relay_backend_registry(
            default_backend_id=settings.relay_default_backend_id,
            default_contract_revision=settings.relay_default_contract_revision,
            configurations=settings.relay_backends,
            legacy_base_url=settings.relay_base_url,
            legacy_client_id=settings.relay_client_id,
            legacy_api_key=settings.relay_api_key,
            allow_local_http=not runtime_settings_are_protected(settings),
            legacy_compatibility_enabled=(
                settings.relay_legacy_compatibility_enabled
            ),
            fallback_client_provider=(
                (lambda: getattr(app.state, "relay_client", None))
                if settings.relay_legacy_compatibility_enabled
                else None
            ),
        )
    if relay_client is None:
        relay_client = relay_backend_registry.default_client_or_none()
    app.state.relay_client = relay_client
    app.state.relay_backend_registry = relay_backend_registry
    if owns_relay_backend_registry:
        app.router.on_shutdown.append(relay_backend_registry.close)

    def resolve_task_relay_client(task) -> RelayClient:
        try:
            return app.state.relay_backend_registry.resolve(
                backend_id=task.relay_backend_id,
                contract_revision=task.relay_contract_revision,
            )
        except RelayBackendResolutionError as exc:
            raise HTTPException(
                status_code=503,
                detail="Relay client is not configured",
            ) from exc

    app.state.resolve_task_relay_client = resolve_task_relay_client

    def read_relay_model_release_evidence(
        *,
        request_id: str,
        required: bool,
    ):
        client = app.state.relay_client
        if client is None:
            if required:
                raise HTTPException(status_code=503, detail="Relay 客户端未配置")
            return None, "Relay 客户端未配置"
        reader = getattr(client, "get_model_release_evidence", None)
        if not callable(reader):
            if required:
                raise HTTPException(
                    status_code=503,
                    detail="Relay 路由发布测试证据接口未配置",
                )
            return None, "Relay 路由发布测试证据接口未配置"
        try:
            return reader(request_id=request_id), None
        except RelayTemporaryError as exc:
            if required:
                raise HTTPException(
                    status_code=503,
                    detail="Relay 路由发布测试证据暂时不可用",
                ) from exc
            return None, "Relay 路由发布测试证据暂时不可用"
        except RelayPermanentError as exc:
            if required:
                raise HTTPException(
                    status_code=502,
                    detail="Relay 路由发布测试证据无效或鉴权失败",
                ) from exc
            return None, "Relay 路由发布测试证据无效或鉴权失败"

    def read_relay_model_catalog(
        *,
        request_id: str,
        required: bool,
    ):
        client = app.state.relay_client
        if client is None:
            if required:
                raise HTTPException(status_code=503, detail="Relay 客户端未配置")
            return None, "Relay 客户端未配置"
        reader = getattr(client, "get_model_catalog", None)
        if not callable(reader):
            if required:
                raise HTTPException(
                    status_code=503,
                    detail="Relay 模型目录接口未配置",
                )
            return None, "Relay 模型目录接口未配置"
        try:
            catalog_read = reader(
                if_none_match=None,
                request_id=request_id,
            )
        except RelayTemporaryError as exc:
            if required:
                raise HTTPException(
                    status_code=503,
                    detail="Relay 模型目录暂时不可用",
                ) from exc
            return None, "Relay 模型目录暂时不可用"
        except RelayPermanentError as exc:
            if required:
                raise HTTPException(
                    status_code=502,
                    detail="Relay 模型目录无效或鉴权失败",
                ) from exc
            return None, "Relay 模型目录无效或鉴权失败"
        catalog = getattr(catalog_read, "catalog", None)
        if (
            catalog is None
            or bool(getattr(catalog_read, "not_modified", False))
        ):
            if required:
                raise HTTPException(
                    status_code=502,
                    detail="Relay 未返回完整模型目录快照",
                )
            return None, "Relay 未返回完整模型目录快照"
        return catalog, None

    def require_matching_relay_release_snapshot(*, catalog, evidence) -> None:
        try:
            validate_model_catalog_release_evidence_pair(
                catalog=catalog,
                evidence=evidence,
            )
            if runtime_settings_are_protected(settings):
                validate_protected_model_catalog_routes(catalog=catalog)
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502,
                detail="Relay 模型目录与路由发布证据不满足受保护环境要求",
            ) from exc

    def require_models_release_ready(
        session: Session,
        *,
        model_ids: set[str],
        request_id: str,
    ) -> dict[str, ModelReleaseReadiness]:
        if not model_ids:
            return {}
        models = {
            model.id: model
            for model in session.scalars(
                select(ModelDefinition).where(ModelDefinition.id.in_(model_ids))
            ).all()
        }
        missing = sorted(model_ids - set(models))
        if missing:
            raise HTTPException(status_code=404, detail="模型不存在")
        for model_id in sorted(model_ids):
            model = models[model_id]
            skip_relay_sync = __import__("os").environ.get("ENVIRONMENT") == "development"
            if not skip_relay_sync and (
                model.relay_capability_revision is None
                or model.relay_capability_candidate_revision is None
                or model.relay_capability_approved_ceiling is None
                or model.relay_capability_candidate_revision
                != model.relay_capability_revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "模型必须先同步并批准当前 Relay 能力候选版本，"
                        "才能恢复客户分发"
                    ),
                )
        # Resolve readiness from the live Relay snapshot only, and let every
        # failure propagate as its own status: 503 when Relay cannot be read,
        # 502 when the catalog/evidence pair does not match, 409 when the model
        # is not currently distributable.
        #
        # This must stay strict. The directory read reports these exact
        # blockers to the operator, so admitting a billable task on evidence
        # the read refuses would make the two surfaces disagree about the same
        # model. Development gating belongs on `skip_relay_sync` above, which
        # only relaxes the capability-version comparison - never the evidence.
        catalog, _ = read_relay_model_catalog(
            request_id=request_id,
            required=True,
        )
        evidence, evidence_error = read_relay_model_release_evidence(
            request_id=request_id,
            required=True,
        )
        require_matching_relay_release_snapshot(
            catalog=catalog,
            evidence=evidence,
        )
        catalog_models = {item.id: item for item in catalog.data}
        result: dict[str, ModelReleaseReadiness] = {}
        for model_id in sorted(model_ids):
            model = models[model_id]
            relay_model = catalog_models.get(model.slug)
            if (
                relay_model is None
                or relay_model.lifecycle != "published_route"
                or not relay_model.customer_callable
                or relay_model.capability_revision
                != model.relay_capability_revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Relay 当前模型目录未发布该模型路由",
                )
            if (
                runtime_settings_are_protected(settings)
                and not relay_model.managed_route
            ):
                raise HTTPException(
                    status_code=409,
                    detail="受保护环境只允许已完成托管接入闭环的模型路由",
                )
            item = RelayCapabilityService.require_customer_distribution_ready(
                public_model_id=model.slug,
                capability_revision=model.relay_capability_revision,
                evidence=evidence,
                evidence_error=evidence_error,
            )
            result[model_id] = build_model_release_readiness(
                model=model,
                evidence=item.model_dump(mode="json"),
            )
        return result

    def read_customer_model_release_evidence(*, request_id: str):
        """One authenticated snapshot per directory read; failures become blockers.

        This read never changes a commercial execution or restores a grant.
        The task endpoint still obtains and validates a separate live snapshot.
        """
        catalog, _ = read_relay_model_catalog(request_id=request_id, required=False)
        if catalog is None:
            return None
        evidence, _ = read_relay_model_release_evidence(request_id=request_id, required=False)
        if evidence is None:
            return None
        try:
            require_matching_relay_release_snapshot(catalog=catalog, evidence=evidence)
        except HTTPException:
            return None
        return evidence

    app.state.read_relay_model_release_evidence = (
        read_relay_model_release_evidence
    )
    app.state.read_relay_model_catalog = read_relay_model_catalog
    app.state.require_models_release_ready = require_models_release_ready
    app.state.read_customer_model_release_evidence = read_customer_model_release_evidence
    if relay_operations_client is None and all(
        (
            settings.relay_operations_base_url,
            settings.relay_tenant_id,
            settings.relay_operations_token,
            settings.relay_reconciliation_approval_key_id,
            settings.relay_reconciliation_approval_secret,
        )
    ):
        relay_operations_client = HttpxRelayOperationsClient(
            base_url=settings.relay_operations_base_url or "",
            tenant_id=settings.relay_tenant_id or "",
            operations_token=settings.relay_operations_token or "",
            approval_key_id=(settings.relay_reconciliation_approval_key_id or ""),
            approval_secret=(settings.relay_reconciliation_approval_secret or ""),
        )
    app.state.relay_operations_client = relay_operations_client
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or [],
        # The browser session contract stays credential-capable even while an
        # IdP is temporarily unavailable, so the frontend does not have to
        # change transport semantics when OIDC is enabled again.  The explicit
        # development-header-auth fixture is the sole non-cookie browser mode
        # and must not advertise credentialed CORS.
        allow_credentials=not settings.development_header_auth_enabled,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Auth-Required", "ETag"],
    )

    @app.middleware("http")
    async def authentication_no_store_middleware(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (
            path.startswith("/api/v1/auth/")
            or path.startswith("/api/v1/account")
            or path.startswith("/api/v1/invitations/")
            or (path.startswith("/api/v1/companies/") and "/invitations" in path)
            or "/owner-invitation" in path
            or path.startswith("/api/v1/platform-admin/users")
            or path.startswith("/api/v1/platform-admin/showcase")
            or (
                request.method == "POST"
                and path == "/api/v1/platform-admin/companies"
            )
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Referrer-Policy"] = "no-referrer"
        if path == "/api/v1/auth/callback":
            response.set_cookie(
                OIDC_STATE_COOKIE_NAME,
                "",
                max_age=0,
                expires=0,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
            )
        if getattr(request.state, "clear_invitation_handoff", False):
            response.set_cookie(
                INVITATION_HANDOFF_COOKIE_NAME,
                "",
                max_age=0,
                expires=0,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/api/v1/invitations",
            )
        return response

    @app.middleware("http")
    async def download_ticket_no_store_middleware(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (
            request.method == "GET"
            and path.startswith("/api/v1/companies/")
            and "/artifacts/" in path
            and (path.endswith("/download") or path.endswith("/preview"))
        ):
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.middleware("http")
    async def relay_native_console_no_store_middleware(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {
            "/api/v1/platform-admin/relay/native-console/open",
            "/api/v1/platform-admin/relay/provider-onboarding/open",
        }:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = normalize_request_id(
            request.headers.get("X-Request-ID")
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(DomainError)
    async def handle_domain_error(_, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "customer-platform"}

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "service": "customer-platform"}

    @app.get("/health/ready")
    def ready():
        try:
            with app.state.session_factory() as session:
                attest_platform_database_connection(
                    session.connection(), "platform-api"
                )
                session.execute(text("SELECT 1"))
                publishing_entitlement_in_use = session.scalar(
                    select(CompanyResourceGrant.id)
                    .join(
                        ResourceDefinition,
                        ResourceDefinition.id == CompanyResourceGrant.resource_id,
                    )
                    .where(
                        CompanyResourceGrant.enabled.is_(True),
                        ResourceDefinition.key == "feature.auto_publish",
                        ResourceDefinition.kind == ResourceKind.FEATURE,
                        ResourceDefinition.active.is_(True),
                    )
                    .limit(1)
                )
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "customer-platform",
                    "database": "unavailable",
                },
            )
        if (
            publishing_entitlement_in_use is not None
            and not settings.publishing_worker_enabled
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "customer-platform",
                    "database": "ok",
                    "publishing": "worker_disabled_with_active_grants",
                },
            )
        if (
            publishing_entitlement_in_use is not None
            and runtime_settings_are_protected(settings)
            and (
                not settings.publishing_adapters.strip()
                or not settings.publishing_media_resolver.strip()
            )
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "customer-platform",
                    "database": "ok",
                    "publishing": "production_adapter_or_media_resolver_missing",
                },
            )
        return {
            "status": "ready",
            "service": "customer-platform",
            "database": "ok",
            "publishing": (
                "configured"
                if publishing_entitlement_in_use is not None
                else "not_required"
            ),
        }

    def require_self_recharge_enabled() -> None:
        if runtime_settings_are_protected(settings):
            raise HTTPException(status_code=404, detail="Not found")

    @app.post(
        "/api/v1/bootstrap",
        response_model=BootstrapResponse,
        status_code=201,
        dependencies=[Depends(require_bootstrap_token)],
    )
    def bootstrap(
        body: BootstrapRequest,
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> BootstrapResponse:
        if not settings.enable_bootstrap:
            raise HTTPException(status_code=404, detail="初始化接口未启用")
        company, user, membership = CompanyService.bootstrap_company(
            session,
            company_name=body.company_name,
            owner_email=str(body.owner_email),
            owner_display_name=body.owner_display_name,
        )
        return BootstrapResponse(
            company_id=company.id,
            user_id=user.id,
            membership_id=membership.id,
        )

    @app.post(
        "/api/v1/bootstrap/platform-admin",
        response_model=PlatformAdminIdentityResponse,
        status_code=201,
        dependencies=[Depends(require_bootstrap_token)],
    )
    def bootstrap_platform_admin(
        body: BootstrapPlatformAdminRequest,
        request: Request,
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        if not settings.enable_bootstrap:
            raise HTTPException(status_code=404, detail="初始化接口未启用")
        user = PlatformAdminService.bootstrap_admin(
            session,
            email=str(body.email),
            display_name=body.display_name,
        )
        AuditService.append(
            session,
            actor_user_id=user.id,
            action="platform_admin.bootstrap",
            target_type="user",
            target_id=user.id,
            before_summary={},
            after_summary={"is_platform_admin": True},
            request_id=request.state.request_id,
        )
        if (
            not runtime_settings_are_protected(settings)
            and settings.development_header_auth_enabled
            and settings.enable_bootstrap
            and not settings.platform_owner_user_ids
            and not app.state.development_platform_owner_user_ids
        ):
            app.state.development_platform_owner_user_ids.add(user.id)
        return PlatformAdminIdentityResponse(user_id=user.id)

    @app.post(
        "/api/v1/bootstrap/models",
        response_model=DevModelSeedResponse,
        status_code=201,
        dependencies=[Depends(require_bootstrap_token)],
    )
    def seed_model(
        body: DevModelSeedRequest,
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        if not settings.enable_bootstrap:
            raise HTTPException(status_code=404, detail="初始化接口未启用")
        existing = session.scalar(
            select(ModelDefinition).where(ModelDefinition.slug == body.slug)
        )
        if existing is not None:
            return existing
        return ModelCatalogService.create_model(
            session,
            slug=body.slug,
            display_name=body.display_name,
            provider_key=body.provider_key,
            capability_version=body.capability_version,
            billing_mode=body.billing_mode,
            capabilities=[
                (capability.key, capability.config) for capability in body.capabilities
            ],
        )

    app.include_router(company_access_router)

    @app.get(
        "/api/v1/companies/{company_id}/models",
        response_model=list[AvailableModelResponse],
    )
    def available_models(
        company_id: str,
        request: Request,
        _: Annotated[TenantContext, Depends(require_permission("models.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelGrantService.list_available_models(
            session,
            company_id=company_id,
            require_relay_approval=(app.state.relay_client is not None),
            release_evidence=read_customer_model_release_evidence(
                request_id=request.state.request_id
            ),
        )

    @app.get(
        "/api/v1/companies/{company_id}/resources",
        response_model=list[AvailableResourceResponse],
    )
    def available_resources(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("resources.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ResourceGrantService.list_available(session, company_id=company_id)

    @app.get(
        "/api/v1/companies/{company_id}/model-grants",
        response_model=list[CompanyModelGrantResponse],
    )
    def list_model_grants(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("models.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelGrantService.list_company_grants(session, company_id=company_id)

    @app.get(
        "/api/v1/companies/{company_id}/wallet",
        response_model=WalletResponse,
    )
    def wallet(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("billing.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return _company_wallet_payload(session, company_id=company_id)

    @app.get(
        "/api/v1/companies/{company_id}/ledger",
        response_model=list[LedgerEntryResponse],
    )
    def ledger(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("billing.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(status_code=404, detail="公司不存在")
        if company.billing_version == 2:
            entries = [
                _point_ledger_payload(entry)
                for entry in session.scalars(
                    select(CompanyPointLedgerEntry)
                    .where(CompanyPointLedgerEntry.company_id == company_id)
                    .order_by(CompanyPointLedgerEntry.created_at.desc())
                ).all()
            ]
            entries.extend(
                _legacy_ledger_payload(entry)
                for entry in session.scalars(
                    select(LedgerEntry)
                    .where(LedgerEntry.company_id == company_id)
                    .order_by(LedgerEntry.created_at.desc())
                ).all()
            )
            entries.sort(
                key=lambda item: (item["created_at"], item["id"]),
                reverse=True,
            )
            return entries
        return [
            _legacy_ledger_payload(entry)
            for entry in session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.company_id == company_id)
                .order_by(LedgerEntry.created_at.desc())
            ).all()
        ]

    @app.post(
        "/api/v1/companies/{company_id}/wallet/recharge",
        response_model=WalletOperationResponse,
        dependencies=[Depends(require_self_recharge_enabled)],
    )
    def recharge(
        company_id: str,
        body: RechargeRequest,
        _: Annotated[TenantContext, Depends(require_permission("billing.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        account, entry, _ = WalletService.recharge(
            session,
            company_id=company_id,
            amount_cents=body.amount_cents,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
        return WalletOperationResponse(
            wallet=_company_wallet_payload(session, company_id=company_id),
            ledger_entry=_legacy_ledger_payload(entry),
        )

    @app.get(
        "/api/v1/companies/{company_id}/wallet/recharges",
        response_model=RechargeRecordPage,
    )
    def recharge_records(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("billing.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> RechargeRecordPage:
        _validate_report_time_range(start_time, end_time)
        (
            total,
            total_amount_cents,
            total_amount_points,
            items,
            unit_versions,
        ) = WalletService.funding_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return RechargeRecordPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            total_amount_cents=total_amount_cents,
            total_amount_points=total_amount_points,
            items=items,
        )

    app.include_router(create_company_input_assets_router(app=app, settings=settings))

    @app.get(
        "/api/v1/companies/{company_id}/publishing/readiness",
        response_model=PublishingReadinessResponse,
    )
    def publishing_readiness(
        company_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> PublishingReadinessResponse:
        permissions = PermissionService.effective_permissions(
            session, membership_id=context.membership_id
        )
        publish_permissions = {
            "publish.accounts.read",
            "publish.accounts.manage",
            "publish.jobs.read",
            "publish.jobs.manage",
        }
        if not permissions.intersection(publish_permissions):
            raise PermissionDeniedError(
                "至少需要一项发布账号或发布任务权限"
            )
        can_read_accounts = "publish.accounts.read" in permissions
        can_manage_accounts = "publish.accounts.manage" in permissions
        can_read_jobs = "publish.jobs.read" in permissions
        can_manage_jobs = "publish.jobs.manage" in permissions
        feature_enabled, entitlement_blockers = (
            PublishingService.entitlement_state(
                session, company_id=company_id
            )
        )
        has_side_effect_permission = can_manage_accounts or can_manage_jobs
        blocking_reasons = list(entitlement_blockers)
        if not has_side_effect_permission:
            blocking_reasons.append("no_publish_manage_permission")
        return PublishingReadinessResponse(
            feature_auto_publish_enabled=feature_enabled,
            can_read_accounts=can_read_accounts,
            can_manage_accounts=can_manage_accounts,
            can_read_jobs=can_read_jobs,
            can_manage_jobs=can_manage_jobs,
            side_effects_enabled=(
                feature_enabled and has_side_effect_permission
            ),
            account_side_effects_enabled=(
                feature_enabled and can_manage_accounts
            ),
            job_side_effects_enabled=(feature_enabled and can_manage_jobs),
            historical_read_enabled=(can_read_accounts or can_read_jobs),
            historical_safety_actions_enabled=has_side_effect_permission,
            blocking_reasons=blocking_reasons,
        )

    @app.get(
        "/api/v1/companies/{company_id}/publishing/connections",
        response_model=list[PublisherConnectionResponse],
    )
    def list_publisher_connections(
        company_id: str,
        _: Annotated[
            TenantContext, Depends(require_permission("publish.accounts.read"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return PublishingService.list_connections(session, company_id=company_id)

    @app.get(
        "/api/v1/companies/{company_id}/publishing/connections/oauth/providers",
        response_model=list[PublisherOAuthProviderResponse],
    )
    def list_publisher_oauth_providers(
        company_id: str,
        _: Annotated[
            TenantContext, Depends(require_permission("publish.accounts.read"))
        ],
    ):
        del company_id
        if (
            not settings.publishing_oauth_callback_url
            or not settings.publishing_oauth_success_url
        ):
            return []
        return [
            PublisherOAuthProviderResponse(
                provider=adapter.provider,
                display_name=adapter.oauth_display_name.strip(),
            )
            for adapter in app.state.publisher_registry.oauth_providers
        ]

    @app.post(
        "/api/v1/companies/{company_id}/publishing/connections/oauth/start",
        response_model=StartPublisherOAuthResponse,
    )
    def start_publisher_oauth(
        company_id: str,
        request: Request,
        body: StartPublisherOAuthRequest,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.accounts.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        PublishingService.require_entitlement(session, company_id=company_id)
        callback_url = settings.publishing_oauth_callback_url
        success_url = settings.publishing_oauth_success_url
        if not callback_url or not success_url:
            raise HTTPException(
                status_code=503,
                detail="Publisher OAuth callback is not configured",
            )
        try:
            adapter = app.state.publisher_registry.require_oauth(body.provider)
        except (KeyError, RuntimeError):
            raise HTTPException(
                status_code=404,
                detail="Publisher OAuth provider is not available",
            ) from None

        oauth_session, state = PublishingService.create_oauth_session(
            session,
            company_id=company_id,
            user_id=context.user_id,
            provider=adapter.provider,
            ttl_seconds=settings.publishing_oauth_state_ttl_seconds,
        )
        try:
            authorization_url = _validate_publisher_authorization_url(
                adapter.build_authorization_url(
                    state=state,
                    redirect_uri=callback_url,
                ),
                expected_state=state,
                production=runtime_settings_are_protected(settings),
            )
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Publisher OAuth provider is temporarily unavailable",
            ) from None

        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="publishing.connection.oauth_start",
            target_type="publisher_oauth_session",
            target_id=oauth_session.id,
            before_summary={},
            after_summary={
                "company_id": company_id,
                "provider": adapter.provider,
                "expires_at": oauth_session.expires_at.isoformat(),
            },
            request_id=request.state.request_id,
        )
        return StartPublisherOAuthResponse(
            provider=adapter.provider,
            authorization_url=authorization_url,
            expires_at=oauth_session.expires_at,
        )

    @app.get("/api/v1/publishing/oauth/callback")
    def complete_publisher_oauth(
        request: Request,
        state: Annotated[str, Query(min_length=16, max_length=256)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        code: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
        error: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    ):
        success_url = settings.publishing_oauth_success_url
        callback_url = settings.publishing_oauth_callback_url
        if not success_url or not callback_url:
            raise HTTPException(status_code=503, detail="Publisher OAuth is not configured")

        try:
            oauth_session = PublishingService.claim_oauth_session(
                session,
                state=state,
            )
        except DomainError:
            return RedirectResponse(
                _publisher_oauth_result_url(
                    success_url,
                    status="failed",
                    reason="invalid_or_expired_state",
                ),
                status_code=303,
            )

        if error or not code:
            AuditService.append(
                session,
                actor_user_id=oauth_session.created_by_user_id,
                action="publishing.connection.oauth_failed",
                target_type="publisher_oauth_session",
                target_id=oauth_session.id,
                before_summary={},
                after_summary={
                    "company_id": oauth_session.company_id,
                    "provider": oauth_session.provider,
                    "reason": "provider_denied" if error else "missing_code",
                },
                request_id=request.state.request_id,
            )
            return RedirectResponse(
                _publisher_oauth_result_url(
                    success_url,
                    status="failed",
                    provider=oauth_session.provider,
                    reason="provider_denied" if error else "missing_code",
                ),
                status_code=303,
            )

        try:
            membership = session.scalar(
                select(CompanyMembership).where(
                    CompanyMembership.company_id == oauth_session.company_id,
                    CompanyMembership.user_id == oauth_session.created_by_user_id,
                    CompanyMembership.status == MembershipStatus.ACTIVE,
                )
            )
            if membership is None:
                raise ConflictError("Publisher OAuth initiator is no longer a member")
            PermissionService.require(
                session,
                membership_id=membership.id,
                permission_code="publish.accounts.manage",
            )
            PublishingService.require_entitlement(
                session,
                company_id=oauth_session.company_id,
            )
        except DomainError:
            AuditService.append(
                session,
                actor_user_id=oauth_session.created_by_user_id,
                action="publishing.connection.oauth_failed",
                target_type="publisher_oauth_session",
                target_id=oauth_session.id,
                before_summary={},
                after_summary={
                    "company_id": oauth_session.company_id,
                    "provider": oauth_session.provider,
                    "reason": "authorization_revoked",
                },
                request_id=request.state.request_id,
            )
            return RedirectResponse(
                _publisher_oauth_result_url(
                    success_url,
                    status="failed",
                    provider=oauth_session.provider,
                    reason="authorization_revoked",
                ),
                status_code=303,
            )

        try:
            adapter = app.state.publisher_registry.require_oauth(
                oauth_session.provider
            )
            grant = adapter.exchange_authorization_code(
                code=code,
                redirect_uri=callback_url,
            )
            connection, created = PublishingService.complete_oauth_connection(
                session,
                oauth_session=oauth_session,
                grant=grant,
            )
        except Exception:
            AuditService.append(
                session,
                actor_user_id=oauth_session.created_by_user_id,
                action="publishing.connection.oauth_failed",
                target_type="publisher_oauth_session",
                target_id=oauth_session.id,
                before_summary={},
                after_summary={
                    "company_id": oauth_session.company_id,
                    "provider": oauth_session.provider,
                    "reason": "exchange_failed",
                },
                request_id=request.state.request_id,
            )
            return RedirectResponse(
                _publisher_oauth_result_url(
                    success_url,
                    status="failed",
                    provider=oauth_session.provider,
                    reason="exchange_failed",
                ),
                status_code=303,
            )

        AuditService.append(
            session,
            actor_user_id=oauth_session.created_by_user_id,
            action=(
                "publishing.connection.oauth_create"
                if created
                else "publishing.connection.oauth_refresh"
            ),
            target_type="publisher_connection",
            target_id=connection.id,
            before_summary={},
            after_summary=_publisher_connection_audit_summary(connection),
            request_id=request.state.request_id,
        )
        return RedirectResponse(
            _publisher_oauth_result_url(
                success_url,
                status="connected",
                provider=oauth_session.provider,
            ),
            status_code=303,
        )

    @app.post(
        "/api/v1/companies/{company_id}/publishing/connections",
        response_model=PublisherConnectionResponse,
        status_code=201,
    )
    def create_dev_publisher_connection(
        company_id: str,
        request: Request,
        body: CreateDevPublisherConnectionRequest,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.accounts.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        connection = PublishingService.create_dev_connection(
            session,
            company_id=company_id,
            user_id=context.user_id,
            provider=body.provider,
            display_name=body.display_name,
            environment=settings.environment,
            mock_enabled=settings.publishing_mock_enabled,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="publishing.connection.create",
            target_type="publisher_connection",
            target_id=connection.id,
            before_summary={},
            after_summary=_publisher_connection_audit_summary(connection),
            request_id=request.state.request_id,
        )
        return connection

    @app.delete(
        "/api/v1/companies/{company_id}/publishing/connections/{connection_id}",
        response_model=PublisherConnectionResponse,
    )
    def disable_publisher_connection(
        company_id: str,
        connection_id: str,
        request: Request,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.accounts.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        connection = PublishingService.get_connection_for_company(
            session,
            company_id=company_id,
            connection_id=connection_id,
        )
        before = _publisher_connection_audit_summary(connection)
        connection, changed = PublishingService.disable_connection(
            session,
            company_id=company_id,
            connection_id=connection_id,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="publishing.connection.disable",
                target_type="publisher_connection",
                target_id=connection.id,
                before_summary=before,
                after_summary=_publisher_connection_audit_summary(connection),
                request_id=request.state.request_id,
            )
        return connection

    @app.get(
        "/api/v1/companies/{company_id}/publishing/jobs",
        response_model=PublicationJobPage,
    )
    def list_publication_jobs(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("publish.jobs.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        status: PublicationJobStatus | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ):
        total, items = PublishingService.list_jobs_page(
            session,
            company_id=company_id,
            status=status,
            page=page,
            page_size=page_size,
        )
        return PublicationJobPage(
            page=page, page_size=page_size, total=total, items=items
        )

    @app.post(
        "/api/v1/companies/{company_id}/publishing/jobs",
        response_model=PublicationJobResponse,
        status_code=201,
    )
    def create_publication_job(
        company_id: str,
        request: Request,
        body: CreatePublicationJobRequest,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.jobs.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        job, created = PublishingService.create_job(
            session,
            company_id=company_id,
            user_id=context.user_id,
            artifact_id=body.artifact_id,
            connection_id=body.connection_id,
            idempotency_key=body.idempotency_key,
            title=body.title,
            caption=body.caption,
            scheduled_at=body.scheduled_at,
            timezone_name=body.timezone,
            environment=settings.environment,
            allow_company_artifacts=_can_publish_company_artifacts(
                session, context=context
            ),
        )
        if created:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="publishing.job.create",
                target_type="publication_job",
                target_id=job.id,
                before_summary={},
                after_summary=_publication_job_audit_summary(job),
                request_id=request.state.request_id,
            )
        return job

    @app.post(
        "/api/v1/platform-admin/companies/{company_id}/publishing/jobs",
        response_model=PublicationJobResponse,
        status_code=201,
    )
    def admin_create_publication_job(
        company_id: str,
        request: Request,
        body: CreatePublicationJobRequest,
        context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        job, created = PublishingService.create_job(
            session,
            company_id=company_id,
            user_id=context.user_id,
            artifact_id=body.artifact_id,
            connection_id=body.connection_id,
            idempotency_key=body.idempotency_key,
            title=body.title,
            caption=body.caption,
            scheduled_at=body.scheduled_at,
            timezone_name=body.timezone,
            environment=settings.environment,
            allow_company_artifacts=True,
        )
        if created:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="publishing.job.create",
                target_type="publication_job",
                target_id=job.id,
                before_summary={},
                after_summary=_publication_job_audit_summary(job),
                request_id=request.state.request_id,
            )
        return job

    @app.get(
        "/api/v1/companies/{company_id}/publishing/jobs/{job_id}",
        response_model=PublicationJobDetailResponse,
    )
    def get_publication_job(
        company_id: str,
        job_id: str,
        _: Annotated[TenantContext, Depends(require_permission("publish.jobs.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return PublishingService.job_detail(
            session, company_id=company_id, job_id=job_id
        )

    @app.post(
        "/api/v1/companies/{company_id}/publishing/jobs/{job_id}/approve",
        response_model=PublicationJobResponse,
    )
    def approve_publication_job(
        company_id: str,
        job_id: str,
        request: Request,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.jobs.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        job = PublishingService.get_job(session, company_id=company_id, job_id=job_id)
        before = _publication_job_audit_summary(job)
        job = PublishingService.approve_job(
            session,
            company_id=company_id,
            job_id=job_id,
            actor_user_id=context.user_id,
            environment=settings.environment,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="publishing.job.approve",
            target_type="publication_job",
            target_id=job.id,
            before_summary=before,
            after_summary=_publication_job_audit_summary(job),
            request_id=request.state.request_id,
        )
        return job

    @app.post(
        "/api/v1/companies/{company_id}/publishing/jobs/{job_id}/cancel",
        response_model=PublicationJobResponse,
    )
    def cancel_publication_job(
        company_id: str,
        job_id: str,
        request: Request,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.jobs.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        job = PublishingService.get_job(session, company_id=company_id, job_id=job_id)
        before = _publication_job_audit_summary(job)
        job = PublishingService.cancel_job(
            session,
            company_id=company_id,
            job_id=job_id,
            actor_user_id=context.user_id,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="publishing.job.cancel",
            target_type="publication_job",
            target_id=job.id,
            before_summary=before,
            after_summary=_publication_job_audit_summary(job),
            request_id=request.state.request_id,
        )
        return job

    @app.post(
        "/api/v1/companies/{company_id}/publishing/jobs/{job_id}/retry",
        response_model=PublicationJobResponse,
    )
    def retry_publication_job(
        company_id: str,
        job_id: str,
        request: Request,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.jobs.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        job = PublishingService.get_job(session, company_id=company_id, job_id=job_id)
        before = _publication_job_audit_summary(job)
        job = PublishingService.retry_job(
            session,
            company_id=company_id,
            job_id=job_id,
            environment=settings.environment,
            max_attempts=settings.publishing_max_attempts,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="publishing.job.retry",
            target_type="publication_job",
            target_id=job.id,
            before_summary=before,
            after_summary=_publication_job_audit_summary(job),
            request_id=request.state.request_id,
        )
        return job

    @app.post(
        "/api/v1/companies/{company_id}/publishing/jobs/{job_id}/reconcile",
        response_model=PublicationJobResponse,
    )
    def reconcile_publication_job(
        company_id: str,
        job_id: str,
        request: Request,
        body: ReconcilePublicationJobRequest,
        context: Annotated[
            TenantContext, Depends(require_permission("publish.jobs.manage"))
        ],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        job = PublishingService.get_job(session, company_id=company_id, job_id=job_id)
        before = _publication_job_audit_summary(job)
        job, changed = PublishingService.reconcile_unknown_job(
            session,
            company_id=company_id,
            job_id=job_id,
            outcome=body.outcome,
            external_post_id=body.external_post_id,
            external_post_url=(
                str(body.external_post_url)
                if body.external_post_url is not None
                else None
            ),
            error_code=body.error_code,
            error_message=body.error_message,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="publishing.job.reconcile",
                target_type="publication_job",
                target_id=job.id,
                before_summary=before,
                after_summary=_publication_job_audit_summary(job),
                request_id=request.state.request_id,
            )
        return job

    @app.post(
        "/api/v1/companies/{company_id}/tasks",
        response_model=TaskResponse,
        status_code=201,
    )
    def create_task(
        company_id: str,
        request: Request,
        body: CreateTaskRequest,
        context: Annotated[TenantContext, Depends(require_permission("tasks.create"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        replay_payload = DirectorShotPackageService.canonicalize_task_payload(
            body.request_payload
        )
        replay_payload = InputAssetService.canonicalize_task_payload(
            replay_payload
        )
        replay = TaskService.idempotent_replay(
            session,
            company_id=company_id,
            user_id=context.user_id,
            model_id=body.model_id,
            request_payload=replay_payload,
            idempotency_key=body.idempotency_key,
        )
        if replay is not None:
            return TaskService.response_payload(session, replay)
        commercial_readiness = (
            require_models_release_ready(
                session, model_ids={body.model_id},
                request_id=request.state.request_id,
            ).get(body.model_id)
            if CommercialPricingPolicy.task_needs_live_evidence(session, model_id=body.model_id)
            else None
        )
        normalized_payload, input_assets = InputAssetService.normalize_task_payload(
            session,
            company_id=company_id,
            request_payload=replay_payload,
        )
        director_shot_package = DirectorShotPackageService.require_for_task(
            session,
            company_id=company_id,
            personal_workspace_id=None,
            request_payload=normalized_payload,
        )
        relay_affinity = app.state.relay_backend_registry.default_affinity
        task, created = TaskService.create(
            session,
            company_id=company_id,
            user_id=context.user_id,
            model_id=body.model_id,
            request_payload=normalized_payload,
            idempotency_key=body.idempotency_key,
            expected_capability_version=body.expected_capability_version,
            expected_quote_revision=body.expected_quote_revision,
            require_quote_revision=runtime_settings_are_protected(settings),
            require_relay_capability_revision=(app.state.relay_client is not None),
            relay_backend_id=relay_affinity.backend_id,
            relay_contract_revision=relay_affinity.contract_revision,
            expected_commercial_release_snapshot=(
                commercial_readiness.expected_snapshot
                if commercial_readiness is not None else None
            ),
        )
        if not created:
            return TaskService.response_payload(session, task)
        InputAssetService.link_task(session, task_id=task.id, assets=input_assets)
        DirectorShotPackageService.link_task(
            session,
            task=task,
            package=director_shot_package,
        )
        WalletService.reserve(
            session,
            company_id=company_id,
            task_id=task.id,
            amount_cents=task.quote_cents,
            idempotency_key=body.idempotency_key,
        )
        model = session.get(ModelDefinition, task.model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="模型不存在")
        RelayOutboxService.enqueue(
            session,
            task=task,
            model=model,
            expected_commercial_release_snapshot=(
                commercial_readiness.expected_snapshot if commercial_readiness is not None else None
            ),
            request_id=request.state.request_id,
            # The outbox stores private asset identities at task creation.
            # The dispatcher signs them once immediately before its first POST.
            resolved_assets=[],
            director_shot=DirectorShotPackageService.relay_input(
                director_shot_package
            ),
            director_motion=DirectorShotPackageService.motion_relay_input(
                normalized_payload
            ),
            callback_url=relay_callback_url_for_backend(
                settings.relay_callback_public_url,
                backend_id=task.relay_backend_id,
            ),
        )
        return TaskService.response_payload(session, task)

    @app.get(
        "/api/v1/companies/{company_id}/tasks",
        response_model=list[TaskResponse],
    )
    def list_tasks(
        company_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        scope: Literal["mine", "company"] = "mine",
        status: TaskStatus | None = None,
        model_id: str | None = None,
        limit: int = Query(default=200, ge=1, le=500),
    ):
        tasks = TaskService.list_company_tasks(
            session,
            company_id=company_id,
            visible_user_id=_visible_user_id_for_scope(
                session, context=context, scope=scope
            ),
            status=status,
            model_id=model_id,
            limit=limit,
        )
        return TaskService.response_payloads(session, tasks)

    @app.get(
        "/api/v1/companies/{company_id}/tasks/{task_id}",
        response_model=TaskResponse,
    )
    def get_task(
        company_id: str,
        task_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        scope: Literal["mine", "company"] = "mine",
    ):
        task = TaskService.get_company_task(
            session,
            company_id=company_id,
            task_id=task_id,
            visible_user_id=_visible_user_id_for_scope(
                session, context=context, scope=scope
            ),
        )
        return TaskService.response_payload(session, task)

    @app.post(
        "/api/v1/companies/{company_id}/tasks/{task_id}/cancel",
        response_model=TaskResponse,
    )
    def cancel_task(
        company_id: str,
        task_id: str,
        request: Request,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        result = GenerationCancellationService.cancel_unsubmitted(
            session,
            company_id=company_id,
            task_id=task_id,
            actor_user_id=context.user_id,
        )
        if not result.replayed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="generation.task.cancel",
                target_type="generation_task",
                target_id=result.task.id,
                before_summary=result.before_summary,
                after_summary=result.after_summary,
                request_id=request.state.request_id,
            )
        return TaskService.response_payload(session, result.task)

    @app.get(
        "/api/v1/companies/{company_id}/task-history",
        response_model=TaskHistoryPage,
    )
    def task_history(
        company_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        scope: Literal["mine", "company"] = "mine",
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        media_type: Literal["image", "video"] | None = None,
        query: str | None = Query(default=None, min_length=1, max_length=200),
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> TaskHistoryPage:
        _validate_report_time_range(start_time, end_time)
        visible_user_id = _visible_user_id_for_scope(
            session, context=context, scope=scope
        )
        if (
            visible_user_id is not None
            and employee_user_id is not None
            and employee_user_id != visible_user_id
        ):
            raise HTTPException(status_code=403, detail="不能查询其他员工的任务")
        total, items, unit_versions = TaskArtifactService.task_history_page(
            session,
            company_id=company_id,
            visible_user_id=visible_user_id,
            page=page,
            page_size=page_size,
            employee_user_id=employee_user_id,
            model_id=model_id,
            status=status,
            media_type=media_type,
            query=query,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return TaskHistoryPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            items=items,
        )

    @app.get(
        "/api/v1/companies/{company_id}/artworks",
        response_model=ArtworkPage,
    )
    def artworks(
        company_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        scope: Literal["mine", "company"] = "mine",
        employee_user_id: str | None = None,
        model_id: str | None = None,
        media_type: Literal["image", "video"] | None = None,
        downloaded: bool | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ArtworkPage:
        _validate_report_time_range(start_time, end_time)
        visible_user_id = _visible_user_id_for_scope(
            session, context=context, scope=scope
        )
        if (
            visible_user_id is not None
            and employee_user_id is not None
            and employee_user_id != visible_user_id
        ):
            raise HTTPException(status_code=403, detail="不能查询其他员工的作品")
        total, items, unit_versions = TaskArtifactService.artwork_page(
            session,
            company_id=company_id,
            visible_user_id=visible_user_id,
            page=page,
            page_size=page_size,
            employee_user_id=employee_user_id,
            model_id=model_id,
            media_type=media_type,
            downloaded=downloaded,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return ArtworkPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            items=items,
        )

    @app.get(
        "/api/v1/companies/{company_id}/tasks/{task_id}/artifacts/{asset_id}/preview",
        response_model=ArtifactPreviewResponse,
    )
    def get_task_artifact_preview(
        company_id: str,
        task_id: str,
        asset_id: str,
        request: Request,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        scope: Literal["mine", "company"] = "mine",
    ) -> ArtifactPreviewResponse:
        """Issue an inline-safe Relay URL without recording a download.

        Preview access deliberately bypasses the Download Gateway and never
        appends a DownloadRecord: rendering media is not evidence that the user
        initiated or completed a download. Authorization and storage binding
        validation remain identical to the durable download boundary.
        """

        task = TaskService.get_company_task(
            session,
            company_id=company_id,
            task_id=task_id,
            visible_user_id=_visible_user_id_for_scope(
                session, context=context, scope=scope
            ),
        )
        artifact = session.scalar(
            select(TaskArtifact).where(
                TaskArtifact.company_id == company_id,
                TaskArtifact.task_id == task_id,
                TaskArtifact.asset_id == asset_id,
            )
        )
        if (
            task.status != TaskStatus.SUCCEEDED
            or not task.relay_job_id
            or artifact is None
        ):
            raise HTTPException(status_code=404, detail="Artifact does not exist")

        inline_content_types = {
            "image": {"image/jpeg", "image/png", "image/webp"},
            "video": {"video/mp4", "video/webm"},
        }
        if artifact.content_type not in inline_content_types.get(
            artifact.media_type, set()
        ):
            raise HTTPException(
                status_code=415,
                detail="Artifact media type is not safe for inline preview",
            )

        client = app.state.resolve_task_relay_client(task)
        try:
            preview = client.get_artifact_download(
                task.relay_job_id,
                asset_id,
                request_id=request.state.request_id,
            )
            # A structured storage binding is required even outside production.
            # The legacy unbound response exists only for migration-era download
            # compatibility and is not safe enough for a browser preview URL.
            validate_bound_artifact_download(
                preview,
                production=runtime_settings_are_protected(settings),
                allow_legacy=False,
            )
            return ArtifactPreviewResponse(
                url=preview.url,
                expires_seconds=preview.expires_seconds,
                media_type=artifact.media_type,
                content_type=artifact.content_type,
            )
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503,
                detail="Artifact preview is temporarily unavailable",
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502,
                detail="Relay rejected the artifact preview request",
            ) from exc

    @app.get(
        "/api/v1/companies/{company_id}/tasks/{task_id}/artifacts/{asset_id}/download",
        response_model=ArtifactDownloadResponse,
    )
    def get_task_artifact_download(
        company_id: str,
        task_id: str,
        asset_id: str,
        request: Request,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        scope: Literal["mine", "company"] = "mine",
    ):
        task = TaskService.get_company_task(
            session,
            company_id=company_id,
            task_id=task_id,
            visible_user_id=_visible_user_id_for_scope(
                session, context=context, scope=scope
            ),
        )
        artifact = session.scalar(
            select(TaskArtifact).where(
                TaskArtifact.company_id == company_id,
                TaskArtifact.task_id == task_id,
                TaskArtifact.asset_id == asset_id,
            )
        )
        if (
            task.status != TaskStatus.SUCCEEDED
            or not task.relay_job_id
            or artifact is None
        ):
            raise HTTPException(status_code=404, detail="Artifact does not exist")
        client = app.state.resolve_task_relay_client(task)
        try:
            gateway_client = app.state.download_gateway_client
            gateway_registration_service = None
            relay_job_id = task.relay_job_id
            artifact_size_bytes = artifact.size_bytes
            artifact_sha256 = artifact.sha256
            platform_request_id = request.state.request_id
            if gateway_client is not None:
                gateway_registration_service = (
                    app.state.resolve_download_gateway_registration_service()
                )
                if gateway_registration_service is None:
                    raise DownloadGatewayTemporaryError(
                        "Download Gateway registration service is not configured"
                    )
                # The durable registration service owns its own short
                # transactions. Release this read-only request transaction
                # before it persists the pre-HTTP attempt (also required by
                # SQLite's single-connection test harness).
                session.rollback()
                existing = gateway_registration_service.process_existing(
                    company_id=company_id,
                    task_id=task_id,
                    asset_id=asset_id,
                    requested_by_user_id=context.user_id,
                    platform_request_id=platform_request_id,
                )
                if existing is not None:
                    if existing.ticket is None or existing.download_record_id is None:
                        if existing.status == "reconciled_expired":
                            raise HTTPException(
                                status_code=410,
                                detail="Download Gateway ticket has expired",
                            )
                        if existing.status == "dead":
                            raise DownloadGatewayPermanentError(
                                "Download Gateway registration is dead-lettered"
                            )
                        raise DownloadGatewayTemporaryError(
                            "Download Gateway registration awaits reconciliation"
                        )
                    return ArtifactDownloadResponse(
                        url=existing.ticket.ticket_url,
                        expires_seconds=existing.ticket.expires_seconds,
                        download_record_id=existing.download_record_id,
                        download_status="issued",
                    )
            download = client.get_artifact_download(
                relay_job_id,
                asset_id,
                request_id=platform_request_id,
            )
            storage_binding = validate_bound_artifact_download(
                download,
                production=runtime_settings_are_protected(settings),
                allow_legacy=(settings.allow_legacy_relay_artifact_download_response),
            )
            source_url = str(download.url)
            record_id = str(uuid4())
            if gateway_client is not None:
                if storage_binding is None:
                    raise RelayPermanentError(
                        "Download Gateway requires a Relay storage binding"
                    )
                assert gateway_registration_service is not None
                attempt_id = gateway_registration_service.prepare(
                    company_id=company_id,
                    task_id=task_id,
                    asset_id=asset_id,
                    requested_by_user_id=context.user_id,
                    platform_request_id=platform_request_id,
                    expected_size_bytes=artifact_size_bytes,
                    artifact_sha256=artifact_sha256,
                    source_url=source_url,
                    storage_binding=storage_binding,
                )
                result = gateway_registration_service.process_attempt(
                    attempt_id,
                    return_ticket=True,
                )
                if result.ticket is None or result.download_record_id is None:
                    if result.status == "reconciled_expired":
                        raise HTTPException(
                            status_code=410,
                            detail="Download Gateway ticket has expired",
                        )
                    if result.status == "dead":
                        raise DownloadGatewayPermanentError(
                            "Download Gateway registration is dead-lettered"
                        )
                    raise DownloadGatewayTemporaryError(
                        "Download Gateway registration awaits reconciliation"
                    )
                return ArtifactDownloadResponse(
                    url=result.ticket.ticket_url,
                    expires_seconds=result.ticket.expires_seconds,
                    download_record_id=result.download_record_id,
                    download_status="issued",
                )
            if runtime_settings_are_protected(settings):
                raise DownloadGatewayTemporaryError(
                    "Download Gateway is not configured"
                )
            record = DownloadRecordService.append(
                session,
                record_id=record_id,
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                requested_by_user_id=context.user_id,
                expires_seconds=download.expires_seconds,
                expires_at=(
                    storage_binding.expires_at if storage_binding is not None else None
                ),
                request_id=request.state.request_id,
                storage_binding=storage_binding,
                source_url_sha256=(
                    storage_binding.url_sha256 if storage_binding is not None else None
                ),
            )
            return ArtifactDownloadResponse(
                url=download.url,
                expires_seconds=download.expires_seconds,
                download_record_id=record.id,
                download_status="issued",
            )
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503,
                detail="Artifact download is temporarily unavailable",
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502,
                detail="Relay rejected the artifact download request",
            ) from exc
        except DownloadGatewayTemporaryError as exc:
            raise HTTPException(
                status_code=503,
                detail="Artifact download Gateway is temporarily unavailable",
            ) from exc
        except DownloadGatewayPermanentError as exc:
            raise HTTPException(
                status_code=502,
                detail="Artifact download Gateway rejected the registration",
            ) from exc

    @app.post(
        "/api/v1/companies/{company_id}/tasks/{task_id}/artifacts/{asset_id}/input-asset",
        response_model=PromotedInputAssetResponse,
        status_code=201,
    )
    def promote_task_artifact_to_input_asset(
        company_id: str,
        task_id: str,
        asset_id: str,
        body: PromoteTaskArtifactRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        scope: Literal["mine", "company"] = "mine",
    ):
        task = TaskService.get_company_task(
            session,
            company_id=company_id,
            task_id=task_id,
            visible_user_id=_visible_user_id_for_scope(
                session, context=context, scope=scope
            ),
        )
        artifact = session.scalar(
            select(TaskArtifact).where(
                TaskArtifact.company_id == company_id,
                TaskArtifact.task_id == task_id,
                TaskArtifact.asset_id == asset_id,
            )
        )
        if (
            task.status != TaskStatus.SUCCEEDED
            or not task.relay_job_id
            or artifact is None
        ):
            raise HTTPException(status_code=404, detail="Artifact does not exist")

        # A replay is served without minting another Relay URL or touching
        # storage. Different-source reuse is rejected by the service.
        existing = InputAssetService.get_artifact_promotion_replay(
            session,
            company_id=company_id,
            user_id=context.user_id,
            idempotency_key=body.idempotency_key,
            source_task_artifact_id=artifact.id,
        )
        if existing is not None:
            return existing

        client = app.state.resolve_task_relay_client(task)
        try:
            download = client.get_artifact_download(
                task.relay_job_id,
                asset_id,
                request_id=request.state.request_id,
            )
            storage_binding = validate_bound_artifact_download(
                download,
                production=runtime_settings_are_protected(settings),
                allow_legacy=(settings.allow_legacy_relay_artifact_download_response),
            )
            if storage_binding is None:
                # Legacy artifact responses are available only outside
                # production. Restrict their server-side fetch to the local
                # development Relay so a compromised response cannot turn
                # this copy operation into an HTTPS SSRF primitive.
                allowed_hosts = {"localhost", "127.0.0.1", "::1"}
            else:
                allowed_hosts = {
                    storage_binding.endpoint_host,
                    (f"{storage_binding.bucket}." f"{storage_binding.endpoint_host}"),
                }
            content_source = HttpArtifactContentSource(
                str(download.url),
                timeout_seconds=(settings.artifact_promotion_download_timeout_seconds),
                allowed_hosts=allowed_hosts,
            )
            promoted, created = InputAssetService.promote_task_artifact(
                session,
                store=app.state.input_asset_store,
                artifact=artifact,
                user_id=context.user_id,
                idempotency_key=body.idempotency_key,
                content_source=content_source,
                max_bytes=settings.input_asset_max_bytes,
            )
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503,
                detail="Artifact copy is temporarily unavailable",
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502,
                detail="Artifact copy could not be verified",
            ) from exc
        if created:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="task.artifact.promote_to_input_asset",
                target_type="input_asset",
                target_id=promoted.id,
                before_summary={
                    "task_id": task.id,
                    "task_artifact_id": artifact.id,
                    "asset_id": artifact.asset_id,
                },
                after_summary={
                    "input_asset_id": promoted.id,
                    "media_type": promoted.media_type,
                    "size_bytes": promoted.size_bytes,
                    "sha256": promoted.sha256,
                },
                request_id=request.state.request_id,
            )
        return promoted

    @app.get(
        "/api/v1/companies/{company_id}/download-records",
        response_model=DownloadRecordPage,
    )
    def list_download_records(
        company_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        scope: Literal["mine", "company"] = "mine",
        task_id: str | None = None,
        asset_id: str | None = None,
        employee_user_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> DownloadRecordPage:
        _validate_report_time_range(start_time, end_time)
        visible_user_id = _visible_user_id_for_scope(
            session, context=context, scope=scope
        )
        if (
            visible_user_id is not None
            and employee_user_id is not None
            and employee_user_id != visible_user_id
        ):
            raise HTTPException(status_code=403, detail="不能查询其他员工的下载记录")
        total, items = DownloadRecordService.page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            task_id=task_id,
            asset_id=asset_id,
            requested_by_user_id=visible_user_id or employee_user_id,
            start_time=start_time,
            end_time=end_time,
        )
        return DownloadRecordPage(
            page=page, page_size=page_size, total=total, items=items
        )

    async def _confirm_artifact_download(
        request: Request,
        *,
        source: DownloadCompletionSource,
        session: Session,
    ) -> DownloadCompletionResponse:
        verifier = app.state.download_completion_event_verifier
        if verifier is None:
            raise HTTPException(
                status_code=503,
                detail="Download-completion verifier is not configured",
            )
        body, evidence = verifier.verify(
            await _read_limited_request_body(request),
            source=source,
            event_id=request.headers.get("X-Download-Event-ID"),
            timestamp=request.headers.get("X-Download-Timestamp"),
            signature=request.headers.get("X-Download-Signature"),
        )
        if isinstance(body, EdgeGatewayDownloadCompletionRequest):
            source_evidence = {
                "gateway_request_id": body.gateway_request_id,
                "gateway_transfer_reference": body.gateway_transfer_reference,
            }
        elif isinstance(body, ObsAccessLogDownloadCompletionRequest):
            source_evidence = {
                "obs_bucket": body.obs_bucket,
                "obs_object_key": body.obs_object_key,
            }
            if body.obs_version_id is not None:
                source_evidence["obs_version_id"] = body.obs_version_id
            if body.obs_request_id is not None:
                source_evidence["obs_request_id"] = body.obs_request_id
        else:  # pragma: no cover - verifier returns only the two strict schemas
            raise HTTPException(
                status_code=422,
                detail="Download-completion payload source is invalid",
            )
        completion, _ = DownloadCompletionService.confirm(
            session,
            download_record_id=body.download_record_id,
            company_id=body.company_id,
            task_id=body.task_id,
            asset_id=body.asset_id,
            external_event_id=body.external_event_id,
            source=source,
            bytes_sent=body.bytes_sent,
            completed_at=body.completed_at,
            artifact_sha256=body.artifact_sha256,
            expected_size_bytes=body.expected_size_bytes,
            http_status=body.http_status,
            transfer_scope=body.transfer_scope,
            source_evidence=source_evidence,
            signed_event_id=evidence.event_id,
            signed_event_timestamp=evidence.event_timestamp,
            signed_payload_sha256=evidence.payload_sha256,
        )
        return completion

    @app.post(
        "/internal/artifact-download-completions/edge-gateway",
        response_model=DownloadCompletionResponse,
        status_code=201,
        include_in_schema=False,
    )
    async def confirm_edge_gateway_artifact_download(
        request: Request,
        _: Annotated[None, Depends(require_download_edge_completion_service)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> DownloadCompletionResponse:
        return await _confirm_artifact_download(
            request,
            source=DownloadCompletionSource.EDGE_GATEWAY,
            session=session,
        )

    @app.post(
        "/internal/artifact-download-completions/obs-access-log",
        response_model=DownloadCompletionResponse,
        status_code=201,
        include_in_schema=False,
    )
    async def confirm_obs_access_log_artifact_download(
        request: Request,
        _: Annotated[None, Depends(require_internal_service)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> DownloadCompletionResponse:
        return await _confirm_artifact_download(
            request,
            source=DownloadCompletionSource.OBS_ACCESS_LOG,
            session=session,
        )

    @app.get(
        "/api/v1/companies/{company_id}/reports/tasks",
        response_model=TaskReportPage,
    )
    def task_report(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("reports.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> TaskReportPage:
        _validate_report_time_range(start_time, end_time)
        (
            total,
            total_actual_cost_cents,
            total_actual_cost_points,
            items,
            unit_versions,
        ) = ReportService.task_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            employee_user_id=employee_user_id,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return TaskReportPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            total_actual_cost_cents=total_actual_cost_cents,
            total_actual_cost_points=total_actual_cost_points,
            items=items,
        )

    @app.get(
        "/api/v1/companies/{company_id}/reports/consumption",
        response_model=ConsumptionReportPage,
    )
    def consumption_report(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("reports.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ConsumptionReportPage:
        _validate_report_time_range(start_time, end_time)
        (
            total,
            total_amount_cents,
            total_amount_points,
            items,
            unit_versions,
        ) = ReportService.consumption_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            employee_user_id=employee_user_id,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return ConsumptionReportPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            total_amount_cents=total_amount_cents,
            total_amount_points=total_amount_points,
            items=items,
        )

    @app.get("/api/v1/companies/{company_id}/reports/tasks/export.csv")
    def export_task_report(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("reports.export"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Response:
        _validate_report_time_range(start_time, end_time)
        document = ReportService.task_export(
            session,
            company_id=company_id,
            employee_user_id=employee_user_id,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        return Response(
            content=document.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="task-report.csv"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/companies/{company_id}/reports/consumption/export.csv")
    def export_consumption_report(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("reports.export"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Response:
        _validate_report_time_range(start_time, end_time)
        document = ReportService.consumption_export(
            session,
            company_id=company_id,
            employee_user_id=employee_user_id,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        return Response(
            content=document.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    'attachment; filename="consumption-report.csv"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/api/v1/platform-admin/me",
        response_model=PlatformAdminMeResponse,
    )
    def platform_admin_me(
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        user = session.get(User, admin.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="平台管理员不存在")
        return PlatformAdminMeResponse(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_platform_admin=user.is_platform_admin,
            is_platform_owner=admin.is_platform_owner,
            permission_codes=(
                sorted(PLATFORM_ADMIN_PERMISSION_CODES)
                if admin.is_platform_owner
                else sorted(
                    PlatformAdminAccessService.effective_permissions(
                        session,
                        user_id=user.id,
                        platform_owner_user_ids=frozenset(
                            settings.platform_owner_user_ids
                        ),
                    )
                )
            ),
        )

    @app.post(
        "/api/v1/platform-admin/download-gateway-registration-attempts/"
        "{attempt_id}/reconcile"
    )
    def reconcile_download_gateway_registration_attempt(
        attempt_id: str,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> dict[str, str | bool | None]:
        service = app.state.resolve_download_gateway_registration_service()
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="Download Gateway registration service is not configured",
            )
        # Release the authentication dependency's read transaction before the
        # durable reconciler opens its independently committed transactions.
        session.rollback()
        try:
            result = service.reconcile(attempt_id)
        except DownloadGatewayTemporaryError as exc:
            raise HTTPException(
                status_code=503,
                detail="Download Gateway reconciliation is temporarily unavailable",
            ) from exc
        except DownloadGatewayPermanentError as exc:
            raise HTTPException(
                status_code=502,
                detail="Download Gateway reconciliation failed permanently",
            ) from exc
        return {
            "processed": result.processed,
            "attempt_id": result.attempt_id,
            "status": result.status,
            "download_record_id": result.download_record_id,
        }

    @app.get(
        "/api/v1/platform-admin/models",
        response_model=list[AdminModelResponse],
    )
    def admin_list_models(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelCatalogService.list_models(session)

    @app.get(
        "/api/v1/platform-admin/relay-models",
        response_model=RelayCapabilityAuditResponse,
    )
    def admin_relay_model_catalog(
        request: Request,
        response: Response,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        try:
            read = client.get_model_catalog(request_id=request.state.request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(status_code=502, detail="Relay 模型目录响应不完整")
        release_evidence, evidence_error = read_relay_model_release_evidence(
            request_id=request.state.request_id,
            required=False,
        )
        if release_evidence is not None:
            require_matching_relay_release_snapshot(
                catalog=read.catalog,
                evidence=release_evidence,
            )
        response.headers["Cache-Control"] = "private, no-store"
        audit = RelayCapabilityService.audit_catalog(
            session,
            catalog=read.catalog,
            release_evidence=release_evidence,
            release_evidence_error=evidence_error,
        )
        return {**audit, "etag": read.etag}

    @app.post(
        "/api/v1/platform-admin/relay-models/reconcile",
        response_model=RelayModelReconcileResponse,
    )
    def admin_reconcile_relay_model_catalog(
        request: Request,
        response: Response,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        try:
            read = client.get_model_catalog(
                request_id=request.state.request_id
            )
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应不完整"
            )

        release_evidence, evidence_error = read_relay_model_release_evidence(
            request_id=request.state.request_id,
            required=True,
        )
        assert release_evidence is not None
        require_matching_relay_release_snapshot(
            catalog=read.catalog,
            evidence=release_evidence,
        )
        reconciliation = RelayCatalogReconciliationService.reconcile(
            session,
            catalog=read.catalog,
            actor=ReconciliationAuditActor.user(admin.user_id),
            request_id=request.state.request_id,
            source="relay_catalog_reconcile",
            trigger="platform_admin_request",
        )
        audit = RelayCapabilityService.audit_catalog(
            session,
            catalog=read.catalog,
            release_evidence=release_evidence,
            release_evidence_error=evidence_error,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return {
            **audit,
            "etag": read.etag,
            "created_count": reconciliation.created_count,
            "synced_count": reconciliation.synced_count,
            "invalidated_count": reconciliation.invalidated_count,
            "unchanged_count": reconciliation.unchanged_count,
            "created_model_ids": list(reconciliation.created_model_ids),
            "synced_model_ids": list(reconciliation.synced_model_ids),
            "invalidated_model_ids": list(
                reconciliation.invalidated_model_ids
            ),
            "reconciliation_audit_id": (
                reconciliation.reconciliation_audit_id
            ),
        }

    @app.get(
        "/api/v1/platform-admin/model-commercial-releases",
        response_model=list[ModelCommercialReleasePlanResponse],
    )
    def admin_list_model_commercial_releases(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelCommercialReleaseService.list_plans(session)

    @app.put(
        "/api/v1/platform-admin/models/{model_id}/commercial-release-plan",
        response_model=ModelCommercialReleasePlanResponse,
    )
    def admin_approve_model_commercial_release_plan(
        model_id: str,
        body: ModelCommercialReleasePlanRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        try:
            read = client.get_model_catalog(request_id=request.state.request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(status_code=502, detail="Relay 模型目录响应不完整")
        release_evidence, _ = read_relay_model_release_evidence(
            request_id=request.state.request_id,
            required=True,
        )
        if release_evidence is None:  # pragma: no cover - required reader fails first
            raise HTTPException(status_code=503, detail="Relay 路由发布证据不可用")
        require_matching_relay_release_snapshot(
            catalog=read.catalog,
            evidence=release_evidence,
        )
        relay_model = RelayCapabilityService.relay_model(
            read.catalog,
            model_slug=ModelCatalogService.get_model(
                session, model_id=model_id
            ).slug,
        )
        RelayCapabilityService.require_customer_callable_model(relay_model)
        plan, _ = ModelCommercialReleaseService.approve_plan(
            session,
            model_id=model_id,
            expected_capability_version=body.expected_capability_version,
            expected_candidate_revision=body.expected_candidate_revision,
            expected_catalog_revision=body.expected_catalog_revision,
            expected_routing_release_sha256=body.expected_routing_release_sha256,
            provider_cost_currency=body.provider_cost_currency,
            provider_cost_formula=body.provider_cost_formula.model_dump(
                mode="json"
            ),
            provider_cost_evidence_kind=body.provider_cost_evidence_kind,
            provider_cost_evidence_reference=(
                body.provider_cost_evidence_reference
            ),
            provider_cost_evidence_sha256=(
                body.provider_cost_evidence_sha256
            ),
            provider_cost_effective_at=body.provider_cost_effective_at,
            fx_cny_micros_per_currency_unit=(
                body.fx_cny_micros_per_currency_unit
            ),
            fx_source=body.fx_source,
            fx_version=body.fx_version,
            fx_evidence_sha256=body.fx_evidence_sha256,
            fx_effective_at=body.fx_effective_at,
            personal_price_points=body.personal_price_points,
            enterprise_price_points=body.enterprise_price_points,
            personal_config_override=body.personal_config_override,
            enterprise_config_override=body.enterprise_config_override,
            approval_reason=body.approval_reason,
            idempotency_key=body.idempotency_key,
            approved_by_user_id=admin.user_id,
            request_id=request.state.request_id,
            release_evidence=release_evidence,
            supersedes_plan_id=body.supersedes_plan_id,
        )
        return plan

    @app.post(
        "/api/v1/platform-admin/model-commercial-releases/reconcile",
        response_model=ModelCommercialReleaseReconcileResponse,
    )
    def admin_reconcile_model_commercial_releases(
        request: Request,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        try:
            read = client.get_model_catalog(request_id=request.state.request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(status_code=502, detail="Relay 模型目录响应不完整")
        release_evidence, _ = read_relay_model_release_evidence(
            request_id=request.state.request_id,
            required=True,
        )
        assert release_evidence is not None
        require_matching_relay_release_snapshot(
            catalog=read.catalog,
            evidence=release_evidence,
        )
        # Catalog materialization remains a separate fail-closed step. It does
        # not approve or distribute anything by itself.
        locked_companies = ModelCommercialReleaseService.lock_distribution_companies(session)
        RelayCatalogReconciliationService.reconcile(
            session,
            catalog=read.catalog,
            actor=ReconciliationAuditActor.system("relay-catalog-sync"),
            request_id=request.state.request_id,
            source="commercial_release_reconcile",
            trigger="platform_admin_request",
        )
        result = ModelCommercialReleaseService.reconcile(
            session,
            locked_companies=locked_companies,
            catalog=read.catalog,
            release_evidence=release_evidence,
            request_id=request.state.request_id,
            trigger="platform_admin_request",
        )
        return {
            "catalog_revision": result.catalog_revision,
            "planned_count": result.planned_count,
            "released_count": result.released_count,
            "blocked_count": result.blocked_count,
            "unchanged_count": result.unchanged_count,
            "items": list(result.items),
        }

    # ------------------------------------------------------------------
    # Commercial release batches
    #
    # One approval action covers several models, and one activation
    # transaction releases them together or not at all.  Approval semantics
    # are unchanged: every plan still comes from approve_plan().
    # ------------------------------------------------------------------

    def _batch_relay_snapshot(request_id: str):
        """Read the live Relay catalog plus its matching release evidence."""

        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        try:
            read = client.get_model_catalog(request_id=request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(status_code=502, detail="Relay 模型目录响应不完整")
        release_evidence, _ = read_relay_model_release_evidence(
            request_id=request_id,
            required=True,
        )
        if release_evidence is None:
            raise HTTPException(status_code=503, detail="Relay 路由发布证据不可用")
        require_matching_relay_release_snapshot(
            catalog=read.catalog,
            evidence=release_evidence,
        )
        return read.catalog, release_evidence

    def _batch_relay_snapshot_reader(request_id: str):
        """Resolve the Relay snapshot on first use instead of up front.

        Batch endpoints must be able to reject an unknown batch id (404) or a
        malformed item list (409) without a Relay round trip.  Resolving
        eagerly would report a Relay outage for what is really a client error.
        """

        resolved: list[tuple[object, object]] = []

        def read():
            if not resolved:
                resolved.append(_batch_relay_snapshot(request_id))
            return resolved[0]

        return read

    @app.post(
        "/api/v1/platform-admin/model-commercial-release-batches/preflight",
        response_model=ModelCommercialReleaseBatchPreflightResponse,
    )
    def admin_preflight_model_commercial_release_batch(
        body: ModelCommercialReleaseBatchPreflightRequest,
        request: Request,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelCommercialReleaseService.preflight_batch(
            session,
            model_ids=body.model_ids,
            relay_snapshot=_batch_relay_snapshot_reader(request.state.request_id),
        )

    @app.get(
        "/api/v1/platform-admin/model-commercial-release-batches",
        response_model=list[ModelCommercialReleaseBatchResponse],
    )
    def admin_list_model_commercial_release_batches(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelCommercialReleaseService.list_batches(session)

    @app.post(
        "/api/v1/platform-admin/model-commercial-release-batches",
        response_model=ModelCommercialReleaseBatchResponse,
        status_code=201,
    )
    def admin_create_model_commercial_release_batch(
        body: ModelCommercialReleaseBatchCreateRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelCommercialReleaseService.create_batch(
            session,
            idempotency_key=body.idempotency_key,
            items=[item.model_dump(mode="python") for item in body.items],
            relay_snapshot=_batch_relay_snapshot_reader(request.state.request_id),
            approved_by_user_id=admin.user_id,
            request_id=request.state.request_id,
        )

    @app.post(
        "/api/v1/platform-admin/model-commercial-release-batches/{batch_id}/activate",
        response_model=ModelCommercialReleaseBatchActivateResponse,
    )
    def admin_activate_model_commercial_release_batch(
        batch_id: str,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        try:
            return ModelCommercialReleaseService.activate_release_batch(
                session,
                batch_id=batch_id,
                relay_snapshot=_batch_relay_snapshot_reader(
                    request.state.request_id
                ),
                activated_by_user_id=admin.user_id,
                request_id=request.state.request_id,
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            # The activation transaction rolled back leaving no partial trace,
            # so record the reason in its own transaction.  Without this the
            # batch would silently stay 'approved' and the operator would have
            # no idea why.
            session.rollback()
            failure_session = app.state.session_factory()
            try:
                ModelCommercialReleaseService.record_batch_activation_failure(
                    failure_session,
                    batch_id=batch_id,
                    failure_code="batch_activation_blocked",
                    failure_summary={"message": str(exc)[:500]},
                    request_id=request.state.request_id,
                )
                failure_session.commit()
            except Exception:  # pragma: no cover - diagnostics must not mask
                failure_session.rollback()
            finally:
                failure_session.close()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BATCH_ACTIVATION_BLOCKED",
                    "message": str(exc),
                    "batch_id": batch_id,
                },
            ) from exc

    @app.post(
        "/api/v1/platform-admin/model-commercial-release-batches/{batch_id}/abandon",
        response_model=ModelCommercialReleaseBatchResponse,
    )
    def admin_abandon_model_commercial_release_batch(
        batch_id: str,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelCommercialReleaseService.abandon_batch(
            session,
            batch_id=batch_id,
            abandoned_by_user_id=admin.user_id,
            request_id=request.state.request_id,
        )

    @app.post(
        "/api/v1/platform-admin/models",
        response_model=AdminModelResponse,
        status_code=201,
    )
    def admin_create_model(
        body: AdminModelCreateRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        model, created = ModelCatalogService.create_draft(
            session,
            slug=body.slug,
            display_name=body.display_name,
            provider_key=body.provider_key,
            billing_mode=body.billing_mode,
            capabilities=[
                (capability.key, capability.config) for capability in body.capabilities
            ],
        )
        response = ModelCatalogService.response(session, model=model)
        if created:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.create",
                target_type="model_definition",
                target_id=model.id,
                before_summary={},
                after_summary=_model_audit_summary(response),
                request_id=request.state.request_id,
            )
        return response

    @app.get(
        "/api/v1/platform-admin/models/{model_id}",
        response_model=AdminModelResponse,
    )
    def admin_get_model(
        model_id: str,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        model = ModelCatalogService.get_model(session, model_id=model_id)
        return ModelCatalogService.response(session, model=model)

    @app.put(
        "/api/v1/platform-admin/models/{model_id}",
        response_model=AdminModelResponse,
    )
    def admin_update_model(
        model_id: str,
        body: AdminModelUpdateRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, model, changed = ModelCatalogService.update_model(
            session,
            model_id=model_id,
            display_name=body.display_name,
            provider_key=body.provider_key,
            billing_mode=body.billing_mode,
            capabilities=[
                (capability.key, capability.config) for capability in body.capabilities
            ],
            expected_capability_version=body.expected_capability_version,
            capability_schema_downgrade_reason=body.capability_schema_downgrade_reason,
        )
        after = ModelCatalogService.response(session, model=model)
        if changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.update",
                target_type="model_definition",
                target_id=model.id,
                before_summary=_model_audit_summary(before),
                after_summary={
                    **_model_audit_summary(after),
                    **({
                        "capability_schema_downgrade_reason": body.capability_schema_downgrade_reason.strip(),
                    } if body.capability_schema_downgrade_reason is not None else {}),
                },
                request_id=request.state.request_id,
            )
        return after

    @app.post(
        "/api/v1/platform-admin/models/{model_id}/relay-capability/sync",
        response_model=RelayCapabilityCandidateResponse,
    )
    def admin_sync_relay_capability_candidate(
        model_id: str,
        body: RelayCapabilityCandidateSyncRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        model = ModelCatalogService.get_model(session, model_id=model_id)
        try:
            read = client.get_model_catalog(request_id=request.state.request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(status_code=502, detail="Relay 模型目录响应不完整")
        relay_model = RelayCapabilityService.relay_model(
            read.catalog, model_slug=model.slug
        )
        before_state, locked_model, changed, compatibility = (
            RelayCapabilityService.sync_candidate(
                session,
                model_id=model_id,
                expected_capability_version=body.expected_capability_version,
                expected_catalog_revision=body.expected_catalog_revision,
                expected_candidate_revision=body.expected_capability_revision,
                catalog=read.catalog,
                relay_model=relay_model,
            )
        )
        state = RelayCapabilityService.candidate_state(locked_model)
        if changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.relay_capability.candidate_sync",
                target_type="model_definition",
                target_id=locked_model.id,
                before_summary={
                    "candidate_revision": before_state["candidate_revision"],
                    "approved_revision": before_state["approved_revision"],
                },
                after_summary={
                    "relay_capability_decision": {
                        "reason": body.reason,
                        "before_revision": before_state["candidate_revision"],
                        "after_revision": state["candidate_revision"],
                        "catalog_revision": state[
                            "candidate_catalog_revision"
                        ],
                        "capability_diff": state["capability_diff"],
                    }
                },
                request_id=request.state.request_id,
            )
        return {
            "model": ModelCatalogService.response(
                session, model=locked_model
            ),
            "compatibility": compatibility,
            "candidate_revision": state["candidate_revision"],
            "approved_revision": state["approved_revision"],
            "approval_status": state["approval_status"],
            "requires_approval": state["requires_approval"],
            "capability_diff": state["capability_diff"],
            "changed": changed,
        }

    @app.get(
        "/api/v1/platform-admin/models/{model_id}/relay-capability-history",
        response_model=RelayCapabilityHistoryResponse,
    )
    def admin_relay_capability_history(
        model_id: str,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        model = ModelCatalogService.get_model(session, model_id=model_id)
        state = RelayCapabilityService.candidate_state(model)
        return {
            "model_id": model.id,
            "candidate_revision": state["candidate_revision"],
            "approved_revision": state["approved_revision"],
            "approval_status": state["approval_status"],
            "requires_approval": state["requires_approval"],
            "capability_diff": state["capability_diff"],
            "items": RelayCapabilityService.approval_history(
                session, model_id=model.id
            ),
        }

    @app.post(
        "/api/v1/platform-admin/models/{model_id}/relay-capability",
        response_model=RelayCapabilityApprovalResponse,
    )
    def admin_approve_relay_capability(
        model_id: str,
        body: RelayCapabilityApprovalRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        client = app.state.relay_client
        if client is None:
            raise HTTPException(status_code=503, detail="Relay 客户端未配置")
        model = ModelCatalogService.get_model(session, model_id=model_id)
        stored_candidate_state = RelayCapabilityService.candidate_state(model)
        if stored_candidate_state["candidate_revision"] is None:
            raise HTTPException(
                status_code=409,
                detail="请先同步并审阅 Relay 模型能力候选版本",
            )
        try:
            read = client.get_model_catalog(request_id=request.state.request_id)
        except RelayTemporaryError as exc:
            raise HTTPException(
                status_code=503, detail="Relay 模型目录暂时不可用"
            ) from exc
        except RelayPermanentError as exc:
            raise HTTPException(
                status_code=502, detail="Relay 模型目录响应无效"
            ) from exc
        if read.catalog is None or read.not_modified:
            raise HTTPException(status_code=502, detail="Relay 模型目录响应不完整")
        relay_model = RelayCapabilityService.relay_model(
            read.catalog, model_slug=model.slug
        )
        RelayCapabilityService.require_customer_callable_model(relay_model)
        if read.catalog.catalog_revision != body.expected_catalog_revision:
            raise HTTPException(
                status_code=409,
                detail="Relay 模型目录版本已变化，请重新同步并审阅候选版本",
            )
        if relay_model.capability_revision != body.expected_capability_revision:
            raise HTTPException(
                status_code=409,
                detail="Relay 模型能力版本已变化，请重新同步并审阅候选版本",
            )
        release_evidence, evidence_error = read_relay_model_release_evidence(
            request_id=request.state.request_id,
            required=True,
        )
        assert release_evidence is not None
        require_matching_relay_release_snapshot(
            catalog=read.catalog,
            evidence=release_evidence,
        )
        route_evidence = RelayCapabilityService.require_route_evidence_ready(
            public_model_id=model.slug,
            capability_revision=relay_model.capability_revision,
            evidence=release_evidence,
            evidence_error=evidence_error,
        )
        if (
            route_evidence.routing_release_sha256
            != body.expected_routing_release_sha256
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Relay 路由发布版本已变化，请重新刷新路由测试证据后审批"
                ),
            )
        before, locked_model, changed, compatibility, approval_diff = (
            RelayCapabilityService.approve_candidate(
                session,
                model_id=model_id,
                expected_capability_version=body.expected_capability_version,
                expected_catalog_revision=body.expected_catalog_revision,
                expected_candidate_revision=body.expected_capability_revision,
                live_catalog_revision=read.catalog.catalog_revision,
                live_candidate_revision=relay_model.capability_revision,
                live_candidate=relay_model.capabilities.contract_dump(),
            )
        )
        locked_candidate_state = RelayCapabilityService.candidate_state(
            locked_model
        )
        after = ModelCatalogService.response(session, model=locked_model)
        approval_audit_id = None
        if changed:
            approval = AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.relay_capability.approve",
                target_type="model_definition",
                target_id=locked_model.id,
                before_summary=_model_audit_summary(before),
                after_summary={
                    **_model_audit_summary(after),
                    "relay_capability_decision": {
                        "reason": body.reason,
                        "before_revision": before[
                            "relay_capability_revision"
                        ],
                        "after_revision": after[
                            "relay_capability_revision"
                        ],
                        "catalog_revision": (
                            locked_model.relay_capability_approved_catalog_revision
                        ),
                        "expected_routing_release_sha256": (
                            body.expected_routing_release_sha256
                        ),
                        "capability_diff": approval_diff,
                        "route_release_evidence": route_evidence.model_dump(
                            mode="json"
                        ),
                    },
                },
                request_id=request.state.request_id,
            )
            approval_audit_id = approval.id
        return {
            "model": after,
            "compatibility": compatibility,
            "capability_revision": after["relay_capability_revision"],
            "candidate_revision": locked_candidate_state["candidate_revision"],
            "approved_revision": after["relay_capability_revision"],
            "approval_status": "approved",
            "requires_approval": False,
            "capability_diff": approval_diff,
            "approval_audit_id": approval_audit_id,
            "changed": changed,
        }

    @app.post(
        "/api/v1/platform-admin/models/{model_id}/publish",
        response_model=AdminModelResponse,
    )
    def admin_publish_model(
        model_id: str,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        current_model = ModelCatalogService.get_model(session, model_id=model_id)
        if current_model.active:
            return ModelCatalogService.response(session, model=current_model)
        if app.state.relay_client is not None and (
            current_model.relay_capability_revision is None
            or current_model.relay_capability_approved_ceiling is None
            or not ModelCatalogService.relay_candidate_is_approved(
                current_model
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="请先同步并批准中转站模型能力版本，再发布模型",
            )
        release_evidence = require_models_release_ready(
            session,
            model_ids={model_id},
            request_id=request.state.request_id,
        )
        before, model, changed = ModelCatalogService.publish(
            session,
            model_id=model_id,
            require_relay_capability_revision=(app.state.relay_client is not None),
            expected_release_snapshot=(
                release_evidence[model_id].expected_snapshot
                if model_id in release_evidence
                else None
            ),
        )
        after = ModelCatalogService.response(session, model=model)
        if changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.publish",
                target_type="model_definition",
                target_id=model.id,
                before_summary=_model_audit_summary(before),
                after_summary={
                    **_model_audit_summary(after),
                    "route_release_evidence": release_evidence.get(
                        model_id
                    ).evidence
                    if model_id in release_evidence
                    else None,
                },
                request_id=request.state.request_id,
            )
        return after

    @app.post(
        "/api/v1/platform-admin/models/{model_id}/disable",
        response_model=AdminModelResponse,
    )
    def admin_disable_model(
        model_id: str,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, model, changed = ModelCatalogService.disable(session, model_id=model_id)
        after = ModelCatalogService.response(session, model=model)
        if changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.disable",
                target_type="model_definition",
                target_id=model.id,
                before_summary=_model_audit_summary(before),
                after_summary=_model_audit_summary(after),
                request_id=request.state.request_id,
            )
        return after

    @app.delete(
        "/api/v1/platform-admin/models/{model_id}",
        status_code=204,
    )
    def admin_delete_model(
        model_id: str,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> None:
        before = ModelCatalogService.delete_draft(session, model_id=model_id)
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="model.delete",
            target_type="model_definition",
            target_id=model_id,
            before_summary=_model_audit_summary(before),
            after_summary={},
            request_id=request.state.request_id,
        )

    @app.get(
        "/api/v1/platform-admin/personal-model-grants",
        response_model=list[AdminPersonalModelGrantResponse],
    )
    def admin_list_personal_model_grants(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return PersonalRetailGrantService.list_all(session)

    @app.post(
        "/api/v1/platform-admin/personal-model-grants/batch/preview",
    )
    def admin_preview_personal_model_grant_batch(
        body: AdminPersonalModelGrantBatchPreviewRequest,
        request: Request,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        changes = [item.model_dump(exclude_unset=True) for item in body.changes]
        release_readiness = require_models_release_ready(
            session,
            model_ids={item["model_id"] for item in changes if CommercialPricingPolicy.needs_live_evidence(session, model_id=item["model_id"], change=item)},
            request_id=request.state.request_id,
        )
        return PersonalRetailGrantService.preview_batch(
            session,
            changes=changes,
            require_relay_approval=(app.state.relay_client is not None),
            expected_release_snapshots={
                model_id: readiness.expected_snapshot
                for model_id, readiness in release_readiness.items()
            },
        )

    @app.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
    )
    def admin_execute_personal_model_grant_batch(
        body: AdminPersonalModelGrantBatchExecuteRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        # Nested Pydantic fields retain their own fields-set state. Dumping with
        # exclude_unset preserves the distinction between an omitted limit and
        # an explicit JSON null all the way into the batch fingerprint.
        changes = [item.model_dump(exclude_unset=True) for item in body.changes]
        claim = PersonalRetailGrantService.claim_batch(
            session,
            changes=changes,
            expected_snapshot=body.expected_snapshot,
            actor_user_id=admin.user_id,
            reason=body.reason,
            request_id=request.state.request_id,
            idempotency_key=body.idempotency_key,
        )
        replay = claim.get("replay")
        if isinstance(replay, dict):
            return replay
        release_readiness = (
            require_models_release_ready(
                session,
                model_ids={item["model_id"] for item in changes if CommercialPricingPolicy.needs_live_evidence(session, model_id=item["model_id"], change=item)},
                request_id=request.state.request_id,
            )
        )
        result = PersonalRetailGrantService.execute_batch(
            session,
            changes=changes,
            expected_snapshot=body.expected_snapshot,
            actor_user_id=admin.user_id,
            reason=body.reason,
            request_id=request.state.request_id,
            idempotency_key=body.idempotency_key,
            require_relay_approval=(app.state.relay_client is not None),
            claim=claim,
            expected_release_snapshots={
                model_id: readiness.expected_snapshot
                for model_id, readiness in release_readiness.items()
            },
        )
        if not result.get("idempotent_replay") and release_readiness:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="personal_model_grant.route_evidence",
                target_type="personal_model_grant_batch",
                target_id=body.idempotency_key,
                before_summary={},
                after_summary={
                    "models": {
                        model_id: readiness.evidence
                        for model_id, readiness in release_readiness.items()
                    }
                },
                request_id=request.state.request_id,
            )
        return result

    @app.put(
        "/api/v1/platform-admin/personal-model-grants/{model_id}",
        response_model=AdminPersonalModelGrantResponse,
    )
    def admin_upsert_personal_model_grant(
        model_id: str,
        body: AdminPersonalModelGrantRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        release_readiness = (
            require_models_release_ready(
                session,
                model_ids={model_id},
                request_id=request.state.request_id,
            )
            if CommercialPricingPolicy.needs_live_evidence(session, model_id=model_id, change=body.model_dump())
            else {}
        )
        limit_changes = {
            field: getattr(body, field)
            for field in ("call_quota", "concurrency_limit")
            if field in body.model_fields_set
        }
        before, after, changed = PersonalRetailGrantService.upsert(
            session,
            model_id=model_id,
            expected_capability_version=body.expected_capability_version,
            expected_quote_revision=body.expected_quote_revision,
            enabled=body.enabled,
            price_per_second_points=body.price_per_second_points,
            price_per_item_points=body.price_per_item_points,
            config_override=body.config_override,
            require_relay_approval=(app.state.relay_client is not None),
            expected_release_snapshot=(
                release_readiness[model_id].expected_snapshot
                if model_id in release_readiness
                else None
            ),
            **limit_changes,
        )
        if changed:
            audit_fields = (
                "model_id",
                "model_slug",
                "capability_version",
                "relay_capability_revision",
                "grant_id",
                "enabled",
                "price_per_second_points",
                "price_per_item_points",
                "call_quota",
                "concurrency_limit",
                "config_override",
                "quote_revision",
            )
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="personal_model_grant.upsert",
                target_type="personal_retail_model_grant",
                target_id=model_id,
                before_summary={
                    **{field: before[field] for field in audit_fields},
                    "reason": body.reason,
                },
                after_summary={
                    **{field: after[field] for field in audit_fields},
                    "reason": body.reason,
                    "route_release_evidence": (
                        release_readiness[model_id].evidence
                        if model_id in release_readiness
                        else None
                    ),
                },
                request_id=request.state.request_id,
            )
        return after

    @app.get(
        "/api/v1/platform-admin/companies",
        response_model=AdminCompanyPage,
    )
    def admin_list_companies(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        total, items = PlatformAdminService.page_companies(
            session, page=page, page_size=page_size
        )
        enriched_items = []
        for company in items:
            owner_row = session.execute(
                select(CompanyMembership, User)
                .join(User, User.id == CompanyMembership.user_id)
                .join(
                    MembershipRole,
                    MembershipRole.membership_id == CompanyMembership.id,
                )
                .join(Role, Role.id == MembershipRole.role_id)
                .where(
                    CompanyMembership.company_id == company.id,
                    Role.system_key == "owner",
                )
                .order_by(CompanyMembership.id)
                .limit(1)
            ).first()
            membership, owner = owner_row if owner_row is not None else (None, None)
            enriched_items.append(
                {
                    "id": company.id,
                    "name": company.name,
                    "status": company.status,
                    "billing_unit": company.billing_unit,
                    "billing_version": company.billing_version,
                    "created_at": company.created_at,
                    "updated_at": company.updated_at,
                    "owner_activation_required": bool(
                        membership is not None
                        and membership.status == MembershipStatus.DISABLED
                    ),
                    "owner_user_id": owner.id if owner is not None else None,
                    "owner_membership_id": (
                        membership.id if membership is not None else None
                    ),
                    # One-time owner links are intentionally never replayed by list.
                    "owner_invitation_url": None,
                    "owner_invitation_expires_at": None,
                }
            )
        return AdminCompanyPage(
            page=page, page_size=page_size, total=total, items=enriched_items
        )

    @app.post(
        "/api/v1/platform-admin/companies",
        response_model=AdminCompanyResponse,
        status_code=201,
    )
    def admin_create_company(
        body: AdminCreateCompanyRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        owner_activation_required = runtime_settings_are_protected(settings)
        company, owner, owner_membership = PlatformAdminService.create_company(
            session,
            name=body.name,
            owner_email=str(body.owner_email),
            owner_display_name=body.owner_display_name,
            owner_activation_required=owner_activation_required,
        )
        invitation_url = None
        invitation_expires_at = None
        if owner_membership.status == MembershipStatus.DISABLED:
            invitation, token, _ = InvitationService.create(
                session,
                company_id=company.id,
                actor_user_id=admin.user_id,
                email=owner.email,
                display_name=owner.display_name,
                # The membership already carries the owner role. This stored
                # non-owner value is never assigned during acceptance.
                primary_role="operator",
                idempotency_key=f"owner-bootstrap:{company.id}",
                expires_in_seconds=settings.invitation_ttl_seconds,
                pepper=settings.jwt_signing_secret,
                request_id=request.state.request_id,
                allow_existing_owner_membership=True,
            )
            invitation_payload = InvitationService.response(
                invitation,
                acceptance_token=token,
                frontend_origin=settings.frontend_origin,
            )
            invitation_url = invitation_payload["invitation_url"]
            invitation_expires_at = invitation.expires_at
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="company.create",
            target_type="company",
            target_id=company.id,
            before_summary={},
            after_summary={
                "name": company.name,
                "status": company.status.value,
                "owner_user_id": owner.id,
                "owner_activation_required": owner_membership.status.value
                == "disabled",
            },
            request_id=request.state.request_id,
        )
        return {
            "id": company.id,
            "name": company.name,
            "status": company.status,
            "billing_unit": company.billing_unit,
            "billing_version": company.billing_version,
            "created_at": company.created_at,
            "updated_at": company.updated_at,
            "owner_activation_required": owner_membership.status
            == MembershipStatus.DISABLED,
            "owner_user_id": owner.id,
            "owner_membership_id": owner_membership.id,
            "owner_invitation_url": invitation_url,
            "owner_invitation_expires_at": invitation_expires_at,
        }

    @app.patch(
        "/api/v1/platform-admin/companies/{company_id}/status",
        response_model=AdminCompanyResponse,
    )
    def admin_set_company_status(
        company_id: str,
        body: AdminCompanyStatusRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, company = PlatformAdminService.set_company_status(
            session, company_id=company_id, status=body.status
        )
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="company.status.update",
            target_type="company",
            target_id=company.id,
            before_summary={"status": before.value},
            after_summary={"status": company.status.value},
            request_id=request.state.request_id,
        )
        return company

    @app.get(
        "/api/v1/platform-admin/companies/{company_id}/entitlements",
        response_model=CompanyEntitlementsResponse,
    )
    def admin_company_entitlements(
        company_id: str,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> CompanyEntitlementsResponse:
        return CompanyEntitlementsResponse.model_validate(
            PlatformAdminService.company_entitlements(session, company_id=company_id)
        )

    @app.post(
        "/api/v1/platform-admin/companies/{company_id}/billing/migrate-to-points",
        response_model=CompanyPointsMigrationResponse,
    )
    def admin_migrate_company_to_points(
        company_id: str,
        body: CompanyPointsMigrationRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        if not admin.is_platform_owner:
            raise HTTPException(status_code=403, detail="仅平台所有者可迁移企业计费")
        result = CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=body.expected_available_cents,
            idempotency_key=body.idempotency_key,
        )
        if result.changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="company.billing.migrate_to_points",
                target_type="company",
                target_id=company_id,
                before_summary={
                    "billing_unit": BillingUnit.CNY_CENT.value,
                    "billing_version": 1,
                    "available_cents": result.conversion.source_cents,
                },
                after_summary={
                    "billing_unit": BillingUnit.POINT.value,
                    "billing_version": 2,
                    "available_points": result.wallet.available_points,
                    "rounding_remainder_cents": (
                        result.conversion.rounding_remainder_cents
                    ),
                    "rounding_subsidy_cents": (
                        result.conversion.rounding_subsidy_cents
                    ),
                    "generation_enabled": False,
                },
                request_id=request.state.request_id,
            )
        return {
            "company_id": company_id,
            "source_cents": result.conversion.source_cents,
            "converted_points": result.conversion.converted_points,
            "legacy_points": result.conversion.legacy_points,
            "rounding_remainder_cents": (
                result.conversion.rounding_remainder_cents
            ),
            "rounding_grant_points": result.conversion.rounding_grant_points,
            "rounding_subsidy_cents": result.conversion.rounding_subsidy_cents,
            "generation_enabled": False,
            "changed": result.changed,
            "wallet": _company_wallet_payload(session, company_id=company_id),
        }

    @app.post(
        "/api/v1/platform-admin/companies/{company_id}/recharge",
        response_model=WalletOperationResponse,
    )
    def admin_recharge(
        company_id: str,
        body: RechargeRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        account, entry, created = WalletService.recharge(
            session,
            company_id=company_id,
            amount_cents=body.amount_cents,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
        if created:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="company.wallet.recharge",
                target_type="company",
                target_id=company_id,
                before_summary={
                    "available_cents": account.available_cents - body.amount_cents
                },
                after_summary={
                    "available_cents": account.available_cents,
                    "ledger_entry_id": entry.id,
                    "amount_cents": body.amount_cents,
                },
                request_id=request.state.request_id,
            )
        return WalletOperationResponse(
            wallet=_company_wallet_payload(session, company_id=company_id),
            ledger_entry=_legacy_ledger_payload(entry),
        )

    @app.get(
        "/api/v1/platform-admin/companies/{company_id}/recharges",
        response_model=RechargeRecordPage,
    )
    def admin_recharge_records(
        company_id: str,
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> RechargeRecordPage:
        _validate_report_time_range(start_time, end_time)
        (
            total,
            total_amount_cents,
            total_amount_points,
            items,
            unit_versions,
        ) = WalletService.funding_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return RechargeRecordPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            total_amount_cents=total_amount_cents,
            total_amount_points=total_amount_points,
            items=items,
        )

    @app.put(
        "/api/v1/platform-admin/companies/{company_id}/model-grants",
        response_model=CompanyModelGrantResponse,
    )
    def admin_upsert_model_grant(
        company_id: str,
        body: CompanyModelGrantRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        release_readiness = (
            require_models_release_ready(
                session,
                model_ids={body.model_id},
                request_id=request.state.request_id,
            )
            if CommercialPricingPolicy.needs_live_evidence(session, model_id=body.model_id, company_id=company_id, change=body.model_dump())
            else {}
        )
        before, grant = ModelGrantService.upsert_grant(
            session,
            company_id=company_id,
            model_id=body.model_id,
            enabled=body.enabled,
            price_per_second_cents=body.price_per_second_cents,
            price_per_item_cents=body.price_per_item_cents,
            price_per_second_points=body.price_per_second_points,
            price_per_item_points=body.price_per_item_points,
            actor_user_id=admin.user_id,
            config_override=body.config_override,
            call_quota=body.call_quota,
            concurrency_limit=body.concurrency_limit,
            effective_at=body.effective_at,
            expires_at=body.expires_at,
            expected_updated_at=body.expected_updated_at,
            return_before=True,
            expected_release_snapshot=(
                release_readiness[body.model_id].expected_snapshot
                if body.model_id in release_readiness
                else None
            ),
        )
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="company.model_grant.upsert",
            target_type="company_model_grant",
            target_id=grant.id,
            before_summary=before,
            after_summary={
                "company_id": company_id,
                "model_id": grant.model_id,
                "enabled": grant.enabled,
                "price_per_second_cents": grant.price_per_second_cents,
                "price_per_item_cents": grant.price_per_item_cents,
                "price_per_second_points": grant.price_per_second_points,
                "price_per_item_points": grant.price_per_item_points,
                "point_price_active_version_id": grant.point_price_active_version_id,
                "config_override": grant.config_override,
                "call_quota": grant.call_quota,
                "concurrency_limit": grant.concurrency_limit,
                "effective_at": (
                    grant.effective_at.isoformat()
                    if grant.effective_at is not None
                    else None
                ),
                "expires_at": (
                    grant.expires_at.isoformat()
                    if grant.expires_at is not None
                    else None
                ),
                "updated_at": grant.updated_at.isoformat(),
                "route_release_evidence": (
                    release_readiness[body.model_id].evidence
                    if body.model_id in release_readiness
                    else None
                ),
            },
            request_id=request.state.request_id,
        )
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(status_code=404, detail="公司不存在")
        return ModelGrantService.response(
            grant, billing_version=company.billing_version
        )

    @app.get(
        "/api/v1/platform-admin/resources",
        response_model=list[ResourceDefinitionResponse],
    )
    def admin_list_resources(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ResourceGrantService.list_definitions(session)

    @app.post(
        "/api/v1/platform-admin/resources",
        response_model=ResourceDefinitionResponse,
        status_code=201,
    )
    def admin_create_resource(
        body: ResourceDefinitionRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        resource = ResourceGrantService.create_definition(
            session,
            key=body.key,
            kind=body.kind,
            display_name=body.display_name,
            description=body.description,
            active=body.active,
        )
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="resource.create",
            target_type="resource_definition",
            target_id=resource.id,
            before_summary={},
            after_summary={
                "key": resource.key,
                "kind": resource.kind.value,
                "active": resource.active,
            },
            request_id=request.state.request_id,
        )
        return resource

    @app.put(
        "/api/v1/platform-admin/resources/{resource_id}",
        response_model=ResourceDefinitionResponse,
    )
    def admin_update_resource(
        resource_id: str,
        body: ResourceDefinitionUpdateRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, resource, changed = ResourceGrantService.update_definition(
            session,
            resource_id=resource_id,
            display_name=body.display_name,
            description=body.description,
            active=body.active,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="resource.update",
                target_type="resource_definition",
                target_id=resource.id,
                before_summary=before,
                after_summary={
                    "display_name": resource.display_name,
                    "description": resource.description,
                    "active": resource.active,
                },
                request_id=request.state.request_id,
            )
        return resource

    @app.put(
        "/api/v1/platform-admin/companies/{company_id}/resources/{resource_id}",
        response_model=CompanyResourceGrantResponse,
    )
    def admin_upsert_resource_grant(
        company_id: str,
        resource_id: str,
        body: CompanyResourceGrantRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        existing = session.scalar(
            select(CompanyResourceGrant).where(
                CompanyResourceGrant.company_id == company_id,
                CompanyResourceGrant.resource_id == resource_id,
            )
        )
        before = (
            {
                "enabled": existing.enabled,
                "config_override": existing.config_override,
                "call_quota": existing.call_quota,
                "concurrency_limit": existing.concurrency_limit,
                "effective_at": (
                    existing.effective_at.isoformat()
                    if existing.effective_at is not None
                    else None
                ),
                "expires_at": (
                    existing.expires_at.isoformat()
                    if existing.expires_at is not None
                    else None
                ),
            }
            if existing
            else {}
        )
        grant = ResourceGrantService.upsert_company_grant(
            session,
            company_id=company_id,
            resource_id=resource_id,
            enabled=body.enabled,
            config_override=body.config_override,
            call_quota=body.call_quota,
            concurrency_limit=body.concurrency_limit,
            effective_at=body.effective_at,
            expires_at=body.expires_at,
        )
        AuditService.append(
            session,
            actor_user_id=admin.user_id,
            action="company.resource_grant.upsert",
            target_type="company_resource_grant",
            target_id=grant.id,
            before_summary=before,
            after_summary={
                "company_id": company_id,
                "resource_id": resource_id,
                "enabled": grant.enabled,
                "config_override": grant.config_override,
                "call_quota": grant.call_quota,
                "concurrency_limit": grant.concurrency_limit,
                "effective_at": (
                    grant.effective_at.isoformat()
                    if grant.effective_at is not None
                    else None
                ),
                "expires_at": (
                    grant.expires_at.isoformat()
                    if grant.expires_at is not None
                    else None
                ),
            },
            request_id=request.state.request_id,
        )
        return grant

    @app.get(
        "/api/v1/platform-admin/reports/consumption",
        response_model=ConsumptionReportPage,
    )
    def admin_consumption_report(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        company_id: str | None = None,
        employee_user_id: str | None = None,
        employee_query: str | None = Query(default=None, max_length=160),
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ConsumptionReportPage:
        _validate_report_time_range(start_time, end_time)
        (
            total,
            total_amount_cents,
            total_amount_points,
            items,
            unit_versions,
        ) = ReportService.consumption_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            employee_user_id=employee_user_id,
            employee_query=employee_query,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        billing_unit, billing_version = _report_billing_metadata(
            session,
            company_id=company_id,
            unit_versions=unit_versions,
        )
        return ConsumptionReportPage(
            page=page,
            page_size=page_size,
            total=total,
            billing_unit=billing_unit,
            billing_version=billing_version,
            total_amount_cents=total_amount_cents,
            total_amount_points=total_amount_points,
            items=items,
        )

    @app.get("/api/v1/platform-admin/reports/consumption/export.csv")
    def admin_export_consumption_report(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        company_id: str | None = None,
        employee_user_id: str | None = None,
        employee_query: str | None = Query(default=None, max_length=160),
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Response:
        _validate_report_time_range(start_time, end_time)
        document = ReportService.consumption_export(
            session,
            company_id=company_id,
            employee_user_id=employee_user_id,
            employee_query=employee_query,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        return Response(
            content=document.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    'attachment; filename="platform-consumption-report.csv"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post(
        "/api/v1/platform-admin/channel-costs",
        response_model=ChannelCostEntryResponse,
        status_code=201,
    )
    def admin_create_channel_cost(
        body: ChannelCostCreateRequest,
        request: Request,
        admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        entry, created = ChannelCostService.create(
            session,
            **body.model_dump(),
            source=ChannelCostSource.PLATFORM_ADMIN,
            recorded_by_user_id=admin.user_id,
        )
        if created:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="channel_cost.create",
                target_type="channel_cost_entry",
                target_id=entry.id,
                before_summary={},
                after_summary={
                    "amount_cents": entry.amount_cents,
                    "channel_key": entry.channel_key,
                    "channel_type": entry.channel_type.value,
                    "company_id": entry.company_id,
                    "personal_workspace_id": entry.personal_workspace_id,
                    "task_id": entry.task_id,
                    "external_reference": entry.external_reference,
                },
                request_id=request.state.request_id,
            )
        return entry

    @app.get(
        "/api/v1/platform-admin/channel-costs",
        response_model=ChannelCostPage,
    )
    def admin_channel_costs(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        company_id: str | None = None,
        personal_workspace_id: str | None = None,
        task_id: str | None = None,
        channel_key: str | None = Query(default=None, max_length=120),
        channel_type: ChannelType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ChannelCostPage:
        _validate_report_time_range(start_time, end_time)
        total, total_amount_cents, items = ChannelCostService.page(
            session,
            page=page,
            page_size=page_size,
            company_id=company_id,
            personal_workspace_id=personal_workspace_id,
            task_id=task_id,
            channel_key=channel_key,
            channel_type=channel_type,
            start_time=start_time,
            end_time=end_time,
        )
        return ChannelCostPage(
            page=page,
            page_size=page_size,
            total=total,
            total_amount_cents=total_amount_cents,
            items=items,
        )

    @app.get(
        "/api/v1/platform-admin/dashboard",
        response_model=PlatformDashboardResponse,
    )
    def admin_dashboard(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        return DashboardService.build(session, page=page, page_size=page_size)

    @app.get(
        "/api/v1/platform-admin/audit-logs",
        response_model=AuditLogPage,
    )
    def admin_audit_logs(
        _: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        total, items = AuditService.page(session, page=page, page_size=page_size)
        return AuditLogPage(page=page, page_size=page_size, total=total, items=items)

    @app.post(
        "/internal/relay/provider-onboarding-status",
        response_model=ProviderOnboardingStatusBatchResponse,
    )
    def provider_onboarding_status(
        body: ProviderOnboardingStatusBatchRequest,
        response: Response,
        _: Annotated[None, Depends(require_internal_service)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> ProviderOnboardingStatusBatchResponse:
        # This endpoint is deliberately projection-only. Platform catalog
        # reconciliation, commercial approval, pricing, and grant mutations
        # remain explicit workflows with their existing authorities.
        response.headers["Cache-Control"] = "no-store"
        return ProviderOnboardingStatusProjectionService.project(
            session,
            body=body,
        )

    @app.post(
        "/internal/channel-costs",
        response_model=ChannelCostEntryResponse,
        status_code=201,
    )
    async def record_relay_channel_cost(
        request: Request,
        _: Annotated[None, Depends(require_internal_service)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        verifier = app.state.channel_cost_event_verifier
        if verifier is None:  # pragma: no cover - dependency fails first
            raise HTTPException(
                status_code=503,
                detail="Relay channel-cost verifier is not configured",
            )
        body, evidence = verifier.verify(
            await _read_limited_request_body(request),
            event_id=request.headers.get("X-Relay-Event-ID"),
            timestamp=request.headers.get("X-Relay-Timestamp"),
            signature=request.headers.get("X-Relay-Signature"),
        )
        entry, _ = ChannelCostService.create(
            session,
            **body.model_dump(),
            relay_event_id=evidence.event_id if evidence else None,
            relay_event_timestamp=(evidence.delivery_timestamp if evidence else None),
            relay_payload_sha256=(evidence.payload_sha256 if evidence else None),
            source=ChannelCostSource.RELAY,
            recorded_by_user_id=None,
        )
        return entry

    @app.post(
        "/internal/relay/dispatch-once",
        response_model=InternalDispatchResponse,
    )
    def dispatch_once(
        _: Annotated[None, Depends(require_internal_service)],
    ) -> InternalDispatchResponse:
        client = app.state.relay_backend_registry
        if client is None:
            raise HTTPException(status_code=503, detail="中转站客户端尚未配置")
        result = RelayOutboxDispatcher(
            app.state.session_factory,
            client,
            max_attempts=settings.relay_dispatch_max_attempts,
            asset_reference_resolver=app.state.input_asset_relay_resolver,
        ).dispatch_once()
        return InternalDispatchResponse(
            processed=result.processed,
            outbox_id=result.outbox_id,
            status=result.status,
            relay_job_id=result.relay_job_id,
        )

    @app.post(
        "/internal/relay/status",
        response_model=TaskResponse,
    )
    def sync_relay_status(
        body: RelayStatusUpdateRequest,
        _: Annotated[None, Depends(require_internal_service)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        if runtime_settings_are_protected(settings):
            # Production status transitions may come only from a complete,
            # strictly parsed Relay GET or a trusted signed callback. This
            # legacy test/operations helper accepts a caller-authored snapshot
            # and must never become an alternate settlement authority.
            raise HTTPException(status_code=404, detail="Not found")
        task = RelayStatusService.apply(
            session,
            company_id=body.company_id,
            task_id=body.task_id,
            relay_job_id=body.relay_job_id,
            status=body.status,
            outputs=body.outputs,
            failure_reason=(
                body.error.message if body.error is not None else body.failure_reason
            ),
            error_snapshot=(
                {
                    **body.error.model_dump(mode="json"),
                    "source": "poll",
                }
                if body.error is not None
                else None
            ),
            reservation_action=body.reservation_action,
        )
        return TaskService.response_payload(session, task)

    @app.post("/internal/relay-callbacks", status_code=204)
    async def receive_relay_callback(
        request: Request,
        session: Annotated[Session, Depends(get_db, scope="function")],
        event_id: Annotated[str | None, Header(alias="X-Relay-Event-ID")] = None,
        timestamp: Annotated[str | None, Header(alias="X-Relay-Timestamp")] = None,
        signature: Annotated[str | None, Header(alias="X-Relay-Signature")] = None,
    ) -> Response:
        if not settings.relay_legacy_compatibility_enabled:
            # The unqualified route belonged to the retired Python Relay.
            # Keep the path non-callable so old allowlists cannot select a
            # production verifier after the native new-api cutover.
            raise HTTPException(status_code=404, detail="Not found")
        verifier = app.state.relay_callback_verifier
        if verifier is None:
            raise HTTPException(
                status_code=503,
                detail="中转站主动回调尚未配置",
            )
        payload, payload_sha256 = verifier.verify(
            await _read_limited_request_body(request),
            event_id=event_id,
            timestamp=timestamp,
            signature=signature,
        )
        _, duplicate = RelayCallbackService.apply(
            session,
            payload=payload,
            payload_sha256=payload_sha256,
            request_id=request.state.request_id,
            source_backend_id=LEGACY_RELAY_BACKEND_ID,
        )
        return Response(
            status_code=204,
            headers={"X-Relay-Callback-Duplicate": ("true" if duplicate else "false")},
        )

    @app.post("/internal/relay-callbacks/{source_backend_id}", status_code=204)
    async def receive_backend_bound_relay_callback(
        source_backend_id: str,
        request: Request,
        session: Annotated[Session, Depends(get_db, scope="function")],
        event_id: Annotated[str | None, Header(alias="X-Relay-Event-ID")] = None,
        timestamp: Annotated[str | None, Header(alias="X-Relay-Timestamp")] = None,
        signature: Annotated[str | None, Header(alias="X-Relay-Signature")] = None,
    ) -> Response:
        registry = app.state.relay_callback_verifier_registry
        if registry is None:
            raise HTTPException(
                status_code=503,
                detail="中转站主动回调尚未配置",
            )
        verifier = registry.resolve(source_backend_id)
        payload, payload_sha256 = verifier.verify(
            await _read_limited_request_body(request),
            event_id=event_id,
            timestamp=timestamp,
            signature=signature,
        )
        _, duplicate = RelayCallbackService.apply(
            session,
            payload=payload,
            payload_sha256=payload_sha256,
            request_id=request.state.request_id,
            source_backend_id=source_backend_id,
        )
        return Response(
            status_code=204,
            headers={"X-Relay-Callback-Duplicate": ("true" if duplicate else "false")},
        )

    @app.get(
        "/internal/relay-callback-events",
        response_model=RelayCallbackEventPage,
    )
    def list_relay_callback_events(
        _: Annotated[None, Depends(require_internal_service)],
        session: Annotated[Session, Depends(get_db, scope="function")],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        company_id: str | None = None,
        task_id: str | None = None,
        relay_status: (
            Literal[
                "processing",
                "reconciliation_required",
                "succeeded",
                "failed",
                "cancelled",
            ]
            | None
        ) = None,
    ) -> RelayCallbackEventPage:
        total, items = RelayCallbackService.page(
            session,
            page=page,
            page_size=page_size,
            company_id=company_id,
            task_id=task_id,
            relay_status=relay_status,
        )
        return RelayCallbackEventPage(
            page=page,
            page_size=page_size,
            total=total,
            items=items,
        )

    @app.post(
        "/internal/tasks/timeout-scan",
        response_model=InternalTimeoutScanResponse,
    )
    def scan_task_timeouts(
        _: Annotated[None, Depends(require_internal_service)],
    ) -> InternalTimeoutScanResponse:
        result = TaskTimeoutService(
            app.state.session_factory,
            app.state.relay_backend_registry,
            queued_timeout_seconds=settings.task_queued_timeout_seconds,
            processing_timeout_seconds=settings.task_processing_timeout_seconds,
            batch_size=settings.task_timeout_batch_size,
        ).scan_once()
        return InternalTimeoutScanResponse(
            scanned=result.scanned,
            compensated=result.compensated,
            reconciled=result.reconciled,
            deferred=result.deferred,
            items=[
                TimeoutScanItemResponse(
                    task_id=item.task_id,
                    previous_status=item.previous_status,
                    outcome=item.outcome,
                    reason=item.reason,
                    final_status=item.final_status,
                    released_cents=item.released_cents,
                )
                for item in result.items
            ],
        )

    @app.get(
        "/internal/tasks/timeout-events",
        response_model=TaskTimeoutEventPage,
    )
    def list_task_timeout_events(
        _: Annotated[None, Depends(require_internal_service)],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> TaskTimeoutEventPage:
        total, items = TaskTimeoutService(
            app.state.session_factory,
            app.state.relay_backend_registry,
            queued_timeout_seconds=settings.task_queued_timeout_seconds,
            processing_timeout_seconds=settings.task_processing_timeout_seconds,
            batch_size=settings.task_timeout_batch_size,
        ).page_events(page=page, page_size=page_size)
        return TaskTimeoutEventPage(
            page=page,
            page_size=page_size,
            total=total,
            items=items,
        )

    app.include_router(platform_admin_access_router)
    app.include_router(admin_operations_router)
    app.include_router(admin_relay_native_console_router)
    app.include_router(admin_task_content_router)
    app.include_router(relay_telemetry_router)
    app.include_router(personal_workspace_router)
    app.include_router(authentication_router)
    app.include_router(showcase_router)
    app.include_router(payments_router)
    app.include_router(finance_router)
    app.include_router(enterprise_billing_router)
    app.include_router(director_shot_packages_router)
    return app


app = create_app()
