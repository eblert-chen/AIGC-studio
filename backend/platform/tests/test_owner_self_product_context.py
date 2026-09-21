from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from platform_api.models import (
    AccountSecurityEvent,
    AuthProductContextSwitch,
    AuthSession,
    ExternalIdentity,
    GenerationTask,
    PersonalWalletAccount,
    PersonalWorkspace,
    ProductContext,
    User,
    UserAccountType,
    UserStatus,
    utcnow,
)
from platform_api.services.authentication import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SessionService,
)
from platform_api.services.personal_billing import PersonalWalletService
from tests.test_personal_workspace import _retail_model
from tests.test_production_auth_lifecycle import _auth_app, _login


ORIGIN = "https://frontend.example.test"


def _complete_login(browser: TestClient, provider: dict[str, str | int]) -> dict:
    _, callback = _login(browser, provider)
    completed = browser.get(callback, follow_redirects=False)
    assert completed.status_code == 303, completed.text
    state = browser.get("/api/v1/auth/session")
    assert state.status_code == 200, state.text
    return state.json()


def _switch(
    browser: TestClient,
    *,
    target: str,
    key: str,
    csrf: str,
):
    return browser.post(
        "/api/v1/auth/product-context",
        headers={
            "Origin": ORIGIN,
            CSRF_HEADER_NAME: csrf,
            "Idempotency-Key": key,
        },
        json={"target_context": target},
    )


