from __future__ import annotations

import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import TimestampMixin, enum_kwargs, new_id


class PlatformAdminPermissionEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class PlatformAdminPermission(Base):
    """Server-owned permission catalog mirrored in the database for introspection."""

    __tablename__ = "platform_admin_permissions"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class PlatformAdminRole(TimestampMixin, Base):
    __tablename__ = "platform_admin_roles"
    __table_args__ = (
        CheckConstraint("lock_version >= 1", name="ck_platform_admin_role_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    updated_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class PlatformAdminRolePermission(Base):
    __tablename__ = "platform_admin_role_permissions"

    role_id: Mapped[str] = mapped_column(
        ForeignKey("platform_admin_roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("platform_admin_permissions.code", ondelete="RESTRICT"),
        primary_key=True,
    )


class PlatformAdminAccessProfile(TimestampMixin, Base):
    """One lockable snapshot for a non-owner administrator's access assignment."""

    __tablename__ = "platform_admin_access_profiles"
    __table_args__ = (
        CheckConstraint(
            "lock_version >= 1", name="ck_platform_admin_access_profile_version"
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class PlatformAdminRoleAssignment(TimestampMixin, Base):
    __tablename__ = "platform_admin_role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "role_id", name="uq_platform_admin_role_assignment"
        ),
        Index("ix_platform_admin_role_assignment_role", "role_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("platform_admin_access_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("platform_admin_roles.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class PlatformAdminUserPermissionOverride(TimestampMixin, Base):
    __tablename__ = "platform_admin_user_permission_overrides"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("platform_admin_access_profiles.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("platform_admin_permissions.code", ondelete="RESTRICT"),
        primary_key=True,
    )
    effect: Mapped[PlatformAdminPermissionEffect] = mapped_column(
        Enum(PlatformAdminPermissionEffect, **enum_kwargs), nullable=False
    )
    changed_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

