from __future__ import annotations

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    AuditLog,
    LedgerEntry,
    LedgerKind,
    PersonalLedgerEntry,
    PersonalWalletAccount,
    PersonalWorkspace,
    User,
    UserStatus,
)
from platform_api.services.personal import PersonalWorkspaceService

from .test_platform_admin import bootstrap_admin


def _personal_user(
    app,
    suffix: str,
    *,
    workspace: bool = True,
    workspace_active: bool = True,
    status: UserStatus = UserStatus.ACTIVE,
) -> tuple[str, str | None]:
    with app.state.session_factory.begin() as session:
        user = User(
            email=f"points-{suffix}@example.com",
            display_name=f"Points {suffix}",
            status=status,
        )
        session.add(user)
        session.flush()
        workspace_id = None
        if workspace:
            personal_workspace = PersonalWorkspaceService.ensure(
                session, user_id=user.id
            )
            personal_workspace.active = workspace_active
            workspace_id = personal_workspace.id
        return user.id, workspace_id


def _grant(
    client,
    headers: dict[str, str],
    user_id: str,
    *,
    amount_points: int = 125,
    idempotency_key: str = "owner-gift-points-0001",
    note: str = "封闭内测赠送",
):
    return client.post(
        f"/api/v1/platform-admin/users/{user_id}/points-grants",
        headers=headers,
        json={
            "amount_points": amount_points,
            "idempotency_key": idempotency_key,
            "note": note,
        },
    )


def test_owner_grants_personal_points_idempotently_and_audits_once(app, client):
    admin_id, admin_headers = bootstrap_admin(client, "personal-points-owner")
    user_id, workspace_id = _personal_user(app, "gift-success")
    request_headers = {**admin_headers, "X-Request-ID": "gift-original-request"}

    granted = _grant(client, request_headers, user_id)
    assert granted.status_code == 200, granted.text
    body = granted.json()
    assert body["user_id"] == user_id
    assert body["workspace_id"] == workspace_id
    assert body["wallet"] == {
        "workspace_id": workspace_id,
        "available_points": 125,
        "reserved_points": 0,
    }
    assert body["ledger_entry"]["kind"] == "recharge"
    assert body["ledger_entry"]["amount_points"] == 125
    assert body["ledger_entry"]["note"] == "封闭内测赠送"
    assert "idempotency_key" not in body["ledger_entry"]
    assert body["created"] is True

    replay = _grant(
        client,
        {**admin_headers, "X-Request-ID": "gift-replay-request"},
        user_id,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["created"] is False
    assert replay.json()["ledger_entry"]["id"] == body["ledger_entry"]["id"]
    assert replay.json()["wallet"]["available_points"] == 125

    user_page = client.get(
        "/api/v1/platform-admin/users?page=1&page_size=100",
        headers=admin_headers,
    )
    assert user_page.status_code == 200, user_page.text
    listed = next(item for item in user_page.json()["items"] if item["id"] == user_id)
    assert listed["personal_workspace_id"] == workspace_id
    assert listed["personal_workspace_active"] is True
    assert listed["available_points"] == 125
    assert listed["reserved_points"] == 0

    history = client.get(
        f"/api/v1/platform-admin/users/{user_id}/points-grants",
        headers=admin_headers,
        params={"page": 1, "page_size": 20},
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["total_amount_points"] == 125
    assert history.json()["items"][0]["id"] == body["ledger_entry"]["id"]

    with app.state.session_factory() as session:
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert wallet.available_points == 125
        assert wallet.reserved_points == 0
        personal_ledger_count = int(
            session.scalar(
                select(func.count(PersonalLedgerEntry.id)).where(
                    PersonalLedgerEntry.workspace_id == workspace_id,
                    PersonalLedgerEntry.kind == LedgerKind.RECHARGE,
                )
            )
            or 0
        )
        company_ledger_count = int(
            session.scalar(select(func.count(LedgerEntry.id))) or 0
        )
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "personal.wallet.points_grant",
                    AuditLog.target_id == user_id,
                )
            ).all()
        )
        assert personal_ledger_count == 1
        assert company_ledger_count == 0
        assert len(audits) == 1
        assert audits[0].actor_user_id == admin_id
        assert audits[0].request_id == "gift-original-request"
        assert audits[0].before_summary["available_points"] == 0
        assert audits[0].after_summary["available_points"] == 125


