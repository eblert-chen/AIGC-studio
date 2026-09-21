from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..asset_storage import InputAssetSignatureError, InputAssetStorageError
from ..config import Settings
from ..dependencies import TenantContext, get_db, require_permission
from ..models import InputAssetStatus
from ..schemas import InputAssetAccessResponse, InputAssetResponse
from ..services.input_assets import InputAssetService


def create_company_input_assets_router(*, app: FastAPI, settings: Settings) -> APIRouter:
    """Bind asset delivery to this application's store, signer and settings."""
    router = APIRouter()

    @router.post(
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
        normalization_profile: Annotated[
            Literal["director_previs_mp4_v1"] | None,
            Form(),
        ] = None,
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
            normalization_profile=normalization_profile,
        )

    @router.get(
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

    @router.get(
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

    @router.get(
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

    @router.delete(
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

    @router.get("/api/v1/input-assets/{asset_id}/content", include_in_schema=False)
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

    return router
