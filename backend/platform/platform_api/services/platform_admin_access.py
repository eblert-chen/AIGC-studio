from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import PlatformAdminActivity, User
from ..platform_admin_access_catalog import (
    PLATFORM_ADMIN_PERMISSION_CATALOG,
    PLATFORM_ADMIN_PERMISSION_CODES,
)
from ..platform_admin_access_models import (
    PlatformAdminAccessProfile,
    PlatformAdminPermission,
    PlatformAdminPermissionEffect,
    PlatformAdminRole,
    PlatformAdminRoleAssignment,
    PlatformAdminRolePermission,
    PlatformAdminUserPermissionOverride,
)
from ..platform_admin_access_policy import (
    resolve_platform_admin_route_permission,
    resolve_platform_admin_route_permissions,
)
from .audit import AuditService
from .errors import ConflictError, NotFoundError, PermissionDeniedError


_ROLE_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,79}\Z")


@dataclass(frozen=True)
class PlatformAdminAccessSnapshot:
    user_id: str
    is_platform_owner: bool
    lock_version: int
    role_ids: tuple[str, ...]
    inherited_permissions: frozenset[str]
    permission_overrides: dict[str, PlatformAdminPermissionEffect]
    effective_permissions: frozenset[str]
    snapshot: str


@dataclass(frozen=True)
class PlatformAdminRoleSnapshot:
    id: str
    key: str
    display_name: str
    description: str
    active: bool
    lock_version: int
    permission_codes: tuple[str, ...]


