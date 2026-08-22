from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
    ModelDefinition,
    PersonalWalletAccount,
    TaskArtifact,
    TaskStatus,
)
from ..relay_client import (
    RelayPermanentError,
    RelayTemporaryError,
    validate_bound_artifact_download,
)
from ..relay_backends import relay_callback_url_for_backend
from ..schemas import ArtifactDownloadResponse, ArtifactPreviewResponse
from ..services.personal import (
    PersonalModelService,
    PersonalTaskService,
    PersonalWorkspaceService,
)
from ..services.personal_billing import PersonalWalletService
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
    user: SessionUserResponse
    personal: PersonalSurfaceResponse
    companies: list[CompanySurfaceResponse]
    platform_admin: bool


class PersonalMeResponse(StrictModel):
    workspace_id: str
    user: SessionUserResponse
    capabilities: PersonalCapabilities


class PersonalWalletResponse(StrictModel):
    workspace_id: str
    available_points: int
    reserved_points: int


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
    effective_capabilities: dict[str, Any]


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


class PersonalTaskPage(StrictModel):
    items: list[PersonalTaskResponse]
    total: int
    page: int
    page_size: int


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


class PersonalArtworkPage(StrictModel):
    items: list[PersonalArtworkResponse]
    total: int
    page: int
    page_size: int


def _workspace(session: Session, context: UserContext):
    return PersonalWorkspaceService.ensure(session, user_id=context.user_id)


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
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    return PersonalWorkspaceService.surfaces(session, user_id=context.user_id)


@router.get("/api/v1/personal/me", response_model=PersonalMeResponse)
def personal_me(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    surfaces = PersonalWorkspaceService.surfaces(
        session, user_id=context.user_id
    )
    return {
        "workspace_id": surfaces["personal"]["workspace_id"],
        "user": surfaces["user"],
        "capabilities": surfaces["personal"]["capabilities"],
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
    "/internal/personal/wallets/{workspace_id}/credit",
    response_model=InternalPersonalCreditResponse,
)
def credit_personal_wallet(
    workspace_id: str,
    body: InternalPersonalCreditRequest,
    _: Annotated[None, Depends(require_internal_service)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
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
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    _workspace(session, context)
    return PersonalModelService.list_available(session)


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
    settings = request.app.state.settings
    relay_affinity = request.app.state.relay_backend_registry.default_affinity
    task, created = PersonalTaskService.create(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
        model_id=body.model_id,
        request_payload=body.request_payload,
        idempotency_key=body.idempotency_key,
        expected_capability_version=body.expected_capability_version,
        expected_quote_revision=body.expected_quote_revision,
        require_quote_revision=runtime_settings_are_protected(settings),
        require_relay_capability_revision=(request.app.state.relay_client is not None),
        relay_backend_id=relay_affinity.backend_id,
        relay_contract_revision=relay_affinity.contract_revision,
    )
    if not created:
        return PersonalTaskService.response_payloads(session, [task])[0]
    if task.quote_points is None:
        raise RuntimeError("personal task quote was not persisted")
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
        request_id=request.state.request_id,
        resolved_assets=[],
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
