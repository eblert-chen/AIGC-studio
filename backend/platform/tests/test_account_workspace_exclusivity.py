from __future__ import annotations

from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    AuditLog,
    Company,
    CompanyInvitation,
    CompanyInvitationStatus,
    CompanyMembership,
    CompanyStatus,
    MembershipStatus,
    PersonalLedgerEntry,
    PersonalWorkspace,
    User,
    UserAccountType,
    UserStatus,
    utcnow,
)
from platform_api.services.authentication import InvitationService
from platform_api.services.access_lifecycle import AccessLifecycleService
from platform_api.services.companies import CompanyService
from platform_api.services.errors import DomainError
from platform_api.services.personal import PersonalWorkspaceService

from .conftest import bootstrap
from .test_platform_admin import bootstrap_admin


def _personal_user(app, suffix: str) -> str:
    with app.state.session_factory.begin() as session:
        user = User(
            email=f"partition-personal-{suffix}@example.com",
            display_name=f"Personal {suffix}",
            status=UserStatus.ACTIVE,
            email_verified_at=utcnow(),
        )
        session.add(user)
        session.flush()
        PersonalWorkspaceService.ensure(session, user_id=user.id)
        return user.id


def test_company_account_has_one_company_product_surface_and_no_personal_access(
    app, client
) -> None:
    tenant = bootstrap(client, "partition-company")
    headers = {"X-User-ID": tenant["user_id"]}

    surfaces = client.get("/api/v1/session/surfaces", headers=headers)
    assert surfaces.status_code == 200, surfaces.text
    assert surfaces.json()["account_type"] == "company"
    assert surfaces.json()["personal"] is None
    assert [item["company_id"] for item in surfaces.json()["companies"]] == [
        tenant["company_id"]
    ]
    assert surfaces.json()["platform_admin"] is False

    denied = client.get("/api/v1/personal/me", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "account_type_mismatch"

    with app.state.session_factory() as session:
        assert (
            session.scalar(
                select(PersonalWorkspace).where(
                    PersonalWorkspace.user_id == tenant["user_id"]
                )
            )
            is None
        )


def test_platform_admin_has_only_platform_surface_and_cannot_receive_personal_points(
    app, client
) -> None:
    admin_id, admin_headers = bootstrap_admin(client, "partition-platform")
    surfaces = client.get(
        "/api/v1/session/surfaces",
        headers={"X-User-ID": admin_id},
    )
    assert surfaces.status_code == 200, surfaces.text
    assert surfaces.json()["account_type"] == "platform_admin"
    assert surfaces.json()["personal"] is None
    assert surfaces.json()["companies"] == []
    assert surfaces.json()["platform_admin"] is True

    denied_personal = client.get(
        "/api/v1/personal/me",
        headers={"X-User-ID": admin_id},
    )
    assert denied_personal.status_code == 403

    denied_points = client.post(
        f"/api/v1/platform-admin/users/{admin_id}/points-grants",
        headers=admin_headers,
        json={
            "amount_points": 100,
            "idempotency_key": "partition-platform-points-0001",
            "note": "must remain platform-only",
        },
    )
    assert denied_points.status_code == 409
    with app.state.session_factory() as session:
        assert int(session.scalar(select(func.count(PersonalLedgerEntry.id))) or 0) == 0


def test_personal_account_is_rejected_before_company_provisioning_side_effects(
    app, client
) -> None:
    tenant = bootstrap(client, "partition-guard")
    personal_id = _personal_user(app, "guard")
    with app.state.session_factory.begin() as session:
        personal = session.get(User, personal_id)
        assert personal is not None
        company_count = int(session.scalar(select(func.count(Company.id))) or 0)
        invitation_count = int(
            session.scalar(select(func.count(CompanyInvitation.id))) or 0
        )

        with pytest.raises(DomainError) as invite_error:
            InvitationService.create(
                session,
                company_id=tenant["company_id"],
                actor_user_id=tenant["user_id"],
                email=personal.email,
                display_name=personal.display_name,
                primary_role="operator",
                idempotency_key="partition-invite-0001",
                expires_in_seconds=3600,
                pepper=None,
                request_id="partition-invite-request",
            )
        assert invite_error.value.code == "account_type_conflict"

        with pytest.raises(DomainError) as member_error:
            CompanyService.add_member(
                session,
                company_id=tenant["company_id"],
                email=personal.email,
                display_name=personal.display_name,
            )
        assert member_error.value.code == "account_type_conflict"

        with pytest.raises(DomainError) as owner_error:
            CompanyService.bootstrap_company(
                session,
                company_name="Must not be created",
                owner_email=personal.email,
                owner_display_name=personal.display_name,
            )
        assert owner_error.value.code == "account_type_conflict"

        assert int(session.scalar(select(func.count(Company.id))) or 0) == company_count
        assert (
            int(session.scalar(select(func.count(CompanyInvitation.id))) or 0)
            == invitation_count
        )
        assert (
            session.scalar(
                select(CompanyMembership.id).where(
                    CompanyMembership.user_id == personal_id
                )
            )
            is None
        )


def test_personal_account_cannot_accept_a_legacy_company_invitation(
    app, client
) -> None:
    tenant = bootstrap(client, "partition-legacy-invite")
    personal_id = _personal_user(app, "legacy-invite")
    raw_token = "legacy-personal-conflict-token"
    with app.state.session_factory.begin() as session:
        personal = session.get(User, personal_id)
        assert personal is not None
        invitation = CompanyInvitation(
            token_digest=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            company_id=tenant["company_id"],
            email=personal.email,
            display_name=personal.display_name,
            primary_role="operator",
            status=CompanyInvitationStatus.PENDING,
            expires_at=utcnow() + timedelta(hours=1),
            created_by_user_id=tenant["user_id"],
            idempotency_key="legacy-partition-invite-0001",
            request_fingerprint="a" * 64,
        )
        session.add(invitation)
        session.flush()
        invitation_id = invitation.id

        with pytest.raises(DomainError) as error:
            InvitationService.accept(
                session,
                token=raw_token,
                pepper=None,
                user=personal,
                actor_session_id=None,
                request_id="legacy-partition-accept",
                ip_hash=None,
                user_agent="pytest",
            )
        assert error.value.code == "account_type_conflict"
        assert session.get(CompanyInvitation, invitation_id).status == (
            CompanyInvitationStatus.PENDING
        )
        assert (
            session.scalar(
                select(CompanyMembership.id).where(
                    CompanyMembership.user_id == personal_id
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(AuditLog.id).where(
                    AuditLog.action == "company.invitation.accept",
                    AuditLog.target_id == invitation_id,
                )
            )
            is None
        )


def test_company_account_can_join_multiple_companies_without_personal_fallback(
    app, client
) -> None:
    first = bootstrap(client, "partition-multi")
    second = client.post(
        "/api/v1/bootstrap",
        json={
            "company_name": "Company partition-multi-second",
            "owner_email": "owner-partition-multi@example.com",
            "owner_display_name": "Owner partition-multi",
        },
    )
    assert second.status_code == 201, second.text

    headers = {"X-User-ID": first["user_id"]}
    surfaces = client.get("/api/v1/session/surfaces", headers=headers)
    assert surfaces.status_code == 200, surfaces.text
    assert surfaces.json()["account_type"] == "company"
    assert surfaces.json()["personal"] is None
    assert len(surfaces.json()["companies"]) == 2

    with app.state.session_factory.begin() as session:
        assert (
            int(
                session.scalar(
                    select(func.count(CompanyMembership.id)).where(
                        CompanyMembership.user_id == first["user_id"]
                    )
                )
                or 0
            )
            == 2
        )
        for company in session.scalars(
            select(Company).join(
                CompanyMembership,
                CompanyMembership.company_id == Company.id,
            ).where(CompanyMembership.user_id == first["user_id"])
        ):
            company.status = CompanyStatus.SUSPENDED

    suspended = client.get("/api/v1/session/surfaces", headers=headers)
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["account_type"] == "company"
    assert suspended.json()["personal"] is None
    assert {item["status"] for item in suspended.json()["companies"]} == {
        "suspended"
    }


def test_personal_workspace_provisioning_fails_closed_for_inconsistent_account(
    app,
) -> None:
    with app.state.session_factory.begin() as session:
        user = User(
            email="partition-inconsistent@example.com",
            display_name="Inconsistent account",
            account_type=UserAccountType.COMPANY,
        )
        session.add(user)
        session.flush()
        session.add(PersonalWorkspace(user_id=user.id, active=True))
        session.flush()

        with pytest.raises(DomainError) as error:
            PersonalWorkspaceService.ensure(session, user_id=user.id)

        assert error.value.code == "permission_denied"


def test_member_reactivation_rejects_cross_product_account(app, client) -> None:
    tenant = bootstrap(client, "partition-reactivate")
    with app.state.session_factory.begin() as session:
        target = User(
            email="partition-reactivate-personal@example.com",
            display_name="Personal target",
            account_type=UserAccountType.PERSONAL,
        )
        session.add(target)
        session.flush()
        membership = CompanyMembership(
            company_id=tenant["company_id"],
            user_id=target.id,
            status=MembershipStatus.DISABLED,
        )
        session.add(membership)
        session.flush()

        with pytest.raises(DomainError) as error:
            AccessLifecycleService.set_member_status(
                session,
                company_id=tenant["company_id"],
                membership_id=membership.id,
                status=MembershipStatus.ACTIVE,
                actor_membership_id=tenant["membership_id"],
            )

        assert error.value.code == "account_type_conflict"
        assert membership.status == MembershipStatus.DISABLED
