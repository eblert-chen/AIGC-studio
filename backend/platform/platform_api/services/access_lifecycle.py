from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    CompanyMembership,
    MemberPermissionOverride,
    MembershipRole,
    MembershipStatus,
    Permission,
    PermissionEffect,
    Role,
    RolePermission,
    User,
)
from .errors import ConflictError, NotFoundError, PermissionDeniedError
from .permission_catalog import PERMISSION_CODES
from .permissions import PermissionService


OWNER_ROLE_KEY = "owner"
TEAM_LEAD_ROLE_KEY = "team_lead"
OPERATOR_ROLE_KEY = "operator"

ASSIGNABLE_SYSTEM_ROLE_KEYS = {TEAM_LEAD_ROLE_KEY, OPERATOR_ROLE_KEY}
PRIMARY_ROLE_KEYS = ASSIGNABLE_SYSTEM_ROLE_KEYS

TEAM_LEAD_PERMISSIONS = {
    "assets.manage",
    "assets.read",
    "users.read",
    "models.read",
    "resources.read",
    "tasks.read",
    "tasks.create",
    "publish.accounts.read",
    "publish.accounts.manage",
    "publish.jobs.read",
    "publish.jobs.manage",
}

OPERATOR_PERMISSIONS = {
    "assets.manage",
    "assets.read",
    "models.read",
    "resources.read",
    "tasks.read",
    "tasks.create",
    "publish.accounts.read",
    "publish.jobs.read",
    "publish.jobs.manage",
}


@dataclass(frozen=True)
class RoleSnapshot:
    id: str
    company_id: str
    name: str
    description: str
    is_system: bool
    system_key: str | None
    permission_codes: frozenset[str]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "name": self.name,
            "description": self.description,
            "is_system": self.is_system,
            "system_key": self.system_key,
            "permission_codes": sorted(self.permission_codes),
        }


