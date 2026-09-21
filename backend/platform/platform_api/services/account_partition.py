from __future__ import annotations

from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyMembership,
    ExternalIdentity,
    MembershipStatus,
    PersonalWorkspace,
    ProductContext,
    User,
    UserAccountType,
)
from .errors import DomainError


class AccountProductType(str, Enum):
    """Validated API-facing product account resolution."""

    PERSONAL = "personal"
    COMPANY = "company"
    PLATFORM_ADMIN = "platform_admin"
    UNAVAILABLE = "unavailable"


class AccountPartitionService:
    """Resolve and enforce the personal/company/platform account partition.

    ``User.account_type`` is the authoritative product boundary.  Migration 0044
    preserves historical rows and ledgers but disables conflicting workspaces or
    memberships.  Any later cross-table inconsistency therefore fails closed;
    it is never resolved dynamically by choosing a higher-precedence surface.
    """

    @staticmethod
    def has_company_membership(session: Session, *, user_id: str) -> bool:
        return session.scalar(
            select(CompanyMembership.id)
            .where(CompanyMembership.user_id == user_id)
            .limit(1)
        ) is not None

    @staticmethod
    def has_active_company_membership(session: Session, *, user_id: str) -> bool:
        return session.scalar(
            select(CompanyMembership.id)
            .where(
                CompanyMembership.user_id == user_id,
                CompanyMembership.status == MembershipStatus.ACTIVE,
            )
            .limit(1)
        ) is not None

    @staticmethod
    def personal_workspace(
        session: Session, *, user_id: str, for_update: bool = False
    ) -> PersonalWorkspace | None:
        statement = select(PersonalWorkspace).where(
            PersonalWorkspace.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @classmethod
    def resolve(cls, session: Session, *, user: User):
        """Return the persisted account type after checking cross-table evidence."""

        account_type = user.account_type
        workspace = cls.personal_workspace(session, user_id=user.id)
        has_membership = cls.has_company_membership(session, user_id=user.id)
        has_active_membership = cls.has_active_company_membership(
            session,
            user_id=user.id,
        )
        if account_type == UserAccountType.PLATFORM_ADMIN:
            if (
                not user.is_platform_admin
                or has_active_membership
                or (
                    workspace is not None
                    and workspace.active
                    and workspace.owner_self_identity_id is None
                )
            ):
                return AccountProductType.UNAVAILABLE
            return AccountProductType.PLATFORM_ADMIN
        if user.is_platform_admin:
            return AccountProductType.UNAVAILABLE
        if account_type == UserAccountType.COMPANY:
            if workspace is not None and workspace.active:
                return AccountProductType.UNAVAILABLE
            return AccountProductType.COMPANY
        if account_type == UserAccountType.PERSONAL:
            if has_membership:
                return AccountProductType.UNAVAILABLE
            return AccountProductType.PERSONAL
        return AccountProductType.UNAVAILABLE

    @classmethod
    def require_company_provisioning_eligible(
        cls, session: Session, *, user: User
    ) -> None:
        """Allow an existing company account or an unactivated invitee only."""

        if cls.resolve(session, user=user) == AccountProductType.COMPANY:
            return
        message = (
            "平台管理员账号不能加入企业"
            if user.account_type == UserAccountType.PLATFORM_ADMIN
            else "该邮箱已经属于其他账号类型，不能直接加入企业；请改用企业邮箱"
        )
        raise DomainError(
            message,
            "account_type_conflict",
            409,
        )

    @classmethod
    def require_personal(cls, session: Session, *, user: User) -> PersonalWorkspace:
        """Require a normal PERSONAL principal; owner-self is deliberately separate."""

        account_type = cls.resolve(session, user=user)
        if account_type != AccountProductType.PERSONAL:
            raise DomainError(
                "当前账号不是个人用户，不能访问个人空间",
                "account_type_mismatch",
                403,
            )
        workspace = cls.personal_workspace(session, user_id=user.id)
        if workspace is None or not workspace.active:
            raise DomainError("个人空间不可用", "personal_workspace_unavailable", 403)
        return workspace

    @classmethod
    def require_owner_self_personal(
        cls,
        session: Session,
        *,
        user: User,
        external_identity_id: str,
    ) -> PersonalWorkspace:
        """Resolve only the platform owner's explicitly linked self workspace."""

        if (
            user.account_type != UserAccountType.PLATFORM_ADMIN
            or not user.is_platform_admin
        ):
            raise DomainError(
                "当前账号没有平台所有者个人创作主体",
                "owner_self_workspace_unavailable",
                403,
            )
        identity = session.get(ExternalIdentity, external_identity_id)
        workspace = cls.personal_workspace(session, user_id=user.id)
        if (
            identity is None
            or identity.user_id != user.id
            or workspace is None
            or not workspace.active
            or workspace.owner_self_identity_id != identity.id
        ):
            raise DomainError(
                "平台所有者个人创作主体不可用",
                "owner_self_workspace_unavailable",
                403,
            )
        return workspace

    @classmethod
    def require_personal_product_context(
        cls,
        session: Session,
        *,
        user: User,
        active_product_context: ProductContext,
        external_identity_id: str | None,
    ) -> PersonalWorkspace:
        """Resolve the one personal principal selected by the current session."""

        if active_product_context != ProductContext.PERSONAL:
            workspace = cls.personal_workspace(session, user_id=user.id)
            if not (
                user.account_type == UserAccountType.PLATFORM_ADMIN
                and workspace is not None
                and workspace.active
                and workspace.owner_self_identity_id is not None
            ):
                raise DomainError(
                    "当前账号不是个人用户，不能访问个人空间",
                    "account_type_mismatch",
                    403,
                )
            raise DomainError(
                "当前会话未进入个人创作空间",
                "product_context_mismatch",
                403,
            )
        if user.account_type == UserAccountType.PERSONAL:
            return cls.require_personal(session, user=user)
        if external_identity_id is None:
            raise DomainError(
                "个人创作主体身份不可用",
                "owner_self_workspace_unavailable",
                403,
            )
        return cls.require_owner_self_personal(
            session,
            user=user,
            external_identity_id=external_identity_id,
        )
