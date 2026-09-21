from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..dependencies import PlatformAdminContext, get_db, require_platform_admin
from ..services.audit import AuditService

router = APIRouter(
    prefix="/api/v1/platform-admin/relay",
    tags=["platform-admin-relay-native-console"],
)

_CONSOLE_DESTINATIONS = {
    "native_break_glass": ("/channels", "relay.native_console.launch_authorized", "relay_native_console", "RELAY_NATIVE_CONSOLE"),
    "provider_onboarding": ("/provider-onboarding", "relay.provider_onboarding.launch_authorized", "relay_provider_onboarding", "RELAY_PROVIDER_ONBOARDING"),
}
_RESPONSE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


class OpenRelayNativeConsoleRequest(BaseModel):
    """The destination is server-owned; callers cannot influence the launch."""

    model_config = ConfigDict(extra="forbid")


class RelayNativeConsoleLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    mode: Literal["native_break_glass"]


class RelayProviderOnboardingLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    mode: Literal["provider_onboarding"]


def _protect_response(response: Response) -> None:
    for name, value in _RESPONSE_HEADERS.items():
        response.headers[name] = value


def _authorize_console_launch(
    *,
    mode: Literal["native_break_glass", "provider_onboarding"],
    request: Request,
    response: Response,
    admin: PlatformAdminContext,
    session: Session,
) -> dict[str, str]:
    _protect_response(response)
    settings = request.app.state.settings
    # Only these two server-selected destinations exist. Neither endpoint
    # accepts a path, origin, redirect URL, credential or impersonation token.
    destination_path, audit_action, target_type, error_prefix = _CONSOLE_DESTINATIONS[mode]
    console_name = "Relay provider onboarding" if mode == "provider_onboarding" else "Relay native console"

    # Both destinations reach privileged Relay management. Returning a URL
    # does not establish a Relay session or grant any provider/model authority.
    # Delegated administrators remain fail-closed in every environment even
    # when they have Relay health-management permission.
    if not admin.is_platform_owner:
        raise HTTPException(
            status_code=403,
            detail={
                "code": f"{error_prefix}_OWNER_REQUIRED",
                "message": (
                    f"{console_name} access is restricted to the platform owner"
                ),
            },
            headers=_RESPONSE_HEADERS,
        )

    origin = settings.relay_native_admin_console_origin
    if not origin:
        raise HTTPException(
            status_code=503,
            detail={
                "code": f"{error_prefix}_NOT_CONFIGURED",
                "message": (
                    "Relay provider onboarding console is not configured"
                    if mode == "provider_onboarding"
                    else "Relay native administrator console is not configured"
                ),
            },
            headers=_RESPONSE_HEADERS,
        )

    AuditService.append(
        session,
        actor_user_id=admin.user_id,
        action=audit_action,
        target_type=target_type,
        target_id="new-api",
        before_summary={},
        after_summary={
            "mode": mode,
            "destination_origin": origin,
            "destination_path": destination_path,
        },
        request_id=str(request.state.request_id),
    )
    # The authorization audit must be durable before the browser is given the
    # native destination.  The surrounding dependency's second commit is safe.
    session.commit()

    return {"url": f"{origin}{destination_path}", "mode": mode}


@router.post("/native-console/open", response_model=RelayNativeConsoleLaunch)
def open_relay_native_console(
    _body: OpenRelayNativeConsoleRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> RelayNativeConsoleLaunch:
    return RelayNativeConsoleLaunch(**_authorize_console_launch(
        mode="native_break_glass", request=request, response=response,
        admin=admin, session=session,
    ))


@router.post("/provider-onboarding/open", response_model=RelayProviderOnboardingLaunch)
def open_relay_provider_onboarding(
    _body: OpenRelayNativeConsoleRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> RelayProviderOnboardingLaunch:
    return RelayProviderOnboardingLaunch(**_authorize_console_launch(
        mode="provider_onboarding", request=request, response=response,
        admin=admin, session=session,
    ))