class PlatformAdminAccessService:
    """Server-enforced access for the single platform-administrator boundary.

    Product owners are identified only by the server-side subject allowlist. They
    always receive the complete catalog and cannot be narrowed through database
    assignments. Every other platform administrator starts with no permission.
    """

    @staticmethod
    def _change_reason(value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3 or len(normalized) > 500:
            raise ConflictError(
                "Platform administrator access changes require a reason "
                "between 3 and 500 characters"
            )
        return normalized

    @staticmethod
    def permission_for_request(*, method: str, route_path: str) -> str | None:
        """Resolve legacy route authorization; unknown admin routes fail closed."""

        return resolve_platform_admin_route_permission(
            method=method, route_path=route_path
        )

    @staticmethod
    def permissions_for_request(
        *, method: str, route_path: str
    ) -> tuple[str, ...] | None:
        return resolve_platform_admin_route_permissions(
            method=method, route_path=route_path
        )

    @classmethod
    def authorize_request(
        cls,
        session: Session,
        *,
        user_id: str,
        platform_owner_user_ids: set[str] | frozenset[str],
        method: str,
        route_path: str,
    ) -> tuple[str, ...] | None:
        """Authorize an existing admin route and return its delegated policy.

        Owners return ``None`` after their database administrator flag is
        verified. For non-owners, ``None`` from the resolver is never treated as
        public access: an unregistered route is rejected explicitly.
        """

        if not cls._user_is_platform_admin(session, user_id):
            raise PermissionDeniedError("Not a platform administrator")
        if user_id in platform_owner_user_ids:
            return None
        permission_codes = cls.permissions_for_request(
            method=method, route_path=route_path
        )
        if permission_codes is None:
            raise PermissionDeniedError(
                "Platform administrator route has no delegated access policy"
            )
        for permission_code in permission_codes:
            cls.require(
                session,
                user_id=user_id,
                permission_code=permission_code,
                platform_owner_user_ids=platform_owner_user_ids,
            )
        return permission_codes

    @staticmethod
    def sync_catalog(session: Session) -> None:
        """Idempotently mirror the immutable code catalog into a fresh database."""

        existing = {
            permission.code: permission
            for permission in session.scalars(
                select(PlatformAdminPermission)
            ).all()
        }
        for spec in PLATFORM_ADMIN_PERMISSION_CATALOG:
            permission = existing.get(spec.code)
            if permission is None:
                session.add(
                    PlatformAdminPermission(
                        code=spec.code,
                        domain=spec.domain,
                        action=spec.action,
                        description=spec.description,
                    )
                )
                continue
            permission.domain = spec.domain
            permission.action = spec.action
            permission.description = spec.description
        session.flush()

    @staticmethod
    def _validate_permission_codes(permission_codes: set[str]) -> None:
        unknown = sorted(permission_codes - PLATFORM_ADMIN_PERMISSION_CODES)
        if unknown:
            raise ConflictError(
                "Unknown platform administrator permissions: " + ", ".join(unknown)
            )

    @staticmethod
    def _user_is_platform_admin(session: Session, user_id: str) -> bool:
        user = session.get(User, user_id)
        return user is not None and user.is_platform_admin

    @classmethod
    def effective_permissions(
        cls,
        session: Session,
        *,
        user_id: str,
        platform_owner_user_ids: set[str] | frozenset[str],
    ) -> frozenset[str]:
        if not cls._user_is_platform_admin(session, user_id):
            return frozenset()
        if user_id in platform_owner_user_ids:
            return PLATFORM_ADMIN_PERMISSION_CODES

        profile = session.get(PlatformAdminAccessProfile, user_id)
        if profile is None:
            return frozenset()

        inherited = set(
            session.scalars(
                select(PlatformAdminRolePermission.permission_code)
                .join(
                    PlatformAdminRoleAssignment,
                    PlatformAdminRoleAssignment.role_id
                    == PlatformAdminRolePermission.role_id,
                )
                .join(
                    PlatformAdminRole,
                    PlatformAdminRole.id == PlatformAdminRolePermission.role_id,
                )
                .where(
                    PlatformAdminRoleAssignment.user_id == user_id,
                    PlatformAdminRole.active.is_(True),
                    PlatformAdminRolePermission.permission_code.in_(
                        PLATFORM_ADMIN_PERMISSION_CODES
                    ),
                )
            ).all()
        )
        overrides = dict(
            session.execute(
                select(
                    PlatformAdminUserPermissionOverride.permission_code,
                    PlatformAdminUserPermissionOverride.effect,
                )
                .where(
                    PlatformAdminUserPermissionOverride.user_id == user_id,
                    PlatformAdminUserPermissionOverride.permission_code.in_(
                        PLATFORM_ADMIN_PERMISSION_CODES
                    ),
                )
            ).all()
        )
        for code, effect in overrides.items():
            if effect == PlatformAdminPermissionEffect.DENY:
                inherited.discard(code)
            else:
                inherited.add(code)
        return frozenset(inherited)

    @classmethod
    def require(
        cls,
        session: Session,
        *,
        user_id: str,
        permission_code: str,
        platform_owner_user_ids: set[str] | frozenset[str],
    ) -> None:
        cls._validate_permission_codes({permission_code})
        if permission_code not in cls.effective_permissions(
            session,
            user_id=user_id,
            platform_owner_user_ids=platform_owner_user_ids,
        ):
            raise PermissionDeniedError(
                f"Platform administrator permission required: {permission_code}"
            )

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        serialized = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(serialized).hexdigest()

    @classmethod
    def access_snapshot(
        cls,
        session: Session,
        *,
        user_id: str,
        platform_owner_user_ids: set[str] | frozenset[str],
    ) -> PlatformAdminAccessSnapshot:
        user = session.get(User, user_id)
        if user is None or not user.is_platform_admin:
            raise NotFoundError("Platform administrator does not exist")

        is_owner = user_id in platform_owner_user_ids
        if is_owner:
            effective = PLATFORM_ADMIN_PERMISSION_CODES
            payload = {
                "user_id": user_id,
                "owner": True,
                "catalog": sorted(effective),
            }
            return PlatformAdminAccessSnapshot(
                user_id=user_id,
                is_platform_owner=True,
                lock_version=0,
                role_ids=(),
                inherited_permissions=effective,
                permission_overrides={},
                effective_permissions=effective,
                snapshot=cls._fingerprint(payload),
            )

        profile = session.get(PlatformAdminAccessProfile, user_id)
        if profile is None:
            payload = {"user_id": user_id, "owner": False, "lock_version": 0}
            return PlatformAdminAccessSnapshot(
                user_id=user_id,
                is_platform_owner=False,
                lock_version=0,
                role_ids=(),
                inherited_permissions=frozenset(),
                permission_overrides={},
                effective_permissions=frozenset(),
                snapshot=cls._fingerprint(payload),
            )

        role_rows = session.execute(
            select(
                PlatformAdminRoleAssignment.role_id,
                PlatformAdminRole.lock_version,
                PlatformAdminRole.active,
            )
            .join(
                PlatformAdminRole,
                PlatformAdminRole.id == PlatformAdminRoleAssignment.role_id,
            )
            .where(PlatformAdminRoleAssignment.user_id == user_id)
            .order_by(PlatformAdminRoleAssignment.role_id)
        ).all()
        role_ids = tuple(row.role_id for row in role_rows)
        inherited = set(
            session.scalars(
                select(PlatformAdminRolePermission.permission_code)
                .join(
                    PlatformAdminRoleAssignment,
                    PlatformAdminRoleAssignment.role_id
                    == PlatformAdminRolePermission.role_id,
                )
                .join(
                    PlatformAdminRole,
                    PlatformAdminRole.id == PlatformAdminRolePermission.role_id,
                )
                .where(
                    PlatformAdminRoleAssignment.user_id == user_id,
                    PlatformAdminRole.active.is_(True),
                    PlatformAdminRolePermission.permission_code.in_(
                        PLATFORM_ADMIN_PERMISSION_CODES
                    ),
                )
            ).all()
        )
        overrides = dict(
            session.execute(
                select(
                    PlatformAdminUserPermissionOverride.permission_code,
                    PlatformAdminUserPermissionOverride.effect,
                )
                .where(PlatformAdminUserPermissionOverride.user_id == user_id)
                .order_by(PlatformAdminUserPermissionOverride.permission_code)
            ).all()
        )
        effective = set(inherited)
        for code, effect in overrides.items():
            if effect == PlatformAdminPermissionEffect.DENY:
                effective.discard(code)
            elif code in PLATFORM_ADMIN_PERMISSION_CODES:
                effective.add(code)

        payload = {
            "user_id": user_id,
            "owner": False,
            "lock_version": profile.lock_version,
            "roles": [
                {
                    "id": row.role_id,
                    "version": row.lock_version,
                    "active": row.active,
                }
                for row in role_rows
            ],
            "overrides": {
                code: effect.value for code, effect in sorted(overrides.items())
            },
            "effective": sorted(effective),
        }
        return PlatformAdminAccessSnapshot(
            user_id=user_id,
            is_platform_owner=False,
            lock_version=profile.lock_version,
            role_ids=role_ids,
            inherited_permissions=frozenset(inherited),
            permission_overrides=overrides,
            effective_permissions=frozenset(effective),
            snapshot=cls._fingerprint(payload),
        )

    @staticmethod
    def _role_permissions(session: Session, role_id: str) -> tuple[str, ...]:
        return tuple(
            session.scalars(
                select(PlatformAdminRolePermission.permission_code)
                .where(PlatformAdminRolePermission.role_id == role_id)
                .order_by(PlatformAdminRolePermission.permission_code)
            ).all()
        )

    @classmethod
    def role_snapshot(
        cls, session: Session, *, role: PlatformAdminRole
    ) -> PlatformAdminRoleSnapshot:
        return PlatformAdminRoleSnapshot(
            id=role.id,
            key=role.key,
            display_name=role.display_name,
            description=role.description,
            active=role.active,
            lock_version=role.lock_version,
            permission_codes=cls._role_permissions(session, role.id),
        )

    @classmethod
    def list_roles(cls, session: Session) -> list[PlatformAdminRoleSnapshot]:
        return [
            cls.role_snapshot(session, role=role)
            for role in session.scalars(
                select(PlatformAdminRole).order_by(
                    PlatformAdminRole.display_name, PlatformAdminRole.id
                )
            ).all()
        ]

    @classmethod
    def _assert_actor_can_delegate(
        cls,
        session: Session,
        *,
        actor_user_id: str,
        permission_codes: set[str],
        platform_owner_user_ids: set[str] | frozenset[str],
    ) -> None:
        if actor_user_id in platform_owner_user_ids:
            return
        actor_permissions = cls.effective_permissions(
            session,
            user_id=actor_user_id,
            platform_owner_user_ids=platform_owner_user_ids,
        )
        undelegable = sorted(permission_codes - actor_permissions)
        if undelegable:
            raise PermissionDeniedError(
                "Cannot delegate permissions the actor does not hold: "
                + ", ".join(undelegable)
            )

    @classmethod
    def create_role(
        cls,
        session: Session,
        *,
        actor_user_id: str,
        key: str,
        display_name: str,
        description: str,
        permission_codes: set[str],
        platform_owner_user_ids: set[str] | frozenset[str],
        request_id: str,
        change_reason: str,
    ) -> PlatformAdminRoleSnapshot:
        change_reason = cls._change_reason(change_reason)
        cls.sync_catalog(session)
        if not _ROLE_KEY.fullmatch(key):
            raise ConflictError(
                "Role key must start with a lowercase letter and contain only "
                "lowercase letters, digits, '.', '_' or '-'"
            )
        cls._validate_permission_codes(permission_codes)
        cls._assert_actor_can_delegate(
            session,
            actor_user_id=actor_user_id,
            permission_codes=permission_codes,
            platform_owner_user_ids=platform_owner_user_ids,
        )
        if session.scalar(select(PlatformAdminRole).where(PlatformAdminRole.key == key)):
            raise ConflictError("Platform administrator role key already exists")
        role = PlatformAdminRole(
            key=key,
            display_name=display_name.strip(),
            description=description.strip(),
            active=True,
            lock_version=1,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        session.add(role)
        session.flush()
        for code in sorted(permission_codes):
            session.add(
                PlatformAdminRolePermission(role_id=role.id, permission_code=code)
            )
        session.flush()
        snapshot = cls.role_snapshot(session, role=role)
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="platform_admin_access.role.create",
            target_type="platform_admin_role",
            target_id=role.id,
            before_summary={},
            after_summary={
                **cls.role_summary(snapshot),
                "change_reason": change_reason,
            },
            request_id=request_id,
        )
        return snapshot

    @classmethod
    def replace_role(
        cls,
        session: Session,
        *,
        actor_user_id: str,
        role_id: str,
        display_name: str,
        description: str,
        active: bool,
        permission_codes: set[str],
        expected_lock_version: int,
        platform_owner_user_ids: set[str] | frozenset[str],
        request_id: str,
        change_reason: str,
    ) -> PlatformAdminRoleSnapshot:
        change_reason = cls._change_reason(change_reason)
        cls.sync_catalog(session)
        cls._validate_permission_codes(permission_codes)
        cls._assert_actor_can_delegate(
            session,
            actor_user_id=actor_user_id,
            permission_codes=permission_codes,
            platform_owner_user_ids=platform_owner_user_ids,
        )
        role = session.scalar(
            select(PlatformAdminRole)
            .where(PlatformAdminRole.id == role_id)
            .with_for_update()
        )
        if role is None:
            raise NotFoundError("Platform administrator role does not exist")
        if role.lock_version != expected_lock_version:
            raise ConflictError("Platform administrator role was changed elsewhere")
        before = cls.role_snapshot(session, role=role)
        role.display_name = display_name.strip()
        role.description = description.strip()
        role.active = active
        role.lock_version += 1
        role.updated_by_user_id = actor_user_id
        session.execute(
            delete(PlatformAdminRolePermission).where(
                PlatformAdminRolePermission.role_id == role.id
            )
        )
        for code in sorted(permission_codes):
            session.add(
                PlatformAdminRolePermission(role_id=role.id, permission_code=code)
            )
        session.flush()
        after = cls.role_snapshot(session, role=role)
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="platform_admin_access.role.replace",
            target_type="platform_admin_role",
            target_id=role.id,
            before_summary=cls.role_summary(before),
            after_summary={
                **cls.role_summary(after),
                "change_reason": change_reason,
            },
            request_id=request_id,
        )
        return after

    @classmethod
    def replace_user_access(
        cls,
        session: Session,
        *,
        actor_user_id: str,
        target_user_id: str,
        role_ids: set[str],
        permission_overrides: dict[str, PlatformAdminPermissionEffect],
        expected_lock_version: int,
        platform_owner_user_ids: set[str] | frozenset[str],
        request_id: str,
        change_reason: str,
    ) -> PlatformAdminAccessSnapshot:
        change_reason = cls._change_reason(change_reason)
        cls.sync_catalog(session)
        # Lock the stable user row first, making first-time profile creation safe.
        target = session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if target is None or not target.is_platform_admin:
            raise NotFoundError("Platform administrator does not exist")
        if target_user_id in platform_owner_user_ids:
            raise ConflictError(
                "Product owner access is immutable and controlled by the server allowlist"
            )
        cls._validate_permission_codes(set(permission_overrides))

        roles = list(
            session.scalars(
                select(PlatformAdminRole)
                .where(PlatformAdminRole.id.in_(role_ids))
                .order_by(PlatformAdminRole.id)
                .with_for_update()
            ).all()
        ) if role_ids else []
        if {role.id for role in roles} != role_ids:
            raise NotFoundError("One or more platform administrator roles do not exist")
        inactive = sorted(role.key for role in roles if not role.active)
        if inactive:
            raise ConflictError("Cannot assign inactive roles: " + ", ".join(inactive))

        delegated = {
            code
            for role in roles
            for code in cls._role_permissions(session, role.id)
        }
        delegated.update(
            code
            for code, effect in permission_overrides.items()
            if effect == PlatformAdminPermissionEffect.ALLOW
        )
        cls._assert_actor_can_delegate(
            session,
            actor_user_id=actor_user_id,
            permission_codes=delegated,
            platform_owner_user_ids=platform_owner_user_ids,
        )

        profile = session.scalar(
            select(PlatformAdminAccessProfile)
            .where(PlatformAdminAccessProfile.user_id == target_user_id)
            .with_for_update()
        )
        current_version = profile.lock_version if profile else 0
        if current_version != expected_lock_version:
            raise ConflictError(
                "Platform administrator access was changed elsewhere"
            )
        before = cls.access_snapshot(
            session,
            user_id=target_user_id,
            platform_owner_user_ids=platform_owner_user_ids,
        )
        if profile is None:
            profile = PlatformAdminAccessProfile(
                user_id=target_user_id,
                lock_version=1,
                updated_by_user_id=actor_user_id,
            )
            session.add(profile)
            session.flush()
        else:
            profile.lock_version += 1
            profile.updated_by_user_id = actor_user_id

        session.execute(
            delete(PlatformAdminRoleAssignment).where(
                PlatformAdminRoleAssignment.user_id == target_user_id
            )
        )
        session.execute(
            delete(PlatformAdminUserPermissionOverride).where(
                PlatformAdminUserPermissionOverride.user_id == target_user_id
            )
        )
        for role_id in sorted(role_ids):
            session.add(
                PlatformAdminRoleAssignment(
                    user_id=target_user_id,
                    role_id=role_id,
                    assigned_by_user_id=actor_user_id,
                )
            )
        for code, effect in sorted(permission_overrides.items()):
            session.add(
                PlatformAdminUserPermissionOverride(
                    user_id=target_user_id,
                    permission_code=code,
                    effect=effect,
                    changed_by_user_id=actor_user_id,
                )
            )
        session.flush()
        after = cls.access_snapshot(
            session,
            user_id=target_user_id,
            platform_owner_user_ids=platform_owner_user_ids,
        )
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="platform_admin_access.user.replace",
            target_type="platform_admin_user",
            target_id=target_user_id,
            before_summary=cls.access_summary(before),
            after_summary={
                **cls.access_summary(after),
                "change_reason": change_reason,
            },
            request_id=request_id,
        )
        return after

    @classmethod
    def list_administrators(
        cls,
        session: Session,
        *,
        platform_owner_user_ids: set[str] | frozenset[str],
    ) -> list[tuple[User, PlatformAdminAccessSnapshot, datetime | None]]:
        users = session.execute(
            select(User, PlatformAdminActivity.last_active_at)
            .outerjoin(
                PlatformAdminActivity,
                PlatformAdminActivity.user_id == User.id,
            )
            .where(User.is_platform_admin.is_(True))
            .order_by(User.display_name, User.id)
        ).all()
        return [
            (
                user,
                cls.access_snapshot(
                    session,
                    user_id=user.id,
                    platform_owner_user_ids=platform_owner_user_ids,
                ),
                last_active_at,
            )
            for user, last_active_at in users
        ]

    @classmethod
    def set_administrator_status(
        cls,
        session: Session,
        *,
        actor_user_id: str,
        target_user_id: str,
        enabled: bool,
        expected_is_platform_admin: bool,
        platform_owner_user_ids: set[str] | frozenset[str],
        request_id: str,
        change_reason: str,
    ) -> User:
        change_reason = cls._change_reason(change_reason)
        target = session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if target is None:
            raise NotFoundError("User does not exist")
        if target_user_id in platform_owner_user_ids:
            raise ConflictError(
                "Product owner administrator status is controlled by the server allowlist"
            )
        if target.is_platform_admin != expected_is_platform_admin:
            raise ConflictError("Platform administrator status was changed elsewhere")
        before = {"is_platform_admin": target.is_platform_admin}
        target.is_platform_admin = enabled
        if not enabled:
            # A later reactivation must start fail-closed; old grants cannot spring
            # back to life after an administrator was deliberately removed.
            session.execute(
                delete(PlatformAdminRoleAssignment).where(
                    PlatformAdminRoleAssignment.user_id == target_user_id
                )
            )
            session.execute(
                delete(PlatformAdminUserPermissionOverride).where(
                    PlatformAdminUserPermissionOverride.user_id == target_user_id
                )
            )
            session.execute(
                delete(PlatformAdminAccessProfile).where(
                    PlatformAdminAccessProfile.user_id == target_user_id
                )
            )
        session.flush()
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="platform_admin_access.user.status",
            target_type="platform_admin_user",
            target_id=target_user_id,
            before_summary=before,
            after_summary={
                "is_platform_admin": enabled,
                "change_reason": change_reason,
            },
            request_id=request_id,
        )
        return target

    @staticmethod
    def role_summary(snapshot: PlatformAdminRoleSnapshot) -> dict:
        return {
            "key": snapshot.key,
            "display_name": snapshot.display_name,
            "description": snapshot.description,
            "active": snapshot.active,
            "lock_version": snapshot.lock_version,
            "permission_codes": list(snapshot.permission_codes),
        }

    @staticmethod
    def access_summary(snapshot: PlatformAdminAccessSnapshot) -> dict:
        return {
            "is_platform_owner": snapshot.is_platform_owner,
            "lock_version": snapshot.lock_version,
            "role_ids": list(snapshot.role_ids),
            "permission_overrides": {
                code: effect.value
                for code, effect in sorted(snapshot.permission_overrides.items())
            },
            "effective_permissions": sorted(snapshot.effective_permissions),
            "snapshot": snapshot.snapshot,
        }
