from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from platform_api import provider_model_onboarding_control
from platform_api.models import ExternalIdentity, User, UserStatus
from platform_api.provider_model_onboarding_control import (
    LocalOnboardingControlError,
    resolve_local_platform_owner,
)
from platform_api.services.admin import PlatformAdminService
from platform_api.services import admin as admin_service

from .test_platform_admin import bootstrap_admin


def _settings(**overrides):
    values = {
        "environment": "development",
        "protected_runtime": False,
        "development_header_auth_enabled": True,
        "enable_bootstrap": True,
        "platform_owner_user_ids": [],
        "oidc_issuer": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_resolves_configured_oidc_subject_to_local_platform_owner(app):
    with app.state.session_factory() as session:
        admin = PlatformAdminService.bootstrap_admin(
            session,
            email="provider-onboarding-owner@example.com",
            display_name="Provider onboarding owner",
        )
        session.add(
            ExternalIdentity(
                user_id=admin.id,
                issuer="https://issuer.example/",
                subject="provider-owner-subject",
                email_at_link=admin.email,
            )
        )
        session.commit()

        resolved = resolve_local_platform_owner(
            session,
            _settings(
                oidc_issuer="https://issuer.example/",
                platform_owner_user_ids=["provider-owner-subject"],
            ),
        )

    assert resolved == {
        "user_id": admin.id,
        "source": "configured_oidc_owner",
    }


def test_falls_back_to_first_platform_admin_only_in_local_development(
    app, monkeypatch
):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2030, 1, 1, tzinfo=timezone.utc)
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(admin_service, "datetime", FrozenDateTime)
    with app.state.session_factory() as session:
        first = PlatformAdminService.bootstrap_admin(
            session,
            email="provider-onboarding-first@example.com",
            display_name="First provider onboarding owner",
        )
        second = PlatformAdminService.bootstrap_admin(
            session,
            email="provider-onboarding-second@example.com",
            display_name="Second provider onboarding owner",
        )
        session.commit()

        resolved = resolve_local_platform_owner(session, _settings())
        assert first.created_at < second.created_at

    assert resolved == {
        "user_id": first.id,
        "source": "development_first_admin",
    }


def test_legacy_colliding_first_admin_timestamps_fail_closed(app):
    with app.state.session_factory() as session:
        first = PlatformAdminService.bootstrap_admin(
            session,
            email="provider-onboarding-collision-a@example.com",
            display_name="Collision A",
        )
        second = PlatformAdminService.bootstrap_admin(
            session,
            email="provider-onboarding-collision-b@example.com",
            display_name="Collision B",
        )
        collision = datetime(2025, 1, 1, tzinfo=timezone.utc)
        first.created_at = collision
        second.created_at = collision
        session.commit()

        resolved = provider_model_onboarding_control.resolve_local_platform_owner(
            session, _settings()
        )

    assert resolved is None


def test_http_owner_fallback_rejects_legacy_timestamp_collision(app, client):
    first_id, first_headers = bootstrap_admin(client, "owner-collision-a")
    second_id, second_headers = bootstrap_admin(client, "owner-collision-b")
    collision = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with app.state.session_factory() as session:
        session.get(User, first_id).created_at = collision
        session.get(User, second_id).created_at = collision
        session.commit()
    app.state.development_platform_owner_user_ids.clear()

    first_response = client.get(
        "/api/v1/platform-admin/me", headers=first_headers
    )
    second_response = client.get(
        "/api/v1/platform-admin/me", headers=second_headers
    )

    assert first_response.status_code == 403
    assert second_response.status_code == 403


def test_inactive_original_admin_does_not_promote_later_admin(app, client):
    first_id, _ = bootstrap_admin(client, "owner-inactive-original")
    _, second_headers = bootstrap_admin(client, "owner-inactive-later")
    with app.state.session_factory() as session:
        first = session.get(User, first_id)
        first.status = UserStatus.DEACTIVATED
        first.deactivated_at = datetime.now(timezone.utc)
        session.commit()
        assert resolve_local_platform_owner(session, _settings()) is None
    app.state.development_platform_owner_user_ids.clear()

    response = client.get(
        "/api/v1/platform-admin/me", headers=second_headers
    )

    assert response.status_code == 403


def test_refuses_owner_resolution_in_protected_runtime(app):
    with app.state.session_factory() as session:
        with pytest.raises(LocalOnboardingControlError) as captured:
            resolve_local_platform_owner(
                session,
                _settings(environment="staging", protected_runtime=True),
            )

    assert captured.value.code == "PROTECTED_RUNTIME_FORBIDDEN"