def test_owner_switches_without_idp_logout_and_contexts_fail_closed() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            platform_state = _complete_login(browser, provider)
            owner_id = platform_state["user"]["id"]
            assert platform_state["account_type"] == "platform_admin"
            assert platform_state["active_product_context"] == "platform"
            assert platform_state["available_product_contexts"] == [
                "platform",
                "personal",
            ]
            assert (
                browser.get("/api/v1/platform-admin/task-content").status_code
                == 200
            )
            old_token = browser.cookies[SESSION_COOKIE_NAME]

            rejected = browser.post(
                "/api/v1/auth/product-context",
                headers={"Origin": ORIGIN, "Idempotency-Key": "owner-switch-no-csrf"},
                json={"target_context": "personal"},
            )
            assert rejected.status_code == 403
            with app.state.session_factory() as database:
                assert database.scalar(select(func.count(AuthSession.id))) == 1
                assert database.scalar(select(PersonalWorkspace.id)) is None

            switched = _switch(
                browser,
                target="personal",
                key="owner-switch-personal-0001",
                csrf=platform_state["csrf_token"],
            )
            assert switched.status_code == 200, switched.text
            personal_state = switched.json()["session"]
            assert switched.headers[CSRF_HEADER_NAME] == personal_state["csrf_token"]
            assert personal_state["account_type"] == "personal"
            assert personal_state["active_product_context"] == "personal"
            assert personal_state["available_product_contexts"] == [
                "platform",
                "personal",
            ]
            assert personal_state["personal"]["workspace_id"]

            wallet = browser.get("/api/v1/personal/wallet")
            assert wallet.status_code == 200, wallet.text
            assert wallet.json()["available_points"] == 0
            assert wallet.json()["reserved_points"] == 0
            assert browser.get("/api/v1/platform-admin/users").status_code == 403
            assert (
                browser.get("/api/v1/platform-admin/task-content").status_code
                == 403
            )

            with app.state.session_factory() as database:
                owner = database.get(User, owner_id)
                workspace = database.scalar(
                    select(PersonalWorkspace).where(PersonalWorkspace.user_id == owner_id)
                )
                assert owner.account_type == UserAccountType.PLATFORM_ADMIN
                assert workspace is not None and workspace.active
                assert workspace.owner_self_identity_id is not None
                persisted_contexts = list(
                    database.scalars(
                        select(AuthSession.active_product_context).order_by(AuthSession.created_at)
                    )
                )
                assert persisted_contexts == [ProductContext.PLATFORM, ProductContext.PERSONAL]
                assert list(
                    database.scalars(
                        text(
                            "SELECT active_product_context FROM auth_sessions "
                            "ORDER BY created_at"
                        )
                    )
                ) == ["platform", "personal"]
                assert SessionService.resolve(
                    database,
                    raw_token=old_token,
                    pepper=app.state.settings.jwt_signing_secret,
                    idle_ttl_seconds=3600,
                ) is None

            replay = _switch(
                browser,
                target="personal",
                key="owner-switch-personal-0001",
                csrf=personal_state["csrf_token"],
            )
            assert replay.status_code == 200, replay.text
            assert replay.headers[CSRF_HEADER_NAME] == personal_state["csrf_token"]
            with app.state.session_factory() as database:
                assert database.scalar(select(func.count(AuthSession.id))) == 2
                assert database.scalar(
                    select(func.count(AuthProductContextSwitch.id))
                ) == 1
                assert database.scalar(
                    select(func.count(AccountSecurityEvent.id)).where(
                        AccountSecurityEvent.event_type
                        == "auth.product_context.switched"
                    )
                ) == 1

            conflict = _switch(
                browser,
                target="platform",
                key="owner-switch-personal-0001",
                csrf=personal_state["csrf_token"],
            )
            assert conflict.status_code == 409
            assert browser.get("/api/v1/personal/wallet").status_code == 200

            returned = _switch(
                browser,
                target="platform",
                key="owner-switch-platform-0001",
                csrf=personal_state["csrf_token"],
            )
            assert returned.status_code == 200, returned.text
            returned_state = returned.json()["session"]
            assert returned_state["active_product_context"] == "platform"
            assert returned_state["account_type"] == "platform_admin"
            assert browser.get("/api/v1/platform-admin/users").status_code == 200
            assert (
                browser.get("/api/v1/platform-admin/task-content").status_code
                == 200
            )
            assert browser.get("/api/v1/personal/wallet").status_code == 403
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_owner_self_points_grant_then_personal_reserve_and_settle() -> None:
    app, engine, provider = _auth_app()
    try:
        with TestClient(app, base_url="https://testserver") as browser:
            state = _complete_login(browser, provider)
            owner_id = state["user"]["id"]
            personal = _switch(
                browser,
                target="personal",
                key="owner-billing-personal-0001",
                csrf=state["csrf_token"],
            ).json()["session"]
            workspace_id = personal["personal"]["workspace_id"]
            assert browser.get("/api/v1/personal/wallet").json()["available_points"] == 0

            platform_response = _switch(
                browser,
                target="platform",
                key="owner-billing-platform-0001",
                csrf=personal["csrf_token"],
            )
            platform = platform_response.json()["session"]
            grant = browser.post(
                f"/api/v1/platform-admin/users/{owner_id}/points-grants",
                headers={"Origin": ORIGIN, CSRF_HEADER_NAME: platform["csrf_token"]},
                json={
                    "amount_points": 100,
                    "idempotency_key": "owner-self-grant-0001",
                    "note": "平台所有者本人创作积分",
                },
            )
            assert grant.status_code == 200, grant.text
            assert grant.json()["wallet"]["available_points"] == 100

            personal_response = _switch(
                browser,
                target="personal",
                key="owner-billing-personal-0002",
                csrf=platform["csrf_token"],
            )
            personal = personal_response.json()["session"]
            model_id = _retail_model(app)
            models = browser.get("/api/v1/personal/models").json()
            created = browser.post(
                "/api/v1/personal/tasks",
                headers={"Origin": ORIGIN, CSRF_HEADER_NAME: personal["csrf_token"]},
                json={
                    "model_id": model_id,
                    "expected_capability_version": models[0]["capability_version"],
                    "expected_quote_revision": models[0]["quote_revision"],
                    "idempotency_key": "owner-self-task-0001",
                    "request_payload": {
                        "mode": "text_to_video",
                        "prompt": "sunrise over a quiet lake",
                        "assets": [],
                        "duration_seconds": 5,
                        "aspect_ratio": "16:9",
                        "resolution": "720p",
                        "output_count": 1,
                        "face_enabled": False,
                    },
                },
            )
            assert created.status_code == 201, created.text
            task = created.json()
            assert task["quote_points"] == 15
            reserved_wallet = browser.get("/api/v1/personal/wallet").json()
            assert reserved_wallet["workspace_id"] == workspace_id
            assert reserved_wallet["available_points"] == 85
            assert reserved_wallet["reserved_points"] == 15
            with app.state.session_factory.begin() as database:
                PersonalWalletService.settle_success(
                    database,
                    workspace_id=workspace_id,
                    task_id=task["id"],
                    actual_cost_points=12,
                    idempotency_key="owner-self-settle-0001",
                )
            settled_wallet = browser.get("/api/v1/personal/wallet").json()
            assert settled_wallet["available_points"] == 88
            assert settled_wallet["reserved_points"] == 0
            with app.state.session_factory() as database:
                stored = database.get(GenerationTask, task["id"])
                assert stored.actual_cost_points == 12
                assert database.get(PersonalWalletAccount, workspace_id).available_points == 88
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_ordinary_platform_admin_cannot_link_or_switch_owner_self_context() -> None:
    app, engine, provider = _auth_app(
        provider_subject="ordinary-platform-admin",
        provider_email="ordinary-platform-admin@example.com",
        provider_name="Ordinary Admin",
    )
    try:
        with app.state.session_factory.begin() as database:
            user = User(
                email="ordinary-platform-admin@example.com",
                display_name="Ordinary Admin",
                status=UserStatus.ACTIVE,
                email_verified_at=utcnow(),
                is_platform_admin=True,
                account_type=UserAccountType.PLATFORM_ADMIN,
            )
            database.add(user)
            database.flush()
            database.add(
                ExternalIdentity(
                    user_id=user.id,
                    issuer="https://identity.example.test",
                    subject="ordinary-platform-admin",
                    email_at_link=user.email,
                )
            )
        with TestClient(app, base_url="https://testserver") as browser:
            state = _complete_login(browser, provider)
            assert (
                browser.get("/api/v1/platform-admin/task-content").status_code
                == 403
            )
            denied = _switch(
                browser,
                target="personal",
                key="ordinary-admin-switch-0001",
                csrf=state["csrf_token"],
            )
            assert denied.status_code == 403
            assert denied.json()["code"] == "product_context_switch_forbidden"
            with app.state.session_factory() as database:
                assert database.scalar(select(PersonalWorkspace.id)) is None
                assert database.scalar(
                    select(func.count(AuthProductContextSwitch.id))
                ) == 0
                assert database.scalar(select(func.count(AuthSession.id))) == 1
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()
