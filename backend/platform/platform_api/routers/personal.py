from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import runtime_settings_are_protected

from ..dependencies import (
    UserContext,
    get_db,
    get_user_context,
    require_internal_service,
)
from ..models import (
    GenerationTask,
    InputAssetStatus,
    ModelDefinition,
    PersonalWalletAccount,
    PersonalWorkspace,
    TaskArtifact,
    TaskStatus,
    User,
)
from ..relay_client import (
    RelayPermanentError,
    RelayTemporaryError,
    validate_bound_artifact_download,
)
from ..relay_backends import relay_callback_url_for_backend
from ..platform_owner_identity import is_platform_owner_identity
from ..schemas import (
    ArtifactDownloadResponse,
    ArtifactPreviewResponse,
    GenerationModeReadinessResponse,
    InputAssetAccessResponse,
    InputAssetResponse,
)
from ..services.input_assets import InputAssetService
from ..services.director_shot_packages import DirectorShotPackageService
from ..services.personal import (
    PersonalModelService,
    PersonalTaskService,
    PersonalWorkspaceService,
)
from ..services.personal_billing import PersonalWalletService
from ..services.commercial_pricing import CommercialPricingPolicy
from ..services.account_partition import AccountPartitionService
from ..services.errors import NotFoundError
from ..services.personal_downloads import PersonalDownloadRecordService
from ..services.relay_outbox import RelayOutboxService


