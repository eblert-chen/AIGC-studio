from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CompanyMembership,
    CompanyStatus,
    MembershipRole,
    MembershipStatus,
    Permission,
    Role,
    RolePermission,
    User,
    UserStatus,
    WalletAccount,
)
from .errors import ConflictError, NotFoundError
from ..auth import JwtAuthenticationError, normalize_email_address
from .permission_catalog import PERMISSION_CATALOG
from .access_lifecycle import (
    OPERATOR_PERMISSIONS,
    OPERATOR_ROLE_KEY,
    OWNER_ROLE_KEY,
    TEAM_LEAD_PERMISSIONS,
    TEAM_LEAD_ROLE_KEY,
)


class CompanyService:
    @staticmethod
    def ensure_permission_catalog(session: Session) -> None:
        existing = set(session.scalars(select(Permission.code)).all())
        for code, description in PERMISSION_CATALOG.items():
            if code not in existing:
                session.add(Permission(code=code, description=description))
        session.flush()

    @classmethod
    def bootstrap_company(
        cls,
        session: Session,
        *,
        company_name: str,
        owner_email: str,
        owner_display_name: str,
        owner_activation_required: bool = False,
    ) -> tuple[Company, User, CompanyMembership]:
        cls.ensure_permission_catalog(session)
        try:
            normalized_email = normalize_email_address(owner_email)
        except JwtAuthenticationError as exc:
            raise ConflictError("公司所有者邮箱无效") from exc
        user = session.scalar(
            select(User)
            .where(func.lower(User.email) == normalized_email.lower())
            .with_for_update()
        )
        if user is not None and user.status in {
            UserStatus.SUSPENDED,
            UserStatus.DEACTIVATED,
        }:
            raise ConflictError("公司所有者账号不可用")

        company = Company(name=company_name.strip(), status=CompanyStatus.ACTIVE)
        if user is None:
            user = User(
                email=normalized_email,
                display_name=owner_display_name.strip(),
                status=(
                    UserStatus.PENDING
                    if owner_activation_required
                    else UserStatus.ACTIVE
                ),
            )
            session.add(user)
        session.add(company)
        session.flush()

        owner_ready = (
            user.status == UserStatus.ACTIVE and user.email_verified_at is not None
        )
        membership_status = (
            MembershipStatus.ACTIVE
            if not owner_activation_required or owner_ready
            else MembershipStatus.DISABLED
        )

        membership = CompanyMembership(
            company_id=company.id,
            user_id=user.id,
            status=membership_status,
        )
        wallet = WalletAccount(company_id=company.id)
        owner_role = Role(
            company_id=company.id,
            name="老板",
            description="公司内置全权限角色",
            is_system=True,
            system_key=OWNER_ROLE_KEY,
        )
        team_lead_role = Role(
            company_id=company.id,
            name="组长",
            description="公司内置组长权限模板，老板可按成员继续微调",
            is_system=True,
            system_key=TEAM_LEAD_ROLE_KEY,
        )
        operator_role = Role(
            company_id=company.id,
            name="运营",
            description="公司内置运营权限模板，老板可按成员继续微调",
            is_system=True,
            system_key=OPERATOR_ROLE_KEY,
        )
        session.add_all(
            [membership, wallet, owner_role, team_lead_role, operator_role]
        )
        session.flush()

        session.add(MembershipRole(membership_id=membership.id, role_id=owner_role.id))
        for permission_code in PERMISSION_CATALOG:
            session.add(
                RolePermission(role_id=owner_role.id, permission_code=permission_code)
            )
        for permission_code in sorted(TEAM_LEAD_PERMISSIONS):
            session.add(
                RolePermission(
                    role_id=team_lead_role.id, permission_code=permission_code
                )
            )
        for permission_code in sorted(OPERATOR_PERMISSIONS):
            session.add(
                RolePermission(
                    role_id=operator_role.id, permission_code=permission_code
                )
            )
        session.flush()
        return company, user, membership

    @staticmethod
    def add_member(
        session: Session,
        *,
        company_id: str,
        email: str,
        display_name: str,
    ) -> tuple[User, CompanyMembership, bool]:
        company = session.get(Company, company_id)
        if not company or company.status != CompanyStatus.ACTIVE:
            raise NotFoundError("公司不存在或不可用")

        normalized_email = email.strip().lower()
        user = session.scalar(select(User).where(User.email == normalized_email))
        if user is None:
            user = User(email=normalized_email, display_name=display_name.strip())
            session.add(user)
            session.flush()

        existing = session.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.user_id == user.id,
            )
        )
        if existing:
            if (
                user.display_name == display_name.strip()
                and existing.status == MembershipStatus.ACTIVE
            ):
                return user, existing, False
            raise ConflictError("该用户已经是公司成员，请使用成员状态接口管理")

        membership = CompanyMembership(
            company_id=company_id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)
        session.flush()
        return user, membership, True

    @staticmethod
    def list_members(
        session: Session, *, company_id: str
    ) -> list[tuple[User, CompanyMembership]]:
        statement = (
            select(User, CompanyMembership)
            .join(CompanyMembership, CompanyMembership.user_id == User.id)
            .where(CompanyMembership.company_id == company_id)
            .order_by(User.created_at)
        )
        return list(session.execute(statement).all())
