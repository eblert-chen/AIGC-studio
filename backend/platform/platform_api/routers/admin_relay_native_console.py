from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..dependencies import PlatformAdminContext, get_db, require_platform_admin
from ..services.audit import AuditService

router = APIRouter(
    prefix="/api/v1/platform-admin/relay/native-console",
    tags=["platform-admin-relay-native-console"],
)

_CONSOLE_PATH = "/channels"
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


def _protect_response(response: Response) -> None:
    for name, value in _RESPONSE_HEADERS.items():
        response.headers[name] = value


@router.post("/open", response_model=RelayNativeConsoleLaunch)
def open_relay_native_console(
    _body: OpenRelayNativeConsoleRequest,
    request: Request,
    response: Response,
    admin: Annotated[PlatformAdminContext, Depends(require_platform_admin)],
    session: Annotated[Session, Depends(get_db, scope="function")],
) -> RelayNativeConsoleLaunch:
    _protect_response(response)
    settings = request.app.state.settings

    # This console can expose provider credentials and arbitrary native channel
    # mutation. Delegated administrators remain fail-closed in every
    # environment even when they have Relay health-management permission.
    if not admin.is_platform_owner:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "RELAY_NATIVE_CONSOLE_OWNER_REQUIRED",
                "message": (
                    "Relay native console access is restricted to the platform owner"
                ),
            },
            headers=_RESPONSE_HEADERS,
        )

    origin = settings.relay_native_admin_console_origin
    if not origin:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RELAY_NATIVE_CONSOLE_NOT_CONFIGURED",
                "message": "Relay native administrator console is not configured",
            },
            headers=_RESPONSE_HEADERS,
        )

    AuditService.append(
        session,
        actor_user_id=admin.user_id,
        action="relay.native_console.launch_authorized",
        target_type="relay_native_console",
        target_id="new-api",
        before_summary={},
        after_summary={
            "mode": "native_break_glass",
            "destination_origin": origin,
            "destination_path": _CONSOLE_PATH,
        },
        request_id=str(request.state.request_id),
    )
    # The authorization audit must be durable before the browser is given the
    # native destination.  The surrounding dependency's second commit is safe.
    session.commit()

    return RelayNativeConsoleLaunch(
        url=f"{origin}{_CONSOLE_PATH}",
        mode="native_break_glass",
    )
