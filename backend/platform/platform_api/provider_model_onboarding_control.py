"""Development-only control helpers for credential-late model onboarding.

The browser-facing Platform administrator header carries a local ``users.id``.
The configured owner allowlist, however, carries OIDC subjects.  This command
resolves that boundary inside the Platform image and database so the local
onboarding runner never asks an operator to copy an administrator identifier or
token into another secret file.

The command is deliberately read-only and refuses every protected runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings, runtime_settings_are_protected
from .database import build_engine, build_session_factory
from .models import ExternalIdentity, User, UserAccountType, UserStatus
from .services.account_partition import AccountPartitionService, AccountProductType
from .services.admin import PlatformAdminService


class LocalOnboardingControlError(RuntimeError):
    """A fail-closed local control boundary error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _eligible_platform_owner(session: Session, user: User | None) -> bool:
    return bool(
        user is not None
        and user.status == UserStatus.ACTIVE
        and user.is_platform_admin
        and user.account_type == UserAccountType.PLATFORM_ADMIN
        and AccountPartitionService.resolve(session, user=user)
        == AccountProductType.PLATFORM_ADMIN
    )


def resolve_local_platform_owner(session: Session, settings: Any) -> dict[str, str] | None:
    """Resolve the exact local owner accepted by development header auth.

    OIDC-backed configured owners take precedence.  When no configured subject
    has a local identity yet, the fallback exactly mirrors the development-only
    first-administrator owner rule in ``require_platform_admin``.
    """

    if runtime_settings_are_protected(settings):
        raise LocalOnboardingControlError(
            "PROTECTED_RUNTIME_FORBIDDEN",
            "provider onboarding owner resolution is development-only",
        )
    if not settings.development_header_auth_enabled or not settings.enable_bootstrap:
        raise LocalOnboardingControlError(
            "LOCAL_CONTROL_DISABLED",
            "development header authentication and bootstrap must both be enabled",
        )

    subjects = sorted(set(settings.platform_owner_user_ids))
    oidc_issuer = (settings.oidc_issuer or "").strip()
    if subjects and oidc_issuer:
        configured = session.scalars(
            select(User)
            .join(ExternalIdentity, ExternalIdentity.user_id == User.id)
            .where(
                ExternalIdentity.issuer == oidc_issuer,
                ExternalIdentity.subject.in_(subjects),
            )
            .order_by(User.created_at.asc(), User.id.asc())
        ).all()
        for user in configured:
            if _eligible_platform_owner(session, user):
                return {"user_id": user.id, "source": "configured_oidc_owner"}

    first_admin = PlatformAdminService.first_unambiguous_platform_admin(session)
    if _eligible_platform_owner(session, first_admin):
        return {"user_id": first_admin.id, "source": "development_first_admin"}
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m platform_api.provider_model_onboarding_control"
    )
    parser.add_argument("command", choices=("resolve-owner",))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    assert args.command == "resolve-owner"
    try:
        settings = get_settings("platform-api")
        if runtime_settings_are_protected(settings):
            raise LocalOnboardingControlError(
                "PROTECTED_RUNTIME_FORBIDDEN",
                "provider onboarding owner resolution is development-only",
            )
        engine = build_engine(settings.database_url)
        factory = build_session_factory(engine)
        with factory() as session:
            resolved = resolve_local_platform_owner(session, settings)
        engine.dispose()
        if resolved is None:
            raise LocalOnboardingControlError(
                "LOCAL_OWNER_NOT_FOUND",
                "no eligible local platform owner exists",
            )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "provider_model_onboarding_local_owner",
                    **resolved,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    except LocalOnboardingControlError as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "code": exc.code, "message": str(exc)},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