class AccessLifecycleService:
    @staticmethod
    def role_permission_codes(session: Session, *, role_id: str) -> set[str]:
        return set(
            session.scalars(
                select(RolePermission.permission_code).where(
                    RolePermission.role_id == role_id
                )
            ).all()
        ) & set(PERMISSION_CODES)

    @classmethod
    def role_snapshot(cls, session: Session, *, role: Role) -> RoleSnapshot:
        return RoleSnapshot(
            id=role.id,
            company_id=role.company_id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            system_key=role.system_key,
            permission_codes=frozenset(
                cls.role_permission_codes(session, role_id=role.id)
            ),
        )

    @classmethod
    def list_roles(cls, session: Session, *, company_id: str) -> list[RoleSnapshot]:
        roles = list(
            session.scalars(
                select(Role)
                .where(Role.company_id == company_id)
                .order_by(Role.is_system.desc(), Role.created_at, Role.id)
            ).all()
        )
        return [cls.role_snapshot(session, role=role) for role in roles]

    @classmethod
    def roles_for_membership(
        cls, session: Session, *, company_id: str, membership_id: str
    ) -> list[RoleSnapshot]:
        roles = list(
            session.scalars(
                select(Role)
                .join(MembershipRole, MembershipRole.role_id == Role.id)
                .where(
                    MembershipRole.membership_id == membership_id,
                    Role.company_id == company_id,
                )
                .order_by(Role.is_system.desc(), Role.created_at, Role.id)
            ).all()
        )
        return [cls.role_snapshot(session, role=role) for role in roles]

    @staticmethod
    def _membership(
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        for_update: bool = False,
    ) -> CompanyMembership:
        query = select(CompanyMembership).where(
            CompanyMembership.id == membership_id,
            CompanyMembership.company_id == company_id,
        )
        if for_update:
            query = query.with_for_update()
        membership = session.scalar(query)
        if membership is None:
            raise NotFoundError("成员不属于当前公司")
        return membership

    @classmethod
    def get_membership(
        cls, session: Session, *, company_id: str, membership_id: str
    ) -> CompanyMembership:
        return cls._membership(
            session, company_id=company_id, membership_id=membership_id
        )

    @staticmethod
    def _role(session: Session, *, company_id: str, role_id: str) -> Role:
        role = session.scalar(
            select(Role).where(Role.id == role_id, Role.company_id == company_id)
        )
        if role is None:
            raise NotFoundError("角色不属于当前公司")
        return role

    @staticmethod
    def system_role(session: Session, *, company_id: str, system_key: str) -> Role:
        role = session.scalar(
            select(Role).where(
                Role.company_id == company_id,
                Role.system_key == system_key,
                Role.is_system.is_(True),
            )
        )
        if role is None:
            raise NotFoundError("公司内置角色不存在")
        return role

    @classmethod
    def _ensure_actor_is_owner(
        cls, session: Session, *, company_id: str, actor_membership_id: str
    ) -> None:
        actor_roles = cls.roles_for_membership(
            session,
            company_id=company_id,
            membership_id=actor_membership_id,
        )
        if not any(role.system_key == OWNER_ROLE_KEY for role in actor_roles):
            raise PermissionDeniedError("只有老板可以配置公司内置角色权限")

    @classmethod
    def _ensure_actor_can_manage_permissions(
        cls,
        session: Session,
        *,
        actor_membership_id: str,
        permission_codes: set[str],
    ) -> None:
        actor_permissions = PermissionService.effective_permissions(
            session, membership_id=actor_membership_id
        )
        if not permission_codes.issubset(actor_permissions):
            raise PermissionDeniedError("不能管理包含操作者自身不具备权限的角色或成员")

    @classmethod
    def current_identity(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        user_id: str,
    ) -> dict:
        membership = cls._membership(
            session, company_id=company_id, membership_id=membership_id
        )
        user = session.get(User, user_id)
        if user is None or membership.user_id != user.id:
            raise NotFoundError("当前成员身份不存在")
        return {
            "company_id": company_id,
            "user_id": user.id,
            "membership_id": membership.id,
            "email": user.email,
            "display_name": user.display_name,
            "is_platform_admin": user.is_platform_admin,
            "status": membership.status,
            "permission_codes": sorted(
                PermissionService.effective_permissions(
                    session, membership_id=membership.id
                )
            ),
            "roles": [
                role.as_dict()
                for role in cls.roles_for_membership(
                    session,
                    company_id=company_id,
                    membership_id=membership.id,
                )
            ],
        }

    @classmethod
    def set_member_status(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        status: MembershipStatus,
        actor_membership_id: str,
    ) -> tuple[MembershipStatus, CompanyMembership, bool]:
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        before = membership.status
        if before == status:
            return before, membership, False
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能停用或启用自己的成员身份")

        target_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership.id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in target_roles):
            raise PermissionDeniedError("老板成员不能被停用")
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=PermissionService.effective_permissions(
                session, membership_id=membership.id
            ),
        )
        membership.status = status
        session.flush()
        return before, membership, True

    @classmethod
    def create_role(
        cls,
        session: Session,
        *,
        company_id: str,
        name: str,
        description: str,
        permission_codes: set[str],
        actor_membership_id: str,
    ) -> tuple[Role, bool]:
        normalized_name = name.strip()
        valid_permissions = set(
            session.scalars(
                select(Permission.code).where(
                    Permission.code.in_(permission_codes & PERMISSION_CODES)
                )
            ).all()
        )
        if valid_permissions != permission_codes:
            raise NotFoundError("包含不存在的权限编码")
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=permission_codes,
        )
        existing = session.scalar(
            select(Role).where(
                Role.company_id == company_id,
                Role.name == normalized_name,
            )
        )
        if existing is not None:
            same = (
                not existing.is_system
                and existing.description == description.strip()
                and cls.role_permission_codes(session, role_id=existing.id)
                == permission_codes
            )
            if same:
                return existing, False
            raise ConflictError("角色名称已存在")

        role = Role(
            company_id=company_id,
            name=normalized_name,
            description=description.strip(),
            is_system=False,
            system_key=None,
        )
        session.add(role)
        session.flush()
        for code in sorted(permission_codes):
            session.add(RolePermission(role_id=role.id, permission_code=code))
        session.flush()
        return role, True

    @classmethod
    def update_role(
        cls,
        session: Session,
        *,
        company_id: str,
        role_id: str,
        name: str,
        description: str,
        permission_codes: set[str],
        actor_membership_id: str,
    ) -> tuple[RoleSnapshot, RoleSnapshot, bool]:
        role = cls._role(session, company_id=company_id, role_id=role_id)
        if role.system_key == OWNER_ROLE_KEY:
            raise PermissionDeniedError("老板角色模板不能修改")
        if role.is_system:
            cls._ensure_actor_is_owner(
                session,
                company_id=company_id,
                actor_membership_id=actor_membership_id,
            )
            if name.strip() != role.name:
                raise PermissionDeniedError("公司内置角色名称不能修改")
        before = cls.role_snapshot(session, role=role)
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=set(before.permission_codes) | permission_codes,
        )
        valid_permissions = set(
            session.scalars(
                select(Permission.code).where(
                    Permission.code.in_(permission_codes & PERMISSION_CODES)
                )
            ).all()
        )
        if valid_permissions != permission_codes:
            raise NotFoundError("包含不存在的权限编码")
        normalized_name = name.strip()
        duplicate = session.scalar(
            select(Role).where(
                Role.company_id == company_id,
                Role.name == normalized_name,
                Role.id != role.id,
            )
        )
        if duplicate is not None:
            raise ConflictError("角色名称已存在")
        if (
            role.name == normalized_name
            and role.description == description.strip()
            and set(before.permission_codes) == permission_codes
        ):
            return before, before, False

        role.name = normalized_name
        role.description = description.strip()
        session.execute(
            delete(RolePermission).where(RolePermission.role_id == role.id)
        )
        for code in sorted(permission_codes):
            session.add(RolePermission(role_id=role.id, permission_code=code))
        session.flush()
        return before, cls.role_snapshot(session, role=role), True

    @classmethod
    def assign_role(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        role_id: str,
        actor_membership_id: str,
    ) -> bool:
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("停用成员不能分配角色")
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能修改自己的成员角色")
        target_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in target_roles):
            raise PermissionDeniedError("老板成员的角色不能修改")
        role = cls._role(session, company_id=company_id, role_id=role_id)
        if role.system_key == OWNER_ROLE_KEY:
            raise PermissionDeniedError("老板角色不能手动分配")
        if role.is_system and role.system_key not in ASSIGNABLE_SYSTEM_ROLE_KEYS:
            raise PermissionDeniedError("该系统角色不能手动分配")
        managed_codes = cls.role_permission_codes(session, role_id=role.id)
        current_primary = [
            current_role
            for current_role in target_roles
            if current_role.system_key in PRIMARY_ROLE_KEYS
        ]
        if role.system_key in PRIMARY_ROLE_KEYS:
            for current_role in current_primary:
                managed_codes.update(current_role.permission_codes)
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=managed_codes,
        )
        current = session.get(
            MembershipRole, {"membership_id": membership_id, "role_id": role_id}
        )
        if role.system_key in PRIMARY_ROLE_KEYS:
            other_primary_ids = {
                current_role.id
                for current_role in current_primary
                if current_role.id != role.id
            }
            if other_primary_ids:
                session.execute(
                    delete(MembershipRole).where(
                        MembershipRole.membership_id == membership_id,
                        MembershipRole.role_id.in_(other_primary_ids),
                    )
                )
            if current is None:
                session.add(
                    MembershipRole(membership_id=membership_id, role_id=role_id)
                )
            if current is None or other_primary_ids:
                session.flush()
                return True
            return False
        if current is not None:
            return False
        session.add(MembershipRole(membership_id=membership_id, role_id=role_id))
        session.flush()
        return True

    @classmethod
    def unassign_role(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        role_id: str,
        actor_membership_id: str,
    ) -> bool:
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能修改自己的成员角色")
        target_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in target_roles):
            raise PermissionDeniedError("老板成员的角色不能修改")
        role = cls._role(session, company_id=company_id, role_id=role_id)
        if role.system_key == OWNER_ROLE_KEY:
            raise PermissionDeniedError("老板角色不能撤销")
        if role.system_key in PRIMARY_ROLE_KEYS:
            raise PermissionDeniedError("基础级别不能单独撤销，请使用升降级接口替换")
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=cls.role_permission_codes(session, role_id=role.id),
        )
        assignment = session.get(
            MembershipRole, {"membership_id": membership_id, "role_id": role_id}
        )
        if assignment is None:
            return False
        session.delete(assignment)
        session.flush()
        return True

    @classmethod
    def replace_roles(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        role_ids: set[str],
        actor_membership_id: str,
        expected_role_ids: set[str] | None = None,
    ) -> tuple[list[RoleSnapshot], list[RoleSnapshot], bool]:
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("停用成员不能重设角色")
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能修改自己的成员角色")
        before = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in before):
            raise PermissionDeniedError("老板成员的角色不能修改")
        requested_roles = list(
            session.scalars(
                select(Role).where(
                    Role.company_id == company_id,
                    Role.id.in_(role_ids),
                )
            ).all()
        ) if role_ids else []
        if {role.id for role in requested_roles} != role_ids:
            raise NotFoundError("包含不属于当前公司的角色")
        if any(role.system_key == OWNER_ROLE_KEY for role in requested_roles):
            raise PermissionDeniedError("老板角色不能通过重设接口分配")
        primary_roles = [
            role
            for role in requested_roles
            if role.system_key in PRIMARY_ROLE_KEYS
        ]
        if len(primary_roles) != 1:
            raise ConflictError("运营或组长必须且只能选择一个基础级别")

        current_mutable = [
            role for role in before if role.system_key != OWNER_ROLE_KEY
        ]
        managed_codes: set[str] = set()
        for role in current_mutable:
            managed_codes.update(role.permission_codes)
        for role in requested_roles:
            managed_codes.update(
                cls.role_permission_codes(session, role_id=role.id)
            )
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=managed_codes,
        )

        current_ids = {role.id for role in current_mutable}
        if expected_role_ids is not None and current_ids != expected_role_ids:
            raise ConflictError("成员角色已被其他会话更新，请刷新后重试")
        if current_ids == role_ids:
            return before, before, False
        if current_ids:
            session.execute(
                delete(MembershipRole).where(
                    MembershipRole.membership_id == membership_id,
                    MembershipRole.role_id.in_(current_ids),
                )
            )
        for role_id in sorted(role_ids):
            session.add(MembershipRole(membership_id=membership_id, role_id=role_id))
        session.flush()
        after = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        return before, after, True

    @classmethod
    def delete_role(
        cls,
        session: Session,
        *,
        company_id: str,
        role_id: str,
        actor_membership_id: str,
    ) -> RoleSnapshot:
        role = cls._role(session, company_id=company_id, role_id=role_id)
        if role.is_system:
            raise PermissionDeniedError("系统角色模板不能删除")
        snapshot = cls.role_snapshot(session, role=role)
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=set(snapshot.permission_codes),
        )
        list(
            session.scalars(
                select(CompanyMembership)
                .join(
                    MembershipRole,
                    MembershipRole.membership_id == CompanyMembership.id,
                )
                .where(
                    CompanyMembership.company_id == company_id,
                    MembershipRole.role_id == role.id,
                )
                .order_by(CompanyMembership.id)
                .with_for_update()
            ).all()
        )
        session.execute(delete(MembershipRole).where(MembershipRole.role_id == role.id))
        session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        session.delete(role)
        session.flush()
        return snapshot

    @classmethod
    def clear_override(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        permission_code: str,
        actor_membership_id: str,
    ) -> tuple[PermissionEffect | None, bool]:
        cls._ensure_actor_is_owner(
            session,
            company_id=company_id,
            actor_membership_id=actor_membership_id,
        )
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if (
            not PermissionService.is_catalog_permission(permission_code)
            or session.get(Permission, permission_code) is None
        ):
            raise NotFoundError("权限不存在")
        if membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("停用成员不能修改个人权限")
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能通过个人覆盖修改自己的管理权限")
        target_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in target_roles):
            raise PermissionDeniedError("老板成员的个人权限不能被覆盖")
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes={permission_code},
        )
        override = session.scalar(
            select(MemberPermissionOverride).where(
                MemberPermissionOverride.membership_id == membership_id,
                MemberPermissionOverride.permission_code == permission_code,
            )
        )
        if override is None:
            return None, False
        before_effect = override.effect
        session.delete(override)
        session.flush()
        return before_effect, True

    @classmethod
    def set_override(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        permission_code: str,
        effect: PermissionEffect,
        actor_membership_id: str,
    ) -> tuple[MemberPermissionOverride, PermissionEffect | None, bool]:
        cls._ensure_actor_is_owner(
            session,
            company_id=company_id,
            actor_membership_id=actor_membership_id,
        )
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if (
            not PermissionService.is_catalog_permission(permission_code)
            or session.get(Permission, permission_code) is None
        ):
            raise NotFoundError("权限不存在")
        if membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("停用成员不能修改个人权限")
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能通过个人覆盖修改自己的管理权限")
        target_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in target_roles):
            raise PermissionDeniedError("老板成员的个人权限不能被覆盖")
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes={permission_code},
        )
        existing = session.scalar(
            select(MemberPermissionOverride).where(
                MemberPermissionOverride.membership_id == membership_id,
                MemberPermissionOverride.permission_code == permission_code,
            )
        )
        before_effect = existing.effect if existing is not None else None
        if existing is not None and existing.effect == effect:
            return existing, before_effect, False
        if existing is None:
            existing = MemberPermissionOverride(
                membership_id=membership_id,
                permission_code=permission_code,
                effect=effect,
            )
            session.add(existing)
        else:
            existing.effect = effect
        session.flush()
        return existing, before_effect, True

    @classmethod
    def replace_overrides(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        overrides: dict[str, PermissionEffect],
        actor_membership_id: str,
        expected_overrides: dict[str, PermissionEffect] | None = None,
    ) -> tuple[
        dict[str, PermissionEffect],
        dict[str, PermissionEffect],
        bool,
    ]:
        cls._ensure_actor_is_owner(
            session,
            company_id=company_id,
            actor_membership_id=actor_membership_id,
        )
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("停用成员不能修改个人权限")
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能通过个人覆盖修改自己的管理权限")
        target_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in target_roles):
            raise PermissionDeniedError("老板成员的个人权限不能被覆盖")
        catalog_codes = set(
            session.scalars(
                select(Permission.code).where(Permission.code.in_(PERMISSION_CODES))
            ).all()
        )
        requested_codes = set(overrides)
        if expected_overrides is not None:
            requested_codes.update(expected_overrides)
        if not requested_codes.issubset(catalog_codes):
            raise NotFoundError("包含不存在的权限编码")
        before = PermissionService.permission_overrides(
            session, membership_id=membership_id
        )
        cls._ensure_actor_can_manage_permissions(
            session,
            actor_membership_id=actor_membership_id,
            permission_codes=set(before) | set(overrides),
        )
        if expected_overrides is not None and before != expected_overrides:
            raise ConflictError("成员权限已被其他会话更新，请刷新后重试")
        if before == overrides:
            return before, before, False

        session.execute(
            delete(MemberPermissionOverride).where(
                MemberPermissionOverride.membership_id == membership_id
            )
        )
        for permission_code, effect in sorted(overrides.items()):
            session.add(
                MemberPermissionOverride(
                    membership_id=membership_id,
                    permission_code=permission_code,
                    effect=effect,
                )
            )
        session.flush()
        after = PermissionService.permission_overrides(
            session, membership_id=membership_id
        )
        return before, after, True

    @classmethod
    def replace_member_access(
        cls,
        session: Session,
        *,
        company_id: str,
        membership_id: str,
        role_ids: set[str],
        permission_overrides: dict[str, PermissionEffect],
        actor_membership_id: str,
        expected_role_ids: set[str],
        expected_permission_overrides: dict[str, PermissionEffect],
    ) -> tuple[
        list[RoleSnapshot],
        list[RoleSnapshot],
        dict[str, PermissionEffect],
        dict[str, PermissionEffect],
        bool,
    ]:
        cls._ensure_actor_is_owner(
            session,
            company_id=company_id,
            actor_membership_id=actor_membership_id,
        )
        membership = cls._membership(
            session,
            company_id=company_id,
            membership_id=membership_id,
            for_update=True,
        )
        if membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("停用成员不能修改访问权限")
        if membership.id == actor_membership_id:
            raise PermissionDeniedError("不能修改自己的成员访问权限")
        current_roles = cls.roles_for_membership(
            session, company_id=company_id, membership_id=membership_id
        )
        if any(role.system_key == OWNER_ROLE_KEY for role in current_roles):
            raise PermissionDeniedError("老板成员的访问权限不能修改")
        current_role_ids = {role.id for role in current_roles}
        current_overrides = PermissionService.permission_overrides(
            session, membership_id=membership_id
        )
        if (
            current_role_ids != expected_role_ids
            or current_overrides != expected_permission_overrides
        ):
            raise ConflictError("成员访问配置已被其他会话更新，请刷新后重试")
        before_roles, after_roles, roles_changed = cls.replace_roles(
            session,
            company_id=company_id,
            membership_id=membership_id,
            role_ids=role_ids,
            actor_membership_id=actor_membership_id,
        )
        before_overrides, after_overrides, overrides_changed = cls.replace_overrides(
            session,
            company_id=company_id,
            membership_id=membership_id,
            overrides=permission_overrides,
            actor_membership_id=actor_membership_id,
        )
        return (
            before_roles,
            after_roles,
            before_overrides,
            after_overrides,
            roles_changed or overrides_changed,
        )
