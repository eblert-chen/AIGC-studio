from __future__ import annotations

from datetime import datetime
import os
import socket
from typing import Annotated, Literal
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import Settings, get_settings, runtime_settings_are_protected
from .asset_storage import (
    FilesystemInputAssetSigner,
    InputAssetStore,
    InputAssetSignatureError,
    InputAssetStorageError,
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
from .routers.relay_telemetry import router as relay_telemetry_router
from .routers.personal import router as personal_workspace_router
from .routers.authentication import router as authentication_router
from .routers.showcase import router as showcase_router
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
    ChannelCostSource,
    ChannelType,
    CompanyMembership,
    CompanyModelGrant,
    CompanyResourceGrant,
    DownloadCompletionSource,
    LedgerEntry,
    InputAssetStatus,
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
    RelayCapabilityApprovalRequest,
    RelayCapabilityApprovalResponse,
    RelayCapabilityAuditResponse,
    AssignRoleRequest,
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
    CompanyMeResponse,
    CreateDevPublisherConnectionRequest,
    CreateMemberRequest,
    CreatePublicationJobRequest,
    CreateRoleRequest,
    CreateTaskRequest,
    DevModelSeedRequest,
    DevModelSeedResponse,
    DownloadRecordPage,
    EdgeGatewayDownloadCompletionRequest,
    ObsAccessLogDownloadCompletionRequest,
    DownloadCompletionResponse,
    InternalDispatchResponse,
    InternalTimeoutScanResponse,
    InputAssetAccessResponse,
    InputAssetResponse,
    PromotedInputAssetResponse,
    PromoteTaskArtifactRequest,
    LedgerEntryResponse,
    MemberResponse,
    MemberPermissionDetailResponse,
    MemberStatusRequest,
    PermissionCatalogResponse,
    PermissionOverrideRequest,
    PermissionOverrideResponse,
    PublicationJobPage,
    PlatformAdminIdentityResponse,
    PlatformAdminMeResponse,
    PlatformDashboardResponse,
    PublicationJobDetailResponse,
    PublicationJobResponse,
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
    ReplaceMemberAccessRequest,
    ReplaceMemberRolesRequest,
    ReplacePermissionOverridesRequest,
    RoleResponse,
    UpdateRoleRequest,
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
from .services.errors import ConflictError, DomainError
from .services.models import ModelCatalogService, ModelGrantService
from .services.publishing import PublishingService
from .publishing_adapters import (
    PublisherAdapterRegistry,
    build_publisher_registry,
    load_publisher_adapter,
)
from .services.relay_capabilities import RelayCapabilityService
from .services.input_assets import (
    InputAssetRelayResolver,
    InputAssetService,
)
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


def _member_response(
    session: Session, *, company_id: str, user: User, membership
) -> MemberResponse:
    roles = AccessLifecycleService.roles_for_membership(
        session, company_id=company_id, membership_id=membership.id
    )
    inherited_permissions = PermissionService.inherited_permissions(
        session, membership_id=membership.id
    )
    permission_overrides = PermissionService.permission_overrides(
        session, membership_id=membership.id
    )
    effective_permissions = PermissionService.apply_overrides(
        inherited_permissions, permission_overrides
    )
    return MemberResponse(
        user_id=user.id,
        membership_id=membership.id,
        email=user.email,
        display_name=user.display_name,
        status=membership.status,
        roles=[
            {
                "id": role.id,
                "name": role.name,
                "is_system": role.is_system,
                "system_key": role.system_key,
            }
            for role in roles
        ],
        inherited_permission_codes=sorted(inherited_permissions),
        effective_permission_codes=sorted(effective_permissions),
        permission_overrides=[
            {"permission_code": code, "effect": effect}
            for code, effect in permission_overrides.items()
        ],
    )


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
        allow_credentials=settings.oidc_enabled,
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
        if request.url.path == "/api/v1/platform-admin/relay/native-console/open":
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

    @app.get(
        "/api/v1/companies/{company_id}/me",
        response_model=CompanyMeResponse,
    )
    def company_me(
        company_id: str,
        context: Annotated[TenantContext, Depends(get_tenant_context)],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return AccessLifecycleService.current_identity(
            session,
            company_id=company_id,
            membership_id=context.membership_id,
            user_id=context.user_id,
        )

    @app.get(
        "/api/v1/companies/{company_id}/permissions",
        response_model=list[PermissionCatalogResponse],
    )
    def list_permission_catalog(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("users.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return PermissionService.list_catalog(session)

    @app.get(
        "/api/v1/companies/{company_id}/members",
        response_model=list[MemberResponse],
    )
    def list_members(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("users.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> list[MemberResponse]:
        return [
            _member_response(
                session,
                company_id=company_id,
                user=user,
                membership=membership,
            )
            for user, membership in CompanyService.list_members(
                session, company_id=company_id
            )
        ]

    @app.get(
        "/api/v1/companies/{company_id}/members/{membership_id}/permissions",
        response_model=MemberPermissionDetailResponse,
    )
    def member_permission_detail(
        company_id: str,
        membership_id: str,
        _: Annotated[TenantContext, Depends(require_permission("users.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        membership = AccessLifecycleService.get_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        return {
            "membership_id": membership.id,
            "items": PermissionService.permission_detail(
                session, membership_id=membership.id
            ),
        }

    @app.post(
        "/api/v1/companies/{company_id}/members",
        response_model=MemberResponse,
        status_code=201,
    )
    def create_member(
        company_id: str,
        body: CreateMemberRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> MemberResponse:
        if runtime_settings_are_protected(request.app.state.settings):
            raise DomainError(
                "Company invitations are required",
                "invitation_required",
                409,
            )
        user, membership, created = CompanyService.add_member(
            session,
            company_id=company_id,
            email=str(body.email),
            display_name=body.display_name,
        )
        if created:
            primary_role = AccessLifecycleService.system_role(
                session,
                company_id=company_id,
                system_key=body.primary_role,
            )
            AccessLifecycleService.assign_role(
                session,
                company_id=company_id,
                membership_id=membership.id,
                role_id=primary_role.id,
                actor_membership_id=context.membership_id,
            )
        else:
            existing_primary_keys = {
                role.system_key
                for role in AccessLifecycleService.roles_for_membership(
                    session,
                    company_id=company_id,
                    membership_id=membership.id,
                )
                if role.system_key in {"operator", "team_lead"}
            }
            if existing_primary_keys != {body.primary_role}:
                raise ConflictError(
                    "该成员已存在；基础级别不一致，请使用成员升降级接口"
                )
        if created:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.create",
                target_type="company_membership",
                target_id=membership.id,
                before_summary={},
                after_summary={
                    "company_id": company_id,
                    "user_id": user.id,
                    "status": membership.status.value,
                    "primary_role": body.primary_role,
                },
                request_id=request.state.request_id,
            )
        return _member_response(
            session,
            company_id=company_id,
            user=user,
            membership=membership,
        )

    @app.patch(
        "/api/v1/companies/{company_id}/members/{membership_id}/status",
        response_model=MemberResponse,
    )
    def set_member_status(
        company_id: str,
        membership_id: str,
        body: MemberStatusRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, membership, changed = AccessLifecycleService.set_member_status(
            session,
            company_id=company_id,
            membership_id=membership_id,
            status=body.status,
            actor_membership_id=context.membership_id,
        )
        user = session.get(User, membership.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="成员用户不存在")
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.status.update",
                target_type="company_membership",
                target_id=membership.id,
                before_summary={"status": before.value},
                after_summary={"status": membership.status.value},
                request_id=request.state.request_id,
            )
        return _member_response(
            session,
            company_id=company_id,
            user=user,
            membership=membership,
        )

    @app.get(
        "/api/v1/companies/{company_id}/roles",
        response_model=list[RoleResponse],
    )
    def list_roles(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("users.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return [
            role.as_dict()
            for role in AccessLifecycleService.list_roles(
                session, company_id=company_id
            )
        ]

    @app.post(
        "/api/v1/companies/{company_id}/roles",
        response_model=RoleResponse,
        status_code=201,
    )
    def create_role(
        company_id: str,
        body: CreateRoleRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        role, created = AccessLifecycleService.create_role(
            session,
            company_id=company_id,
            name=body.name,
            description=body.description,
            permission_codes=body.permission_codes,
            actor_membership_id=context.membership_id,
        )
        snapshot = AccessLifecycleService.role_snapshot(session, role=role)
        if created:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.role.create",
                target_type="role",
                target_id=role.id,
                before_summary={},
                after_summary=snapshot.as_dict(),
                request_id=request.state.request_id,
            )
        return snapshot.as_dict()

    @app.put(
        "/api/v1/companies/{company_id}/roles/{role_id}",
        response_model=RoleResponse,
    )
    def update_role(
        company_id: str,
        role_id: str,
        body: UpdateRoleRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, after, changed = AccessLifecycleService.update_role(
            session,
            company_id=company_id,
            role_id=role_id,
            name=body.name,
            description=body.description,
            permission_codes=body.permission_codes,
            actor_membership_id=context.membership_id,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.role.update",
                target_type="role",
                target_id=role_id,
                before_summary=before.as_dict(),
                after_summary=after.as_dict(),
                request_id=request.state.request_id,
            )
        return after.as_dict()

    @app.delete(
        "/api/v1/companies/{company_id}/roles/{role_id}",
        status_code=204,
    )
    def delete_role(
        company_id: str,
        role_id: str,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> None:
        before = AccessLifecycleService.delete_role(
            session,
            company_id=company_id,
            role_id=role_id,
            actor_membership_id=context.membership_id,
        )
        AuditService.append(
            session,
            actor_user_id=context.user_id,
            action="company.role.delete",
            target_type="role",
            target_id=role_id,
            before_summary=before.as_dict(),
            after_summary={},
            request_id=request.state.request_id,
        )

    @app.post(
        "/api/v1/companies/{company_id}/roles/{role_id}/assign",
        status_code=204,
    )
    def assign_role(
        company_id: str,
        role_id: str,
        body: AssignRoleRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> None:
        before = AccessLifecycleService.roles_for_membership(
            session,
            company_id=company_id,
            membership_id=body.membership_id,
        )
        changed = AccessLifecycleService.assign_role(
            session,
            company_id=company_id,
            membership_id=body.membership_id,
            role_id=role_id,
            actor_membership_id=context.membership_id,
        )
        if changed:
            after = AccessLifecycleService.roles_for_membership(
                session,
                company_id=company_id,
                membership_id=body.membership_id,
            )
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.role.assign",
                target_type="company_membership",
                target_id=body.membership_id,
                before_summary={"role_ids": [role.id for role in before]},
                after_summary={"role_ids": [role.id for role in after]},
                request_id=request.state.request_id,
            )

    @app.delete(
        "/api/v1/companies/{company_id}/roles/{role_id}/assignments/{membership_id}",
        status_code=204,
    )
    def unassign_role(
        company_id: str,
        role_id: str,
        membership_id: str,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> None:
        before = AccessLifecycleService.roles_for_membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
        )
        changed = AccessLifecycleService.unassign_role(
            session,
            company_id=company_id,
            membership_id=membership_id,
            role_id=role_id,
            actor_membership_id=context.membership_id,
        )
        if changed:
            after = AccessLifecycleService.roles_for_membership(
                session,
                company_id=company_id,
                membership_id=membership_id,
            )
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.role.unassign",
                target_type="company_membership",
                target_id=membership_id,
                before_summary={"role_ids": [role.id for role in before]},
                after_summary={"role_ids": [role.id for role in after]},
                request_id=request.state.request_id,
            )

    @app.put(
        "/api/v1/companies/{company_id}/members/{membership_id}/roles",
        response_model=MemberResponse,
    )
    def replace_member_roles(
        company_id: str,
        membership_id: str,
        body: ReplaceMemberRolesRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, after, changed = AccessLifecycleService.replace_roles(
            session,
            company_id=company_id,
            membership_id=membership_id,
            role_ids=body.role_ids,
            actor_membership_id=context.membership_id,
            expected_role_ids=body.expected_role_ids,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.roles.replace",
                target_type="company_membership",
                target_id=membership_id,
                before_summary={"role_ids": [role.id for role in before]},
                after_summary={"role_ids": [role.id for role in after]},
                request_id=request.state.request_id,
            )
        membership = AccessLifecycleService.get_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        user = session.get(User, membership.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="成员用户不存在")
        return _member_response(
            session,
            company_id=company_id,
            user=user,
            membership=membership,
        )

    @app.put(
        "/api/v1/companies/{company_id}/members/{membership_id}/access",
        response_model=MemberResponse,
    )
    def replace_member_access(
        company_id: str,
        membership_id: str,
        body: ReplaceMemberAccessRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        (
            before_roles,
            after_roles,
            before_overrides,
            after_overrides,
            changed,
        ) = AccessLifecycleService.replace_member_access(
            session,
            company_id=company_id,
            membership_id=membership_id,
            role_ids=body.role_ids,
            permission_overrides=body.permission_overrides,
            actor_membership_id=context.membership_id,
            expected_role_ids=body.expected_role_ids,
            expected_permission_overrides=body.expected_permission_overrides,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.access.replace",
                target_type="company_membership",
                target_id=membership_id,
                before_summary={
                    "role_ids": [role.id for role in before_roles],
                    "permission_overrides": {
                        code: effect.value for code, effect in before_overrides.items()
                    },
                },
                after_summary={
                    "role_ids": [role.id for role in after_roles],
                    "permission_overrides": {
                        code: effect.value for code, effect in after_overrides.items()
                    },
                },
                request_id=request.state.request_id,
            )
        membership = AccessLifecycleService.get_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        user = session.get(User, membership.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="成员用户不存在")
        return _member_response(
            session,
            company_id=company_id,
            user=user,
            membership=membership,
        )

    @app.put(
        "/api/v1/companies/{company_id}/members/{membership_id}/permissions",
        response_model=MemberResponse,
    )
    def replace_permission_overrides(
        company_id: str,
        membership_id: str,
        body: ReplacePermissionOverridesRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        before, after, changed = AccessLifecycleService.replace_overrides(
            session,
            company_id=company_id,
            membership_id=membership_id,
            overrides=body.overrides,
            actor_membership_id=context.membership_id,
            expected_overrides=body.expected_overrides,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.permissions.replace",
                target_type="company_membership",
                target_id=membership_id,
                before_summary={
                    "overrides": {code: effect.value for code, effect in before.items()}
                },
                after_summary={
                    "overrides": {code: effect.value for code, effect in after.items()}
                },
                request_id=request.state.request_id,
            )
        membership = AccessLifecycleService.get_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        user = session.get(User, membership.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="成员用户不存在")
        return _member_response(
            session,
            company_id=company_id,
            user=user,
            membership=membership,
        )

    @app.put(
        "/api/v1/companies/{company_id}/members/{membership_id}/permission",
        response_model=PermissionOverrideResponse,
    )
    def set_permission_override(
        company_id: str,
        membership_id: str,
        body: PermissionOverrideRequest,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        override, before_effect, changed = AccessLifecycleService.set_override(
            session,
            company_id=company_id,
            membership_id=membership_id,
            permission_code=body.permission_code,
            effect=body.effect,
            actor_membership_id=context.membership_id,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.permission.set",
                target_type="company_membership",
                target_id=membership_id,
                before_summary={
                    "permission_code": body.permission_code,
                    "effect": before_effect.value if before_effect else None,
                },
                after_summary={
                    "permission_code": body.permission_code,
                    "effect": body.effect.value,
                },
                request_id=request.state.request_id,
            )
        return override

    @app.delete(
        "/api/v1/companies/{company_id}/members/{membership_id}/permission/{permission_code}",
        status_code=204,
    )
    def clear_permission_override(
        company_id: str,
        membership_id: str,
        permission_code: str,
        request: Request,
        context: Annotated[TenantContext, Depends(require_permission("users.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ) -> None:
        before_effect, changed = AccessLifecycleService.clear_override(
            session,
            company_id=company_id,
            membership_id=membership_id,
            permission_code=permission_code,
            actor_membership_id=context.membership_id,
        )
        if changed:
            AuditService.append(
                session,
                actor_user_id=context.user_id,
                action="company.member.permission.clear",
                target_type="company_membership",
                target_id=membership_id,
                before_summary={
                    "permission_code": permission_code,
                    "effect": before_effect.value if before_effect else None,
                },
                after_summary={
                    "permission_code": permission_code,
                    "effect": None,
                },
                request_id=request.state.request_id,
            )

    @app.get(
        "/api/v1/companies/{company_id}/models",
        response_model=list[AvailableModelResponse],
    )
    def available_models(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("models.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return ModelGrantService.list_available_models(session, company_id=company_id)

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
        account = session.get(WalletAccount, company_id)
        if account is None:
            raise HTTPException(status_code=404, detail="公司钱包不存在")
        return account

    @app.get(
        "/api/v1/companies/{company_id}/ledger",
        response_model=list[LedgerEntryResponse],
    )
    def ledger(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("billing.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return list(
            session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.company_id == company_id)
                .order_by(LedgerEntry.created_at.desc())
            ).all()
        )

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
        return WalletOperationResponse(wallet=account, ledger_entry=entry)

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
        total, total_amount_cents, items = WalletService.recharge_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
        )
        return RechargeRecordPage(
            page=page,
            page_size=page_size,
            total=total,
            total_amount_cents=total_amount_cents,
            items=items,
        )

    @app.post(
        "/api/v1/companies/{company_id}/assets",
        response_model=InputAssetResponse,
        status_code=201,
    )
    def upload_input_asset(
        company_id: str,
        context: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        file: Annotated[UploadFile, File(description="Private input media")],
        media_type: Annotated[Literal["image", "video", "audio"] | None, Form()] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        return InputAssetService.create_from_upload(
            session,
            store=app.state.input_asset_store,
            company_id=company_id,
            user_id=context.user_id,
            upload=file,
            requested_media_type=media_type,
            max_bytes=settings.input_asset_max_bytes,
            idempotency_key=idempotency_key,
        )

    @app.get(
        "/api/v1/companies/{company_id}/assets",
        response_model=list[InputAssetResponse],
    )
    def list_input_assets(
        company_id: str,
        _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
        status: InputAssetStatus | None = InputAssetStatus.ACTIVE,
        media_type: Literal["image", "video", "audio"] | None = None,
        limit: int = Query(default=200, ge=1, le=500),
    ):
        return InputAssetService.list_company(
            session,
            company_id=company_id,
            status=status,
            media_type=media_type,
            limit=limit,
        )

    def input_asset_access(
        *,
        company_id: str,
        asset_id: str,
        session: Session,
        disposition: Literal["inline", "attachment"],
    ) -> InputAssetAccessResponse:
        asset = InputAssetService.get_company_asset(
            session, company_id=company_id, asset_id=asset_id
        )
        url = InputAssetService.access_url(
            asset=asset,
            store=app.state.input_asset_store,
            signer=app.state.input_asset_signer,
            expires_seconds=settings.input_asset_signed_url_seconds,
            disposition=disposition,
        )
        return InputAssetAccessResponse(
            url=url,
            expires_seconds=settings.input_asset_signed_url_seconds,
        )

    @app.get(
        "/api/v1/companies/{company_id}/assets/{asset_id}/preview",
        response_model=InputAssetAccessResponse,
    )
    def preview_input_asset(
        company_id: str,
        asset_id: str,
        _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return input_asset_access(
            company_id=company_id,
            asset_id=asset_id,
            session=session,
            disposition="inline",
        )

    @app.get(
        "/api/v1/companies/{company_id}/assets/{asset_id}/download",
        response_model=InputAssetAccessResponse,
    )
    def download_input_asset(
        company_id: str,
        asset_id: str,
        _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        return input_asset_access(
            company_id=company_id,
            asset_id=asset_id,
            session=session,
            disposition="attachment",
        )

    @app.delete(
        "/api/v1/companies/{company_id}/assets/{asset_id}",
        status_code=204,
    )
    def disable_input_asset(
        company_id: str,
        asset_id: str,
        _: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        InputAssetService.disable(session, company_id=company_id, asset_id=asset_id)
        return Response(status_code=204)

    @app.get("/api/v1/input-assets/{asset_id}/content", include_in_schema=False)
    def read_signed_input_asset(
        asset_id: str,
        expires: int,
        disposition: Literal["inline", "attachment"],
        signature: str,
        session: Annotated[Session, Depends(get_db, scope="function")],
    ):
        if app.state.input_asset_signer is None:
            raise HTTPException(status_code=404, detail="Input asset does not exist")
        try:
            app.state.input_asset_signer.verify(
                asset_id,
                expires=expires,
                disposition=disposition,
                signature=signature,
            )
        except InputAssetSignatureError:
            raise HTTPException(
                status_code=404, detail="Input asset does not exist"
            ) from None
        asset = InputAssetService.get_signed_asset(session, asset_id=asset_id)
        if asset.storage_backend != "filesystem":
            raise HTTPException(status_code=404, detail="Input asset does not exist")
        try:
            path = app.state.input_asset_store.local_path(asset.object_key)
        except InputAssetStorageError:
            raise HTTPException(
                status_code=404, detail="Input asset does not exist"
            ) from None
        if path is None:
            raise HTTPException(status_code=404, detail="Input asset does not exist")
        filename = quote(asset.original_filename)
        return FileResponse(
            path,
            media_type=asset.content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (f"{disposition}; filename*=UTF-8''{filename}"),
                "X-Content-Type-Options": "nosniff",
            },
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
        normalized_payload, input_assets = InputAssetService.normalize_task_payload(
            session,
            company_id=company_id,
            request_payload=body.request_payload,
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
        )
        if not created:
            return TaskService.response_payload(session, task)
        InputAssetService.link_task(session, task_id=task.id, assets=input_assets)
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
            request_id=request.state.request_id,
            # The outbox stores private asset identities at task creation.
            # The dispatcher signs them once immediately before its first POST.
            resolved_assets=[],
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
        total, items = TaskArtifactService.task_history_page(
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
        return TaskHistoryPage(page=page, page_size=page_size, total=total, items=items)

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
        total, items = TaskArtifactService.artwork_page(
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
        return ArtworkPage(page=page, page_size=page_size, total=total, items=items)

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
        total, total_actual_cost_cents, items = ReportService.task_page(
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
        return TaskReportPage(
            page=page,
            page_size=page_size,
            total=total,
            total_actual_cost_cents=total_actual_cost_cents,
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
        total, total_amount_cents, items = ReportService.consumption_page(
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
        return ConsumptionReportPage(
            page=page,
            page_size=page_size,
            total=total,
            total_amount_cents=total_amount_cents,
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
        response.headers["Cache-Control"] = "private, no-store"
        audit = RelayCapabilityService.audit_catalog(session, catalog=read.catalog)
        return {**audit, "etag": read.etag}

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
                after_summary=_model_audit_summary(after),
                request_id=request.state.request_id,
            )
        return after

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
        before, locked_model, changed, compatibility = (
            RelayCapabilityService.approve_model_revision(
                session,
                model_id=model_id,
                expected_capability_version=body.expected_capability_version,
                relay_model=relay_model,
            )
        )
        after = ModelCatalogService.response(session, model=locked_model)
        if changed:
            AuditService.append(
                session,
                actor_user_id=admin.user_id,
                action="model.relay_capability.approve",
                target_type="model_definition",
                target_id=locked_model.id,
                before_summary=_model_audit_summary(before),
                after_summary=_model_audit_summary(after),
                request_id=request.state.request_id,
            )
        return {
            "model": after,
            "compatibility": compatibility,
            "capability_revision": relay_model.capability_revision,
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
        before, model, changed = ModelCatalogService.publish(
            session,
            model_id=model_id,
            require_relay_capability_revision=(app.state.relay_client is not None),
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
                after_summary=_model_audit_summary(after),
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
        return WalletOperationResponse(wallet=account, ledger_entry=entry)

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
        total, total_amount_cents, items = WalletService.recharge_page(
            session,
            company_id=company_id,
            page=page,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
        )
        return RechargeRecordPage(
            page=page,
            page_size=page_size,
            total=total,
            total_amount_cents=total_amount_cents,
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
        existing = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.model_id == body.model_id,
            )
        )
        before = (
            {
                "enabled": existing.enabled,
                "price_per_second_cents": existing.price_per_second_cents,
                "price_per_item_cents": existing.price_per_item_cents,
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
        grant = ModelGrantService.upsert_grant(
            session,
            company_id=company_id,
            model_id=body.model_id,
            enabled=body.enabled,
            price_per_second_cents=body.price_per_second_cents,
            price_per_item_cents=body.price_per_item_cents,
            config_override=body.config_override,
            call_quota=body.call_quota,
            concurrency_limit=body.concurrency_limit,
            effective_at=body.effective_at,
            expires_at=body.expires_at,
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
        total, total_amount_cents, items = ReportService.consumption_page(
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
        return ConsumptionReportPage(
            page=page,
            page_size=page_size,
            total=total,
            total_amount_cents=total_amount_cents,
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
    app.include_router(relay_telemetry_router)
    app.include_router(personal_workspace_router)
    app.include_router(authentication_router)
    app.include_router(showcase_router)
    return app


app = create_app()