def test_personal_points_grant_rejects_idempotency_payload_changes(app, client):
    _, admin_headers = bootstrap_admin(client, "personal-points-conflict")
    user_id, _ = _personal_user(app, "gift-conflict")
    assert _grant(client, admin_headers, user_id).status_code == 200

    changed_amount = _grant(
        client,
        admin_headers,
        user_id,
        amount_points=126,
    )
    assert changed_amount.status_code == 409
    changed_note = _grant(
        client,
        admin_headers,
        user_id,
        note="另一笔赠送",
    )
    assert changed_note.status_code == 409

    with app.state.session_factory() as session:
        workspace = session.scalar(
            select(PersonalWorkspace).where(PersonalWorkspace.user_id == user_id)
        )
        wallet = session.get(PersonalWalletAccount, workspace.id)
        assert wallet.available_points == 125
        assert (
            session.scalar(
                select(func.count(PersonalLedgerEntry.id)).where(
                    PersonalLedgerEntry.workspace_id == workspace.id
                )
            )
            == 1
        )


def test_personal_points_grant_strips_transaction_text_and_rejects_blank_note(
    app, client
):
    _, admin_headers = bootstrap_admin(client, "personal-points-text")
    user_id, workspace_id = _personal_user(app, "gift-text")

    blank = _grant(
        client,
        admin_headers,
        user_id,
        idempotency_key="owner-gift-blank-0001",
        note="   ",
    )
    assert blank.status_code == 422

    trimmed = _grant(
        client,
        admin_headers,
        user_id,
        idempotency_key="  owner-gift-trimmed-0001  ",
        note="  有意义的运营赠送  ",
    )
    assert trimmed.status_code == 200, trimmed.text
    assert "idempotency_key" not in trimmed.json()["ledger_entry"]
    assert trimmed.json()["ledger_entry"]["note"] == "有意义的运营赠送"
    replay = _grant(
        client,
        admin_headers,
        user_id,
        idempotency_key="owner-gift-trimmed-0001",
        note="有意义的运营赠送",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["created"] is False

    with app.state.session_factory() as session:
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert wallet.available_points == 125
        assert (
            session.scalar(
                select(func.count(PersonalLedgerEntry.id)).where(
                    PersonalLedgerEntry.workspace_id == workspace_id
                )
            )
            == 1
        )
        entry = session.scalar(
            select(PersonalLedgerEntry).where(
                PersonalLedgerEntry.workspace_id == workspace_id
            )
        )
        assert entry.idempotency_key == (
            "platform-owner-grant:owner-gift-trimmed-0001"
        )


@pytest.mark.parametrize("invalid_amount", [True, "125", 1.0])
def test_personal_points_grant_requires_strict_integer_amount(
    app, client, invalid_amount
):
    _, admin_headers = bootstrap_admin(client, f"strict-{type(invalid_amount).__name__}")
    user_id, workspace_id = _personal_user(
        app, f"strict-{type(invalid_amount).__name__}"
    )
    response = client.post(
        f"/api/v1/platform-admin/users/{user_id}/points-grants",
        headers=admin_headers,
        json={
            "amount_points": invalid_amount,
            "idempotency_key": "owner-strict-amount-0001",
            "note": "严格整数验证",
        },
    )
    assert response.status_code == 422
    with app.state.session_factory() as session:
        wallet = session.get(PersonalWalletAccount, workspace_id)
        assert wallet.available_points == 0
        assert session.scalar(
            select(func.count(PersonalLedgerEntry.id)).where(
                PersonalLedgerEntry.workspace_id == workspace_id
            )
        ) == 0


def test_admin_grant_has_separate_idempotency_namespace_and_history_evidence(
    app, client, internal_headers
):
    _, admin_headers = bootstrap_admin(client, "personal-points-namespace")
    user_id, workspace_id = _personal_user(app, "gift-namespace")
    shared_key = "shared-personal-credit-0001"

    internal = client.post(
        f"/internal/personal/wallets/{workspace_id}/credit",
        headers=internal_headers,
        json={
            "amount_points": 40,
            "idempotency_key": shared_key,
            "note": "已确认的普通积分入账",
        },
    )
    assert internal.status_code == 200, internal.text
    granted = _grant(
        client,
        admin_headers,
        user_id,
        amount_points=125,
        idempotency_key=shared_key,
        note="管理员赠送",
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["wallet"]["available_points"] == 165

    history = client.get(
        f"/api/v1/platform-admin/users/{user_id}/points-grants",
        headers=admin_headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["total_amount_points"] == 125
    assert history.json()["items"][0]["note"] == "管理员赠送"
    assert "idempotency_key" not in history.json()["items"][0]

    with app.state.session_factory() as session:
        entries = list(
            session.scalars(
                select(PersonalLedgerEntry)
                .where(PersonalLedgerEntry.workspace_id == workspace_id)
                .order_by(PersonalLedgerEntry.idempotency_key)
            ).all()
        )
        assert {entry.idempotency_key for entry in entries} == {
            shared_key,
            f"platform-owner-grant:{shared_key}",
        }


def test_personal_points_grant_never_provisions_missing_or_disabled_workspace(
    app, client
):
    _, admin_headers = bootstrap_admin(client, "personal-points-workspace")
    no_workspace_user_id, _ = _personal_user(
        app, "gift-no-workspace", workspace=False
    )
    disabled_user_id, disabled_workspace_id = _personal_user(
        app, "gift-disabled-workspace", workspace_active=False
    )
    suspended_user_id, _ = _personal_user(
        app,
        "gift-suspended-user",
        status=UserStatus.SUSPENDED,
    )

    missing = _grant(client, admin_headers, no_workspace_user_id)
    assert missing.status_code == 404
    disabled = _grant(
        client,
        admin_headers,
        disabled_user_id,
        idempotency_key="owner-gift-disabled-0001",
    )
    assert disabled.status_code == 409
    suspended = _grant(
        client,
        admin_headers,
        suspended_user_id,
        idempotency_key="owner-gift-suspended-0001",
    )
    assert suspended.status_code == 409

    with app.state.session_factory() as session:
        assert session.scalar(
            select(PersonalWorkspace).where(
                PersonalWorkspace.user_id == no_workspace_user_id
            )
        ) is None
        disabled_wallet = session.get(
            PersonalWalletAccount, disabled_workspace_id
        )
        assert disabled_wallet.available_points == 0


def test_non_owner_platform_admin_cannot_read_or_grant_personal_points(app, client):
    bootstrap_admin(client, "personal-points-product-owner")
    _, delegated_headers = bootstrap_admin(client, "personal-points-delegated")
    user_id, _ = _personal_user(app, "gift-owner-boundary")

    denied_write = _grant(client, delegated_headers, user_id)
    assert denied_write.status_code == 403
    denied_read = client.get(
        f"/api/v1/platform-admin/users/{user_id}/points-grants",
        headers=delegated_headers,
    )
    assert denied_read.status_code == 403


def test_personal_points_history_is_scoped_and_paginated(app, client):
    _, admin_headers = bootstrap_admin(client, "personal-points-history")
    first_user_id, _ = _personal_user(app, "gift-history-first")
    second_user_id, _ = _personal_user(app, "gift-history-second")
    for index, amount in enumerate((10, 20, 30)):
        result = _grant(
            client,
            admin_headers,
            first_user_id,
            amount_points=amount,
            idempotency_key=f"owner-history-first-{index:04d}",
            note=f"第一位用户第 {index + 1} 笔",
        )
        assert result.status_code == 200, result.text
    assert _grant(
        client,
        admin_headers,
        second_user_id,
        amount_points=999,
        idempotency_key="owner-history-second-0001",
        note="第二位用户积分",
    ).status_code == 200

    page = client.get(
        f"/api/v1/platform-admin/users/{first_user_id}/points-grants",
        headers=admin_headers,
        params={"page": 2, "page_size": 2},
    )
    assert page.status_code == 200, page.text
    assert page.json()["page"] == 2
    assert page.json()["page_size"] == 2
    assert page.json()["total"] == 3
    assert page.json()["total_amount_points"] == 60
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["amount_points"] in {10, 20, 30}


def test_personal_points_routes_have_explicit_finance_policies():
    from platform_api.platform_admin_access_policy import (
        resolve_platform_admin_route_permission,
    )

    route = "/api/v1/platform-admin/users/{user_id}/points-grants"
    assert resolve_platform_admin_route_permission(
        method="GET", route_path=route
    ) == "platform.finance.read"
    assert resolve_platform_admin_route_permission(
        method="POST", route_path=route
    ) == "platform.finance.manage"
