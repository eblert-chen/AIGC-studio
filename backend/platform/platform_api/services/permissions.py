from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyMembership,
    MemberPermissionOverride,
    MembershipRole,
    Permission,
    PermissionEffect,
    Role,
    RolePermission,
)
from .errors import PermissionDeniedError
from .permission_catalog import PERMISSION_CODES


class PermissionService:
    @staticmethod
    def inherited_permissions(session: Session, *, membership_id: str) -> set[str]:
        return set(
            session.scalars(
                select(RolePermission.permission_code)
                .join(MembershipRole, MembershipRole.role_id == RolePermission.role_id)
                .join(Role, Role.id == MembershipRole.role_id)
                .join(
                    CompanyMembership,
                    CompanyMembership.id == MembershipRole.membership_id,
                )
                .where(
                    MembershipRole.membership_id == membership_id,
                    Role.company_id == CompanyMembership.company_id,
                    RolePermission.permission_code.in_(PERMISSION_CODES),
                )
            ).all()
        )

    @staticmethod
    def permission_overrides(
        session: Session, *, membership_id: str
    ) -> dict[str, PermissionEffect]:
        rows = session.execute(
            select(
                MemberPermissionOverride.permission_code,
                MemberPermissionOverride.effect,
            )
            .where(
                MemberPermissionOverride.membership_id == membership_id,
                MemberPermissionOverride.permission_code.in_(PERMISSION_CODES),
            )
            .order_by(MemberPermissionOverride.permission_code)
        ).all()
        return {code: effect for code, effect in rows}

    @staticmethod
    def list_catalog(session: Session) -> list[Permission]:
        return list(
            session.scalars(
                select(Permission)
                .where(Permission.code.in_(PERMISSION_CODES))
                .order_by(Permission.code)
            ).all()
        )

    @staticmethod
    def is_catalog_permission(permission_code: str) -> bool:
        return permission_code in PERMISSION_CODES

    @staticmethod
    def apply_overrides(
        inherited: set[str], overrides: dict[str, PermissionEffect]
    ) -> set[str]:
        permissions = set(inherited)
        for code, effect in overrides.items():
            if effect == PermissionEffect.DENY:
                permissions.discard(code)
            else:
                permissions.add(code)
        return permissions

    @classmethod
    def effective_permissions(cls, session: Session, *, membership_id: str) -> set[str]:
        return cls.apply_overrides(
            cls.inherited_permissions(session, membership_id=membership_id),
            cls.permission_overrides(session, membership_id=membership_id),
        )

    @classmethod
    def permission_detail(
        cls, session: Session, *, membership_id: str
    ) -> list[dict]:
        inherited = cls.inherited_permissions(
            session, membership_id=membership_id
        )
        overrides = cls.permission_overrides(
            session, membership_id=membership_id
        )
        effective = cls.apply_overrides(inherited, overrides)
        return [
            {
                "code": permission.code,
                "description": permission.description,
                "inherited": permission.code in inherited,
                "override_effect": overrides.get(permission.code),
                "effective": permission.code in effective,
            }
            for permission in cls.list_catalog(session)
        ]

    @classmethod
    def require(
        cls, session: Session, *, membership_id: str, permission_code: str
    ) -> None:
        if permission_code not in cls.effective_permissions(
            session, membership_id=membership_id
        ):
            raise PermissionDeniedError()
