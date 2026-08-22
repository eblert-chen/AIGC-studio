from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..artifact_copy import HttpArtifactContentSource
from ..asset_storage import InputAssetStorageError
from ..config import runtime_settings_are_protected
from ..dependencies import PlatformAdminContext, get_db, require_platform_admin
from ..models import (
    GenerationTask,
    ShowcaseDraftItem,
    ShowcaseMedia,
    ShowcasePublicationEvent,
    ShowcaseRelease,
    ShowcaseReleaseItem,
    TaskArtifact,
    TaskStatus,
)
from ..relay_client import (
    RelayPermanentError,
    RelayTemporaryError,
    validate_bound_artifact_download,
)
from ..showcase_schemas import (
    ShowcaseAdminResponse,
    ShowcaseDraftItemResponse,
    ShowcaseHomeResponse,
    ShowcaseItemCreateRequest,
    ShowcaseItemUpdateRequest,
    ShowcaseMediaResponse,
    ShowcaseMutationResponse,
    ShowcaseOrderRequest,
    ShowcaseOrderResponse,
    ShowcasePublicItem,
    ShowcasePublishRequest,
    ShowcaseReleaseResponse,
    ShowcaseRetireRequest,
    ShowcaseUnpublishResponse,
)
from ..services.audit import AuditService
from ..services.errors import ConflictError
from ..services.showcase import ShowcaseService


router = APIRouter(tags=["showcase"])


def _require_owner(context: PlatformAdminContext) -> None:
    if not context.is_platform_owner:
        raise HTTPException(status_code=403, detail="Platform owner access is required")


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or "showcase-request")


def _media_url(media_id: str) -> str:
    return f"/api/v1/showcase/media/{media_id}/content"


def _admin_media_url(media_id: str) -> str:
    return f"/api/v1/platform-admin/showcase/media/{media_id}/content"


def _delivery_filename(media: ShowcaseMedia) -> str:
    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
    }.get(media.content_type, ".bin")
    return f"showcase-{media.sha256[:12]}{suffix}"


def _media_response(media: ShowcaseMedia) -> ShowcaseMediaResponse:
    return ShowcaseMediaResponse(
        id=media.id,
        source_task_artifact_id=media.source_task_artifact_id,
        original_filename=media.original_filename,
        media_type=media.media_type,
        content_type=media.content_type,
        size_bytes=media.size_bytes,
        sha256=media.sha256,
        created_at=media.created_at,
        content_url=_admin_media_url(media.id),
    )


def _draft_item_response(
    session: Session, item: ShowcaseDraftItem
) -> ShowcaseDraftItemResponse:
    media = session.get(ShowcaseMedia, item.media_id)
    if media is None:  # restrictive foreign key; fail closed on a corrupt DB
        raise HTTPException(status_code=500, detail="Showcase media index is invalid")
    return ShowcaseDraftItemResponse(
        id=item.id,
        media_id=item.media_id,
        title=item.title,
        section=item.section,
        category=item.category,
        alt_text=item.alt_text,
        public_prompt=item.public_prompt,
        aspect_ratio=item.aspect_ratio,
        is_hero=item.is_hero,
        sort_order=item.sort_order,
        retired_at=item.retired_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        media=_media_response(media),
    )


def _release_response(
    release: ShowcaseRelease,
    *,
    item_count: int,
) -> ShowcaseReleaseResponse:
    return ShowcaseReleaseResponse(
        id=release.id,
        version=release.version,
        draft_version=release.draft_version,
        publication_version=release.publication_version,
        published_by_user_id=release.published_by_user_id,
        item_count=item_count,
        source_release_id=release.source_release_id,
        release_note=release.release_note,
        manifest_sha256=release.manifest_sha256,
        published_at=release.published_at,
    )


def _unpublish_response(
    event: ShowcasePublicationEvent,
) -> ShowcaseUnpublishResponse:
    return ShowcaseUnpublishResponse(
        id=event.id,
        actor_user_id=event.actor_user_id,
        previous_release_id=event.previous_release_id,
        publication_version=event.publication_version,
        release_note=event.release_note,
        unpublished_at=event.unpublished_at,
    )