router = APIRouter(tags=["personal-workspace"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonalCapabilities(StrictModel):
    generation: bool
    models: bool
    tasks: bool
    artworks: bool
    task_cancel: bool
    assets: bool
    artifact_access: bool
    publishing: bool


class SessionUserResponse(StrictModel):
    id: str
    email: str
    display_name: str


class PersonalSurfaceResponse(StrictModel):
    kind: Literal["personal"]
    workspace_id: str
    label: str
    capabilities: PersonalCapabilities


class CompanySurfaceResponse(StrictModel):
    kind: Literal["company"]
    company_id: str
    name: str
    status: str


class SessionSurfacesResponse(StrictModel):
    account_type: Literal["personal", "company", "platform_admin", "unavailable"]
    user: SessionUserResponse
    personal: PersonalSurfaceResponse | None
    companies: list[CompanySurfaceResponse]
    platform_admin: bool
    active_product_context: Literal["personal", "company", "platform"] | None
    available_product_contexts: list[Literal["personal", "company", "platform"]]


class PersonalMeResponse(StrictModel):
    workspace_id: str
    user: SessionUserResponse
    capabilities: PersonalCapabilities
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class PersonalWalletResponse(StrictModel):
    workspace_id: str
    available_points: int
    reserved_points: int
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class InternalPersonalCreditRequest(StrictModel):
    amount_points: int = Field(gt=0, le=9_000_000_000_000_000)
    idempotency_key: str = Field(min_length=8, max_length=120)
    note: str = Field(min_length=1, max_length=240)


class PersonalLedgerEntryResponse(StrictModel):
    id: str
    workspace_id: str
    kind: str
    amount_points: int
    available_delta_points: int
    reserved_delta_points: int
    idempotency_key: str
    task_id: str | None
    note: str
    created_at: datetime
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class InternalPersonalCreditResponse(StrictModel):
    wallet: PersonalWalletResponse
    ledger_entry: PersonalLedgerEntryResponse
    created: bool


class PersonalModelResponse(StrictModel):
    id: str
    slug: str
    display_name: str
    billing_mode: Literal["per_second", "per_item"]
    unit_price_points: int
    capability_version: int
    quote_revision: str
    call_quota: int | None
    concurrency_limit: int | None
    effective_capabilities: dict[str, Any]
    readiness_checked_at: datetime
    mode_readiness: dict[str, GenerationModeReadinessResponse]
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class PersonalModelCatalogUnavailableReason(StrictModel):
    code: Literal[
        "model_unpublished",
        "model_disabled",
        "relay_capability_unapproved",
        "personal_distribution_unconfigured",
        "personal_distribution_disabled",
        "personal_price_unavailable",
        "personal_capability_unavailable",
    ]
    message: str = Field(min_length=1, max_length=200)


class PersonalModelCatalogEntryResponse(StrictModel):
    id: str
    slug: str
    display_name: str
    billing_mode: Literal["per_second", "per_item"]
    capability_version: int
    available: bool
    unavailable_reason: PersonalModelCatalogUnavailableReason | None
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class CreatePersonalTaskRequest(StrictModel):
    model_id: str = Field(min_length=1, max_length=36)
    expected_capability_version: int | None = Field(default=None, ge=1)
    expected_quote_revision: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    idempotency_key: str = Field(min_length=8, max_length=120)
    request_payload: dict[str, Any] = Field(default_factory=dict)


class PersonalTaskArtifactResponse(StrictModel):
    artifact_id: str | None = None
    asset_id: str
    media_type: str
    content_type: str
    size_bytes: int
    sha256: str


class PersonalTaskResponse(StrictModel):
    id: str
    idempotency_key: str
    workspace_id: str
    user_id: str
    model_id: str
    status: TaskStatus
    request_payload: dict[str, Any]
    quote_points: int
    pricing_snapshot: dict[str, Any]
    capability_snapshot: dict[str, Any]
    reserved_points: int
    actual_cost_points: int | None
    relay_job_id: str | None
    output_artifacts: list[PersonalTaskArtifactResponse]
    failure_reason: str | None
    relay_error_snapshot: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class PersonalTaskPage(StrictModel):
    items: list[PersonalTaskResponse]
    total: int
    page: int
    page_size: int
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class PersonalArtworkResponse(StrictModel):
    artifact_id: str
    task_id: str
    workspace_id: str
    asset_id: str
    output_index: int
    media_type: Literal["image", "video"]
    content_type: str
    size_bytes: int
    sha256: str
    model_id: str
    model_display_name: str
    request_payload: dict[str, Any]
    actual_cost_points: int
    download_evidence_available: bool
    download_status: Literal["not_downloaded", "issued"]
    download_issue_count: int
    download_completed_count: int
    downloaded: bool
    last_download_issued_at: datetime | None
    last_download_completed_at: datetime | None
    created_at: datetime
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2


class PersonalArtworkPage(StrictModel):
    items: list[PersonalArtworkResponse]
    total: int
    page: int
    billing_unit: Literal["POINT"] = "POINT"
    billing_version: Literal[2] = 2
    page_size: int


def _workspace(session: Session, context: UserContext):
    active_context = context.active_product_context
    if active_context is None:
        user = session.get(User, context.user_id)
        if user is not None and user.account_type.value == "personal":
            from ..models import ProductContext

            active_context = ProductContext.PERSONAL
    return PersonalWorkspaceService.require_for_product_context(
        session,
        user_id=context.user_id,
        active_product_context=active_context,
        external_identity_id=context.external_identity_id,
    )


def _owned_artifact(
    session: Session,
    *,
    workspace_id: str,
    user_id: str,
    task_id: str,
    asset_id: str,
) -> tuple[GenerationTask, TaskArtifact]:
    """Return an exact personal-scope artifact, hiding cross-owner existence."""

    row = session.execute(
        select(GenerationTask, TaskArtifact)
        .join(TaskArtifact, TaskArtifact.task_id == GenerationTask.id)
        .where(
            GenerationTask.id == task_id,
            GenerationTask.company_id.is_(None),
            GenerationTask.personal_workspace_id == workspace_id,
            GenerationTask.user_id == user_id,
            GenerationTask.status == TaskStatus.SUCCEEDED,
            GenerationTask.relay_job_id.is_not(None),
            TaskArtifact.company_id.is_(None),
            TaskArtifact.personal_workspace_id == workspace_id,
            TaskArtifact.asset_id == asset_id,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact does not exist")
    return row


@router.get(
    "/api/v1/session/surfaces",
    response_model=SessionSurfacesResponse,
)
def session_surfaces(
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    return PersonalWorkspaceService.surfaces(
        session,
        user_id=context.user_id,
        active_product_context=context.active_product_context,
        external_identity_id=context.external_identity_id,
        platform_owner=is_platform_owner_identity(
            issuer=context.external_issuer,
            subject=context.external_subject,
            configured_issuer=request.app.state.settings.oidc_issuer,
            configured_subjects=request.app.state.settings.platform_owner_user_ids,
        ),
    )


@router.get("/api/v1/personal/me", response_model=PersonalMeResponse)
def personal_me(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    surfaces = PersonalWorkspaceService.surfaces(
        session,
        user_id=context.user_id,
        active_product_context=context.active_product_context,
        external_identity_id=context.external_identity_id,
        platform_owner=(
            context.cookie_principal is not None
            and context.cookie_principal.user.is_platform_admin
        ),
    )
    personal = surfaces["personal"]
    assert personal is not None
    return {
        "workspace_id": workspace.id,
        "user": surfaces["user"],
        "capabilities": personal["capabilities"],
    }


@router.get("/api/v1/personal/wallet", response_model=PersonalWalletResponse)
def personal_wallet(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    account = session.get(PersonalWalletAccount, workspace.id)
    if account is None:
        # ensure() provisions it with the workspace in the same transaction.
        account = PersonalWalletService._locked_account(session, workspace.id)
    return account


@router.post(
    "/api/v1/personal/assets",
    response_model=InputAssetResponse,
    status_code=201,
)
def upload_personal_input_asset(
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    file: Annotated[UploadFile, File(description="Private personal input media")],
    media_type: Annotated[
        Literal["image", "video", "audio"] | None,
        Form(),
    ] = None,
    normalization_profile: Annotated[
        Literal["director_previs_mp4_v1"] | None,
        Form(),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
):
    workspace = _workspace(session, context)
    return InputAssetService.create_from_upload(
        session,
        store=request.app.state.input_asset_store,
        company_id=None,
        personal_workspace_id=workspace.id,
        user_id=context.user_id,
        upload=file,
        requested_media_type=media_type,
        max_bytes=request.app.state.settings.input_asset_max_bytes,
        idempotency_key=idempotency_key,
        normalization_profile=normalization_profile,
    )


@router.get(
    "/api/v1/personal/assets",
    response_model=list[InputAssetResponse],
)
def list_personal_input_assets(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    status: InputAssetStatus | None = InputAssetStatus.ACTIVE,
    media_type: Literal["image", "video", "audio"] | None = None,
    limit: int = Query(default=200, ge=1, le=500),
):
    workspace = _workspace(session, context)
    return InputAssetService.list_personal(
        session,
        personal_workspace_id=workspace.id,
        status=status,
        media_type=media_type,
        limit=limit,
    )


def _personal_input_asset_access(
    *,
    request: Request,
    workspace_id: str,
    asset_id: str,
    session: Session,
    disposition: Literal["inline", "attachment"],
) -> InputAssetAccessResponse:
    asset = InputAssetService.get_personal_asset(
        session,
        personal_workspace_id=workspace_id,
        asset_id=asset_id,
    )
    expires_seconds = request.app.state.settings.input_asset_signed_url_seconds
    return InputAssetAccessResponse(
        url=InputAssetService.access_url(
            asset=asset,
            store=request.app.state.input_asset_store,
            signer=request.app.state.input_asset_signer,
            expires_seconds=expires_seconds,
            disposition=disposition,
        ),
        expires_seconds=expires_seconds,
    )


@router.get(
    "/api/v1/personal/assets/{asset_id}/preview",
    response_model=InputAssetAccessResponse,
)
def preview_personal_input_asset(
    asset_id: str,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    return _personal_input_asset_access(
        request=request,
        workspace_id=workspace.id,
        asset_id=asset_id,
        session=session,
        disposition="inline",
    )


@router.get(
    "/api/v1/personal/assets/{asset_id}/download",
    response_model=InputAssetAccessResponse,
)
def download_personal_input_asset(
    asset_id: str,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    return _personal_input_asset_access(
        request=request,
        workspace_id=workspace.id,
        asset_id=asset_id,
        session=session,
        disposition="attachment",
    )


@router.delete(
    "/api/v1/personal/assets/{asset_id}",
    status_code=204,
)
def disable_personal_input_asset(
    asset_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    InputAssetService.disable_personal(
        session,
        personal_workspace_id=workspace.id,
        asset_id=asset_id,
    )
    return Response(status_code=204)


@router.post(
    "/internal/personal/wallets/{workspace_id}/credit",
    response_model=InternalPersonalCreditResponse,
)
def credit_personal_wallet(
    workspace_id: str,
    body: InternalPersonalCreditRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = session.get(PersonalWorkspace, workspace_id)
    if workspace is None:
        raise NotFoundError("个人空间不存在")
    user = session.get(User, workspace.user_id)
    if user is None:
        raise NotFoundError("个人用户不存在")
    if workspace.owner_self_identity_id is not None:
        AccountPartitionService.require_owner_self_personal(
            session,
            user=user,
            external_identity_id=workspace.owner_self_identity_id,
        )
    else:
        AccountPartitionService.require_personal(session, user=user)
    account, entry, created = PersonalWalletService.credit(
        session,
        workspace_id=workspace_id,
        amount_points=body.amount_points,
        idempotency_key=body.idempotency_key,
        note=body.note,
    )
    return {"wallet": account, "ledger_entry": entry, "created": created}


@router.get(
    "/api/v1/personal/models",
    response_model=list[PersonalModelResponse],
)
def personal_models(
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    return PersonalModelService.list_available(
        session,
        workspace_id=workspace.id,
        require_relay_approval=(request.app.state.relay_client is not None),
        release_evidence=request.app.state.read_customer_model_release_evidence(
            request_id=request.state.request_id
        ),
    )


@router.get(
    "/api/v1/personal/model-catalog",
    response_model=list[PersonalModelCatalogEntryResponse],
)
def personal_model_catalog(
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    _workspace(session, context)
    return PersonalModelService.list_catalog(
        session,
        require_relay_approval=(request.app.state.relay_client is not None),
    )


@router.post(
    "/api/v1/personal/tasks",
    response_model=PersonalTaskResponse,
    status_code=201,
)
def create_personal_task(
    request: Request,
    body: CreatePersonalTaskRequest,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    replay_payload = DirectorShotPackageService.canonicalize_task_payload(
        body.request_payload
    )
    replay_payload = InputAssetService.canonicalize_task_payload(
        replay_payload
    )
    replay = PersonalTaskService.idempotent_replay(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        model_id=body.model_id,
        request_payload=replay_payload,
        idempotency_key=body.idempotency_key,
    )
    if replay is not None:
        return PersonalTaskService.response_payloads(session, [replay])[0]
    commercial_readiness = (
        request.app.state.require_models_release_ready(
            session, model_ids={body.model_id}, request_id=request.state.request_id,
        ).get(body.model_id)
        if CommercialPricingPolicy.task_needs_live_evidence(session, model_id=body.model_id)
        else None
    )
    normalized_payload, input_assets = InputAssetService.normalize_task_payload(
        session,
        company_id=None,
        personal_workspace_id=workspace.id,
        request_payload=replay_payload,
    )
    director_shot_package = DirectorShotPackageService.require_for_task(
        session,
        company_id=None,
        personal_workspace_id=workspace.id,
        request_payload=normalized_payload,
    )
    settings = request.app.state.settings
    relay_affinity = request.app.state.relay_backend_registry.default_affinity
    task, created = PersonalTaskService.create(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        model_id=body.model_id,
        request_payload=normalized_payload,
        idempotency_key=body.idempotency_key,
        expected_capability_version=body.expected_capability_version,
        expected_quote_revision=body.expected_quote_revision,
        require_quote_revision=runtime_settings_are_protected(settings),
        require_relay_capability_revision=(request.app.state.relay_client is not None),
        relay_backend_id=relay_affinity.backend_id,
        relay_contract_revision=relay_affinity.contract_revision,
        expected_commercial_release_snapshot=(
            commercial_readiness.expected_snapshot
            if commercial_readiness is not None else None
        ),
    )
    if not created:
        return PersonalTaskService.response_payloads(session, [task])[0]
    if task.quote_points is None:
        raise RuntimeError("personal task quote was not persisted")
    InputAssetService.link_task(session, task_id=task.id, assets=input_assets)
    DirectorShotPackageService.link_task(
        session,
        task=task,
        package=director_shot_package,
    )
    PersonalWalletService.reserve(
        session,
        workspace_id=workspace.id,
        task_id=task.id,
        amount_points=task.quote_points,
        idempotency_key=body.idempotency_key,
    )
    model = session.get(ModelDefinition, task.model_id)
    if model is None:
        raise RuntimeError("personal task model disappeared during admission")
    RelayOutboxService.enqueue(
        session,
        task=task,
        model=model,
        expected_commercial_release_snapshot=(
            commercial_readiness.expected_snapshot if commercial_readiness is not None else None
        ),
        request_id=request.state.request_id,
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
    return PersonalTaskService.response_payloads(session, [task])[0]


@router.get("/api/v1/personal/tasks", response_model=PersonalTaskPage)
def personal_tasks(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: TaskStatus | None = None,
    model_id: str | None = None,
    media_type: Literal["image", "video"] | None = None,
):
    workspace = _workspace(session, context)
    total, items = PersonalTaskService.page(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        page=page,
        page_size=page_size,
        status=status,
        model_id=model_id,
        media_type=media_type,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/api/v1/personal/tasks/{task_id}",
    response_model=PersonalTaskResponse,
)
def personal_task(
    task_id: str,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    return PersonalTaskService.get(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        task_id=task_id,
    )


@router.get(
    "/api/v1/personal/tasks/{task_id}/artifacts/{asset_id}/preview",
    response_model=ArtifactPreviewResponse,
)
def personal_artifact_preview(
    task_id: str,
    asset_id: str,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    task, artifact = _owned_artifact(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        task_id=task_id,
        asset_id=asset_id,
    )
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
    try:
        preview = request.app.state.resolve_task_relay_client(
            task
        ).get_artifact_download(
            task.relay_job_id,
            asset_id,
            request_id=request.state.request_id,
        )
        validate_bound_artifact_download(
            preview,
            production=runtime_settings_are_protected(request.app.state.settings),
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


@router.get(
    "/api/v1/personal/tasks/{task_id}/artifacts/{asset_id}/download",
    response_model=ArtifactDownloadResponse,
)
def personal_artifact_download(
    task_id: str,
    asset_id: str,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    workspace = _workspace(session, context)
    task, _ = _owned_artifact(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        task_id=task_id,
        asset_id=asset_id,
    )
    try:
        download = request.app.state.resolve_task_relay_client(
            task
        ).get_artifact_download(
            task.relay_job_id,
            asset_id,
            request_id=request.state.request_id,
        )
        # Personal downloads never accept the migration-era unbound Relay
        # response. The provider's temporary URL is not exposed: this is the
        # Relay-issued URL for the verified platform-controlled OBS object.
        storage_binding = validate_bound_artifact_download(
            download,
            production=runtime_settings_are_protected(request.app.state.settings),
            allow_legacy=False,
        )
        assert storage_binding is not None
        record = PersonalDownloadRecordService.append(
            session,
            workspace_id=workspace.id,
            task_id=task.id,
            asset_id=asset_id,
            requested_by_user_id=context.user_id,
            expires_seconds=download.expires_seconds,
            request_id=request.state.request_id,
            storage_binding=storage_binding,
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


@router.get("/api/v1/personal/artworks", response_model=PersonalArtworkPage)
def personal_artworks(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    model_id: str | None = None,
    media_type: Literal["image", "video"] | None = None,
):
    workspace = _workspace(session, context)
    total, items = PersonalTaskService.artworks_page(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        page=page,
        page_size=page_size,
        model_id=model_id,
        media_type=media_type,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
