from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..dependencies import (
    TenantContext,
    UserContext,
    get_db,
    get_user_context,
    require_permission,
)
from ..models import ProductContext, User
from ..services.errors import ConflictError, NotFoundError
from ..services.personal import PersonalWorkspaceService
from ..services.share_links import (
    CreateShareLinkRequest,
    ShareLinkService,
)
from ..services.authentication import request_ip_hash

router = APIRouter(tags=["share-links"])


def _client_ip(request: Request) -> str:
    # Same boundary as the authentication router: forwarded headers are trusted
    # at the ingress, never here, or rate limiting becomes opt-out.
    return request.client.host if request.client is not None else "unknown"


def _ip_hash(request: Request, *, pepper: str | None) -> str:
    return request_ip_hash(_client_ip(request), pepper=pepper)


def _personal_workspace(session: Session, context: UserContext):
    active_context = context.active_product_context
    if active_context is None:
        user = session.get(User, context.user_id)
        if user is not None and user.account_type.value == "personal":
            active_context = ProductContext.PERSONAL
    return PersonalWorkspaceService.require_for_product_context(
        session,
        user_id=context.user_id,
        active_product_context=active_context,
        external_identity_id=context.external_identity_id,
    )


def _domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=exc.message)
    raise exc


@router.post(
    "/api/v1/companies/{company_id}/shares",
    status_code=201,
)
def create_company_share_link(
    company_id: str,
    body: CreateShareLinkRequest,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    if context.company_id != company_id:
        raise HTTPException(status_code=404, detail="Company does not exist")
    try:
        link, raw_token, _created = ShareLinkService.create(
            session,
            company_id=company_id,
            personal_workspace_id=None,
            user_id=context.user_id,
            body=body,
            pepper=request.app.state.settings.jwt_signing_secret,
            request_id=getattr(request.state, "request_id", "system"),
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    payload = ShareLinkService.management_payload(link)
    # The raw token is the secret. It is returned only in this response and
    # cannot be read back from any endpoint.
    payload["url"] = f"/s/{raw_token}" if raw_token else None
    payload["token_returned"] = raw_token is not None
    return payload


@router.get("/api/v1/companies/{company_id}/shares")
def list_company_share_links(
    company_id: str,
    _: Annotated[TenantContext, Depends(require_permission("assets.read"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
    resource_id: str | None = None,
) -> dict[str, Any]:
    links = ShareLinkService.list(
        session,
        company_id=company_id,
        personal_workspace_id=None,
        resource_id=resource_id,
    )
    return {
        "items": [ShareLinkService.management_payload(link) for link in links]
    }


@router.delete("/api/v1/companies/{company_id}/shares/{share_id}")
def revoke_company_share_link(
    company_id: str,
    share_id: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_permission("assets.manage"))],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    if context.company_id != company_id:
        raise HTTPException(status_code=404, detail="Company does not exist")
    try:
        link = ShareLinkService.revoke(
            session,
            share_id=share_id,
            company_id=company_id,
            personal_workspace_id=None,
            user_id=context.user_id,
            request_id=getattr(request.state, "request_id", "system"),
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return ShareLinkService.management_payload(link)


@router.post("/api/v1/personal/shares", status_code=201)
def create_personal_share_link(
    body: CreateShareLinkRequest,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        link, raw_token, _created = ShareLinkService.create(
            session,
            company_id=None,
            personal_workspace_id=workspace.id,
            user_id=context.user_id,
            body=body,
            pepper=request.app.state.settings.jwt_signing_secret,
            request_id=getattr(request.state, "request_id", "system"),
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    payload = ShareLinkService.management_payload(link)
    payload["url"] = f"/s/{raw_token}" if raw_token else None
    payload["token_returned"] = raw_token is not None
    return payload


@router.get("/api/v1/personal/shares")
def list_personal_share_links(
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
    resource_id: str | None = None,
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    links = ShareLinkService.list(
        session,
        company_id=None,
        personal_workspace_id=workspace.id,
        resource_id=resource_id,
    )
    return {
        "items": [ShareLinkService.management_payload(link) for link in links]
    }


@router.delete("/api/v1/personal/shares/{share_id}")
def revoke_personal_share_link(
    share_id: str,
    request: Request,
    context: Annotated[UserContext, Depends(get_user_context)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    workspace = _personal_workspace(session, context)
    try:
        link = ShareLinkService.revoke(
            session,
            share_id=share_id,
            company_id=None,
            personal_workspace_id=workspace.id,
            user_id=context.user_id,
            request_id=getattr(request.state, "request_id", "system"),
        )
    except (NotFoundError, ConflictError) as exc:
        raise _domain_error(exc) from exc
    return ShareLinkService.management_payload(link)


@router.get("/api/v1/shares/{token}")
def read_shared_resource(
    token: str,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> dict[str, Any]:
    """Anonymous read of a shared result.

    No session, no cookie, no tenant. Everything it returns is redacted, and
    everything it counts is metered per source IP.
    """

    pepper = request.app.state.settings.jwt_signing_secret
    ip_hash = _ip_hash(request, pepper=pepper)
    if ShareLinkService.anonymous_failures_exceeded(session, ip_hash=ip_hash):
        raise HTTPException(status_code=429, detail="Too many failed share reads")
    request_id = getattr(request.state, "request_id", "system")
    try:
        link = ShareLinkService.resolve(session, raw_token=token, pepper=pepper)
    except NotFoundError:
        ShareLinkService.register_anonymous_failure(
            session,
            ip_hash=ip_hash,
            request_id=request_id,
            user_agent=request.headers.get("user-agent", ""),
        )
        raise HTTPException(status_code=404, detail="Share link does not exist")
    ShareLinkService.touch(session, link=link)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return ShareLinkService.public_payload(session, link=link)