@router.get(
    "/api/v1/platform-admin/showcase",
    response_model=ShowcaseAdminResponse,
)
def get_showcase_admin(
    response: Response,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> ShowcaseAdminResponse:
    _require_owner(context)
    state = ShowcaseService.admin_state(session)
    response.headers["Cache-Control"] = "private, no-store"
    return ShowcaseAdminResponse(
        draft_version=state["channel"].draft_version,
        publication_version=state["channel"].publication_version,
        has_unpublished_changes=state["has_unpublished_changes"],
        current_release=(
            _release_response(
                state["current_release"],
                item_count=state["release_item_counts"].get(
                    state["current_release"].id,
                    0,
                ),
            )
            if state["current_release"] is not None
            else None
        ),
        last_unpublished_event=(
            _unpublish_response(state["last_unpublished_event"])
            if state["last_unpublished_event"] is not None
            else None
        ),
        publication_events=[
            _unpublish_response(event) for event in state["publication_events"]
        ],
        items=[_draft_item_response(session, item) for item in state["items"]],
        media=[_media_response(media) for media in state["media"]],
        releases=[
            _release_response(
                release,
                item_count=state["release_item_counts"].get(release.id, 0),
            )
            for release in state["releases"]
        ],
    )


@router.post(
    "/api/v1/platform-admin/showcase/media",
    response_model=ShowcaseMediaResponse,
    status_code=201,
)
def create_showcase_media(
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    file: Annotated[UploadFile | None, File()] = None,
    source_task_artifact_id: Annotated[str | None, Form()] = None,
) -> ShowcaseMediaResponse:
    _require_owner(context)
    if (file is None) == (source_task_artifact_id is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one file or source_task_artifact_id",
        )
    created = False
    if file is not None:
        media, created = ShowcaseService.create_media_from_upload(
            session,
            store=request.app.state.showcase_media_store,
            user_id=context.user_id,
            idempotency_key=idempotency_key,
            upload=file,
            max_bytes=request.app.state.settings.input_asset_max_bytes,
        )
    else:
        assert source_task_artifact_id is not None
        row = session.execute(
            select(TaskArtifact, GenerationTask)
            .join(GenerationTask, GenerationTask.id == TaskArtifact.task_id)
            .where(
                TaskArtifact.id == source_task_artifact_id,
                GenerationTask.user_id == context.user_id,
                GenerationTask.status == TaskStatus.SUCCEEDED,
                GenerationTask.company_id.is_(None),
                GenerationTask.personal_workspace_id.is_not(None),
                TaskArtifact.company_id.is_(None),
                TaskArtifact.personal_workspace_id
                == GenerationTask.personal_workspace_id,
            )
        ).one_or_none()
        if row is None or not row[1].relay_job_id:
            # Do not disclose whether another tenant/user owns this identifier.
            raise HTTPException(status_code=404, detail="Verified artifact does not exist")
        artifact, task = row
        existing = session.scalar(
            select(ShowcaseMedia).where(
                ShowcaseMedia.source_task_artifact_id == artifact.id
            )
        )
        if existing is not None:
            key_owner = session.scalar(
                select(ShowcaseMedia).where(
                    ShowcaseMedia.created_by_user_id == context.user_id,
                    ShowcaseMedia.idempotency_key == idempotency_key,
                )
            )
            if (
                (key_owner is not None and key_owner.id != existing.id)
                or existing.created_by_user_id != context.user_id
                or existing.idempotency_key != idempotency_key
                or existing.source_task_artifact_id != artifact.id
            ):
                raise ConflictError(
                    "Idempotency-Key was already used for different showcase media"
                )
            media = existing
        else:
            client = request.app.state.resolve_task_relay_client(task)
            try:
                download = client.get_artifact_download(
                    task.relay_job_id,
                    artifact.asset_id,
                    request_id=_request_id(request),
                )
                storage_binding = validate_bound_artifact_download(
                    download,
                    production=runtime_settings_are_protected(
                        request.app.state.settings
                    ),
                    allow_legacy=(
                        request.app.state.settings.allow_legacy_relay_artifact_download_response
                    ),
                )
                allowed_hosts = (
                    {"localhost", "127.0.0.1", "::1"}
                    if storage_binding is None
                    else {
                        storage_binding.endpoint_host,
                        f"{storage_binding.bucket}.{storage_binding.endpoint_host}",
                    }
                )
                content_source = HttpArtifactContentSource(
                    str(download.url),
                    timeout_seconds=(
                        request.app.state.settings.artifact_promotion_download_timeout_seconds
                    ),
                    allowed_hosts=allowed_hosts,
                )
                media, created = ShowcaseService.create_media_from_artifact(
                    session,
                    store=request.app.state.showcase_media_store,
                    user_id=context.user_id,
                    idempotency_key=idempotency_key,
                    source_task_artifact_id=artifact.id,
                    asset_id=artifact.asset_id,
                    content_type=artifact.content_type,
                    expected_size_bytes=artifact.size_bytes,
                    expected_sha256=artifact.sha256,
                    content_source=content_source,
                    max_bytes=request.app.state.settings.input_asset_max_bytes,
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
            action="showcase.media.create",
            target_type="showcase_media",
            target_id=media.id,
            before_summary={},
            after_summary={
                "source_task_artifact_id": media.source_task_artifact_id,
                "media_type": media.media_type,
                "content_type": media.content_type,
                "size_bytes": media.size_bytes,
                "sha256": media.sha256,
            },
            request_id=_request_id(request),
        )
    return _media_response(media)


@router.post(
    "/api/v1/platform-admin/showcase/items",
    response_model=ShowcaseMutationResponse,
    status_code=201,
)
def create_showcase_item(
    body: ShowcaseItemCreateRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> ShowcaseMutationResponse:
    _require_owner(context)
    values = body.model_dump(exclude={"expected_draft_version"})
    item, version = ShowcaseService.create_item(
        session,
        user_id=context.user_id,
        expected_draft_version=body.expected_draft_version,
        values=values,
        request_id=_request_id(request),
    )
    return ShowcaseMutationResponse(
        draft_version=version,
        item=_draft_item_response(session, item),
    )


@router.put(
    "/api/v1/platform-admin/showcase/items/{item_id}",
    response_model=ShowcaseMutationResponse,
)
def update_showcase_item(
    item_id: str,
    body: ShowcaseItemUpdateRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> ShowcaseMutationResponse:
    _require_owner(context)
    values = body.model_dump(exclude={"expected_draft_version"})
    item, version = ShowcaseService.update_item(
        session,
        item_id=item_id,
        user_id=context.user_id,
        expected_draft_version=body.expected_draft_version,
        values=values,
        request_id=_request_id(request),
    )
    return ShowcaseMutationResponse(
        draft_version=version,
        item=_draft_item_response(session, item),
    )


@router.post(
    "/api/v1/platform-admin/showcase/items/{item_id}/retire",
    response_model=ShowcaseMutationResponse,
)
def retire_showcase_item(
    item_id: str,
    body: ShowcaseRetireRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> ShowcaseMutationResponse:
    _require_owner(context)
    item, version = ShowcaseService.retire_item(
        session,
        item_id=item_id,
        user_id=context.user_id,
        expected_draft_version=body.expected_draft_version,
        request_id=_request_id(request),
    )
    return ShowcaseMutationResponse(
        draft_version=version,
        item=_draft_item_response(session, item),
    )


@router.put(
    "/api/v1/platform-admin/showcase/order",
    response_model=ShowcaseOrderResponse,
)
def reorder_showcase_items(
    body: ShowcaseOrderRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> ShowcaseOrderResponse:
    _require_owner(context)
    version = ShowcaseService.reorder_items(
        session,
        user_id=context.user_id,
        expected_draft_version=body.expected_draft_version,
        item_ids=body.item_ids,
        request_id=_request_id(request),
    )
    return ShowcaseOrderResponse(draft_version=version, item_ids=body.item_ids)


@router.post(
    "/api/v1/platform-admin/showcase/publish",
    response_model=ShowcaseReleaseResponse,
)
def publish_showcase(
    body: ShowcasePublishRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ShowcaseReleaseResponse:
    _require_owner(context)
    release = ShowcaseService.publish(
        session,
        store=request.app.state.showcase_media_store,
        user_id=context.user_id,
        idempotency_key=idempotency_key,
        expected_draft_version=body.expected_draft_version,
        expected_publication_version=body.expected_publication_version,
        release_note=body.release_note,
        request_id=_request_id(request),
    )
    item_count = int(
        session.scalar(
            select(func.count(ShowcaseReleaseItem.id)).where(
                ShowcaseReleaseItem.release_id == release.id
            )
        )
        or 0
    )
    return _release_response(release, item_count=item_count)


@router.post(
    "/api/v1/platform-admin/showcase/releases/{release_id}/rollback",
    response_model=ShowcaseReleaseResponse,
)
def rollback_showcase(
    release_id: str,
    body: ShowcasePublishRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ShowcaseReleaseResponse:
    _require_owner(context)
    release = ShowcaseService.rollback(
        session,
        store=request.app.state.showcase_media_store,
        target_release_id=release_id,
        user_id=context.user_id,
        idempotency_key=idempotency_key,
        expected_draft_version=body.expected_draft_version,
        expected_publication_version=body.expected_publication_version,
        release_note=body.release_note,
        request_id=_request_id(request),
    )
    item_count = int(
        session.scalar(
            select(func.count(ShowcaseReleaseItem.id)).where(
                ShowcaseReleaseItem.release_id == release.id
            )
        )
        or 0
    )
    return _release_response(release, item_count=item_count)


@router.post(
    "/api/v1/platform-admin/showcase/unpublish",
    response_model=ShowcaseUnpublishResponse,
)
def unpublish_showcase(
    body: ShowcasePublishRequest,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ShowcaseUnpublishResponse:
    _require_owner(context)
    event = ShowcaseService.unpublish(
        session,
        user_id=context.user_id,
        idempotency_key=idempotency_key,
        expected_draft_version=body.expected_draft_version,
        expected_publication_version=body.expected_publication_version,
        release_note=body.release_note,
        request_id=_request_id(request),
    )
    return _unpublish_response(event)


@router.get("/api/v1/showcase/home", response_model=ShowcaseHomeResponse)
def get_showcase_home(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db, scope="function")],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
):
    release, rows = ShowcaseService.public_release(session)
    digest = (
        hashlib.sha256(
            (
                f"{release.id}\n{release.version}\n"
                f"{release.published_at.isoformat()}\n{release.manifest_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        if release is not None
        else hashlib.sha256(b"showcase-home-empty-v1").hexdigest()
    )
    etag = f'"showcase-{digest}"'
    cache_control = "public, max-age=15, must-revalidate"
    if if_none_match == etag:
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": cache_control,
                "Vary": "Accept-Encoding",
            },
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = cache_control
    response.headers["Vary"] = "Accept-Encoding"
    if release is None:
        return ShowcaseHomeResponse()
    public_items = [
        ShowcasePublicItem(
            id=item.id,
            title=item.title,
            section=item.section,
            category=item.category,
            alt_text=item.alt_text,
            public_prompt=item.public_prompt,
            aspect_ratio=item.aspect_ratio,
            media_type=media.media_type,
            content_type=media.content_type,
            media_url=_media_url(media.id),
        )
        for item, media in rows
    ]
    hero = next(
        (public for public, (item, _) in zip(public_items, rows) if item.is_hero),
        None,
    )
    items = [
        public
        for public, (item, _) in zip(public_items, rows)
        if not item.is_hero
    ]
    return ShowcaseHomeResponse(
        release_id=release.id,
        version=release.version,
        published_at=release.published_at,
        hero=hero,
        items=items,
    )


@router.get("/api/v1/platform-admin/showcase/media/{media_id}/content")
def showcase_admin_media_content(
    media_id: str,
    request: Request,
    context: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
):
    _require_owner(context)
    media = session.get(ShowcaseMedia, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Showcase media does not exist")
    store = request.app.state.showcase_media_store
    try:
        if store.kind == "filesystem":
            path = store.local_path(media.object_key)
            if path is None:
                raise InputAssetStorageError("Showcase media is unavailable")
            return FileResponse(
                path,
                media_type=media.content_type,
                headers={
                    "Cache-Control": "private, no-store",
                    "Content-Disposition": "inline",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        url = store.signed_url(
            media.object_key,
            expires_seconds=300,
            original_filename=_delivery_filename(media),
            disposition="inline",
        )
    except InputAssetStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail="Showcase media is unavailable",
        ) from exc
    if not url:
        raise HTTPException(status_code=503, detail="Showcase media is unavailable")
    return RedirectResponse(
        url,
        status_code=307,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/api/v1/showcase/media/{media_id}/content",
    name="showcase_media_content",
)
def showcase_media_content(
    media_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
):
    media = ShowcaseService.public_media(session, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Published media does not exist")
    etag = f'"sha256-{media.sha256}"'
    cache_control = "public, max-age=15, must-revalidate"
    if if_none_match == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": cache_control},
        )
    store = request.app.state.showcase_media_store
    if store.kind == "filesystem":
        path = store.local_path(media.object_key)
        if path is None:
            raise HTTPException(status_code=503, detail="Showcase media is unavailable")
        return FileResponse(
            path,
            media_type=media.content_type,
            headers={
                "ETag": etag,
                "Cache-Control": cache_control,
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )
    try:
        url = store.signed_url(
            media.object_key,
            expires_seconds=300,
            original_filename=_delivery_filename(media),
            disposition="inline",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Showcase media is unavailable") from exc
    if not url:
        raise HTTPException(status_code=503, detail="Showcase media is unavailable")
    return RedirectResponse(
        url,
        status_code=307,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
