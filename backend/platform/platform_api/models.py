from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .relay_identity import (
    NEW_API_RELAY_BACKEND_ID,
    NEW_API_RELAY_CONTRACT_REVISION,
)


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_check_constraints(
    column_name: str,
    *,
    constraint_name: str,
    nullable: bool = False,
) -> tuple[CheckConstraint, CheckConstraint]:
    sqlite_expression = (
        f"length({column_name}) = 64 "
        f"AND lower({column_name}) = {column_name} "
        f"AND {column_name} NOT GLOB '*[^0-9a-f]*'"
    )
    postgres_expression = f"{column_name} ~ '^[0-9a-f]{{64}}$'"
    if nullable:
        sqlite_expression = f"{column_name} IS NULL OR ({sqlite_expression})"
        postgres_expression = f"{column_name} IS NULL OR ({postgres_expression})"
    return (
        CheckConstraint(
            sqlite_expression,
            name=constraint_name,
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            postgres_expression,
            name=constraint_name,
        ).ddl_if(dialect="postgresql"),
    )


class CompanyStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class MembershipStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class CompanyInvitationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PermissionEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class TaskStatus(str, enum.Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InputAssetStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class LedgerKind(str, enum.Enum):
    RECHARGE = "recharge"
    RESERVE = "reserve"
    SETTLE = "settle"
    RELEASE = "release"


class RelayOutboxStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    SENT = "sent"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    PERMANENTLY_FAILED = "permanently_failed"
    CANCELLED = "cancelled"


class ResourceKind(str, enum.Enum):
    FEATURE = "feature"
    AGENT = "agent"
    EXTERNAL_API = "external_api"


class ChannelType(str, enum.Enum):
    REVERSE = "reverse"
    THIRD_PARTY_API = "third_party_api"
    OFFICIAL = "official"


class RelayTaskStage(str, enum.Enum):
    QUEUED = "queued"
    SUBMITTING = "submitting"
    SUBMISSION_UNKNOWN = "submission_unknown"
    PROVIDER_PROCESSING = "provider_processing"
    ARTIFACT_TRANSFERRING = "artifact_transferring"
    ARTIFACT_STORED = "artifact_stored"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChannelCostSource(str, enum.Enum):
    PLATFORM_ADMIN = "platform_admin"
    RELAY = "relay"


class DownloadCompletionSource(str, enum.Enum):
    PLATFORM_PROXY = "platform_proxy"
    OBS_ACCESS_LOG = "obs_access_log"
    EDGE_GATEWAY = "edge_gateway"


class DownloadGatewayRegistrationStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    UNKNOWN = "unknown"
    RECONCILED_EXPIRED = "reconciled_expired"
    REGISTERED = "registered"
    ATTACHED = "attached"
    DEAD = "dead"


class PublisherConnectionStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REQUIRES_REAUTH = "requires_reauth"


class PublicationJobStatus(str, enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    SUBMITTING = "submitting"
    PUBLISHED = "published"
    FAILED = "failed"
    SUBMISSION_UNKNOWN = "submission_unknown"
    REQUIRES_REAUTH = "requires_reauth"
    CANCELLED = "cancelled"


class PublicationAttemptStatus(str, enum.Enum):
    SUBMITTING = "submitting"
    PUBLISHED = "published"
    FAILED = "failed"
    SUBMISSION_UNKNOWN = "submission_unknown"
    REQUIRES_REAUTH = "requires_reauth"


class AuditOutcome(str, enum.Enum):
    """Durable execution outcome attached to an immutable audit record."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


enum_kwargs: dict[str, Any] = {"native_enum": False, "validate_strings": True}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[CompanyStatus] = mapped_column(
        Enum(CompanyStatus, **enum_kwargs), default=CompanyStatus.ACTIVE, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("auth_version >= 1", name="ck_users_auth_version"),
        CheckConstraint(
            "(status = 'DEACTIVATED' AND deactivated_at IS NOT NULL) OR "
            "(status <> 'DEACTIVATED' AND deactivated_at IS NULL)",
            name="ck_users_status_deactivated",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, **enum_kwargs), default=UserStatus.ACTIVE, nullable=False
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auth_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Index("uq_users_email_casefold", func.lower(User.email), unique=True)


class ExternalIdentity(TimestampMixin, Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_external_identity_issuer_subject"),
        Index("ix_external_identity_user", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email_at_link: Mapped[str] = mapped_column(String(320), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        *_sha256_check_constraints(
            "token_digest", constraint_name="ck_auth_session_token_digest_sha256"
        ),
        *_sha256_check_constraints(
            "csrf_digest", constraint_name="ck_auth_session_csrf_digest_sha256"
        ),
        CheckConstraint("auth_version >= 1", name="ck_auth_session_auth_version"),
        Index("ix_auth_session_user_expiry", "user_id", "expires_at"),
        Index("ix_auth_session_active", "revoked_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_identity_id: Mapped[str] = mapped_column(
        ForeignKey("external_identities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    amr: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    auth_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(512), default="", nullable=False)


class OidcLoginTransaction(Base):
    __tablename__ = "oidc_login_transactions"
    __table_args__ = (
        *_sha256_check_constraints(
            "state_digest", constraint_name="ck_oidc_login_state_digest_sha256"
        ),
        *_sha256_check_constraints(
            "ip_hash", constraint_name="ck_oidc_login_ip_hash_sha256"
        ),
        Index("ix_oidc_login_expiry", "expires_at", "consumed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    state_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    nonce: Mapped[str] = mapped_column(String(160), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(160), nullable=False)
    return_to: Mapped[str] = mapped_column(String(2048), nullable=False)
    prompt: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PlatformAdminActivity(Base):
    """Mutable authorized-activity evidence kept outside core user identity."""

    __tablename__ = "platform_admin_activity"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PersonalWorkspace(TimestampMixin, Base):
    __tablename__ = "personal_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CompanyMembership(TimestampMixin, Base):
    __tablename__ = "company_memberships"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", name="uq_membership_company_user"),
        Index("ix_membership_company_status", "company_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, **enum_kwargs), default=MembershipStatus.ACTIVE, nullable=False
    )


class AccountSecurityEvent(Base):
    __tablename__ = "account_security_events"
    __table_args__ = (
        *_sha256_check_constraints(
            "subject_hash",
            constraint_name="ck_account_security_event_subject_hash_sha256",
            nullable=True,
        ),
        Index("ix_account_security_event_user_created", "user_id", "created_at"),
        Index("ix_account_security_event_type_created", "event_type", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    outcome: Mapped[AuditOutcome] = mapped_column(
        Enum(AuditOutcome, **enum_kwargs),
        default=AuditOutcome.SUCCEEDED,
        nullable=False,
    )
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CompanyInvitation(TimestampMixin, Base):
    __tablename__ = "company_invitations"
    __table_args__ = (
        *_sha256_check_constraints(
            "token_digest", constraint_name="ck_company_invitation_token_digest_sha256"
        ),
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_company_invitation_request_fingerprint_sha256",
        ),
        CheckConstraint(
            "primary_role IN ('operator', 'team_lead')",
            name="ck_company_invitation_primary_role",
        ),
        CheckConstraint(
            "(status = 'ACCEPTED' AND accepted_by_user_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'REVOKED' AND accepted_by_user_id IS NULL "
            "AND accepted_at IS NULL AND revoked_at IS NOT NULL) OR "
            "(status IN ('PENDING', 'EXPIRED') AND accepted_by_user_id IS NULL "
            "AND accepted_at IS NULL AND revoked_at IS NULL)",
            name="ck_company_invitation_status_evidence",
        ),
        UniqueConstraint(
            "company_id", "idempotency_key", name="uq_company_invitation_idempotency"
        ),
        Index("ix_company_invitation_company_status", "company_id", "status"),
        Index("ix_company_invitation_email_status", "email", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    primary_role: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[CompanyInvitationStatus] = mapped_column(
        Enum(CompanyInvitationStatus, **enum_kwargs),
        default=CompanyInvitationStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    accepted_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(String(240), nullable=False)


class Role(TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_role_company_name"),
        UniqueConstraint(
            "company_id", "system_key", name="uq_role_company_system_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    system_key: Mapped[str | None] = mapped_column(String(40), nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True
    )


class MembershipRole(Base):
    __tablename__ = "membership_roles"

    membership_id: Mapped[str] = mapped_column(
        ForeignKey("company_memberships.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )


class MemberPermissionOverride(TimestampMixin, Base):
    __tablename__ = "member_permission_overrides"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "permission_code", name="uq_member_permission_override"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    membership_id: Mapped[str] = mapped_column(
        ForeignKey("company_memberships.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code", ondelete="CASCADE"), nullable=False
    )
    effect: Mapped[PermissionEffect] = mapped_column(
        Enum(PermissionEffect, **enum_kwargs), nullable=False
    )


class ModelDefinition(TimestampMixin, Base):
    __tablename__ = "model_definitions"
    __table_args__ = (
        CheckConstraint(
            "billing_mode IN ('per_second', 'per_item')",
            name="ck_model_billing_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(80), nullable=False)
    billing_mode: Mapped[str] = mapped_column(
        String(24), default="per_second", nullable=False
    )
    capability_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    relay_capability_revision: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    relay_capability_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=True
    )


class ModelCapability(Base):
    __tablename__ = "model_capabilities"
    __table_args__ = (
        UniqueConstraint("model_id", "capability_key", name="uq_model_capability_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability_key: Mapped[str] = mapped_column(String(80), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CompanyModelGrant(TimestampMixin, Base):
    __tablename__ = "company_model_grants"
    __table_args__ = (
        UniqueConstraint("company_id", "model_id", name="uq_company_model_grant"),
        CheckConstraint(
            "price_per_second_cents IS NULL OR price_per_second_cents > 0",
            name="ck_grant_second_price_positive",
        ),
        CheckConstraint(
            "price_per_item_cents IS NULL OR price_per_item_cents > 0",
            name="ck_grant_item_price_positive",
        ),
        CheckConstraint(
            "(price_per_second_cents IS NOT NULL AND price_per_item_cents IS NULL) "
            "OR (price_per_second_cents IS NULL AND price_per_item_cents IS NOT NULL)",
            name="ck_grant_exactly_one_price",
        ),
        CheckConstraint(
            "call_quota IS NULL OR call_quota > 0",
            name="ck_model_grant_call_quota_positive",
        ),
        CheckConstraint(
            "concurrency_limit IS NULL OR concurrency_limit > 0",
            name="ck_model_grant_concurrency_positive",
        ),
        CheckConstraint(
            "effective_at IS NULL OR expires_at IS NULL OR expires_at > effective_at",
            name="ck_model_grant_schedule_order",
        ),
        Index(
            "ix_company_model_grant_schedule",
            "company_id",
            "enabled",
            "effective_at",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    price_per_second_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_per_item_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    config_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    call_quota: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PersonalRetailModelGrant(TimestampMixin, Base):
    """Server-owned retail model offer for individual workspaces.

    Retail points are intentionally separate from company contract prices in
    cents.  Missing rows fail closed: a catalog model is not automatically
    available to individual users merely because a company can use it.
    """

    __tablename__ = "personal_retail_model_grants"
    __table_args__ = (
        UniqueConstraint("model_id", name="uq_personal_retail_model_grant"),
        CheckConstraint(
            "price_per_second_points IS NULL OR price_per_second_points > 0",
            name="ck_personal_retail_second_price_positive",
        ),
        CheckConstraint(
            "price_per_item_points IS NULL OR price_per_item_points > 0",
            name="ck_personal_retail_item_price_positive",
        ),
        CheckConstraint(
            "(price_per_second_points IS NOT NULL AND price_per_item_points IS NULL) "
            "OR (price_per_second_points IS NULL AND price_per_item_points IS NOT NULL)",
            name="ck_personal_retail_exactly_one_price",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    price_per_second_points: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_per_item_points: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    config_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ResourceDefinition(TimestampMixin, Base):
    __tablename__ = "resource_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    kind: Mapped[ResourceKind] = mapped_column(
        Enum(ResourceKind, **enum_kwargs), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CompanyResourceGrant(TimestampMixin, Base):
    __tablename__ = "company_resource_grants"
    __table_args__ = (
        UniqueConstraint("company_id", "resource_id", name="uq_company_resource_grant"),
        CheckConstraint(
            "call_quota IS NULL OR call_quota > 0",
            name="ck_resource_grant_call_quota_positive",
        ),
        CheckConstraint(
            "concurrency_limit IS NULL OR concurrency_limit > 0",
            name="ck_resource_grant_concurrency_positive",
        ),
        CheckConstraint(
            "effective_at IS NULL OR expires_at IS NULL OR expires_at > effective_at",
            name="ck_resource_grant_schedule_order",
        ),
        Index(
            "ix_company_resource_grant_schedule",
            "company_id",
            "enabled",
            "effective_at",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resource_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    call_quota: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WalletAccount(TimestampMixin, Base):
    __tablename__ = "wallet_accounts"
    __table_args__ = (
        CheckConstraint("available_cents >= 0", name="ck_wallet_available_nonnegative"),
        CheckConstraint("reserved_cents >= 0", name="ck_wallet_reserved_nonnegative"),
    )

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    available_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class PersonalWalletAccount(TimestampMixin, Base):
    __tablename__ = "personal_wallet_accounts"
    __table_args__ = (
        CheckConstraint(
            "available_points >= 0", name="ck_personal_wallet_available_nonnegative"
        ),
        CheckConstraint(
            "reserved_points >= 0", name="ck_personal_wallet_reserved_nonnegative"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    available_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )


class GenerationTask(TimestampMixin, Base):
    __tablename__ = "generation_tasks"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_task_company_idempotency",
        ),
        UniqueConstraint(
            "personal_workspace_id",
            "idempotency_key",
            name="uq_task_personal_idempotency",
        ),
        Index("ix_generation_task_company_created", "company_id", "created_at"),
        Index(
            "ix_generation_task_personal_created",
            "personal_workspace_id",
            "created_at",
        ),
        Index(
            "ix_generation_task_timeout_scan",
            "status",
            "timeout_checked_at",
            "created_at",
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND quote_cents > 0 AND quote_points IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND quote_cents IS NULL AND quote_points > 0)",
            name="ck_task_scope_quote",
        ),
        CheckConstraint("reserved_cents >= 0", name="ck_task_reserved_nonnegative"),
        CheckConstraint("reserved_points >= 0", name="ck_task_points_reserved_nonnegative"),
        CheckConstraint(
            "actual_cost_cents IS NULL OR actual_cost_cents >= 0",
            name="ck_task_actual_cost_nonnegative",
        ),
        CheckConstraint(
            "actual_cost_points IS NULL OR actual_cost_points >= 0",
            name="ck_task_actual_points_nonnegative",
        ),
        CheckConstraint(
            "length(relay_backend_id) > 0",
            name="ck_task_relay_backend_id_nonempty",
        ),
        CheckConstraint(
            "length(relay_contract_revision) > 0",
            name="ck_task_relay_contract_revision_nonempty",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_definitions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, **enum_kwargs), default=TaskStatus.DRAFT, nullable=False
    )
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    quote_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_points: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    capability_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    reserved_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    actual_cost_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actual_cost_points: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_task_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    relay_backend_id: Mapped[str] = mapped_column(
        String(64),
        default=NEW_API_RELAY_BACKEND_ID,
        server_default=NEW_API_RELAY_BACKEND_ID,
        nullable=False,
    )
    relay_contract_revision: Mapped[str] = mapped_column(
        String(64),
        default=NEW_API_RELAY_CONTRACT_REVISION,
        server_default=NEW_API_RELAY_CONTRACT_REVISION,
        nullable=False,
    )
    relay_job_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, unique=True
    )
    output_artifacts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    relay_error_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    timeout_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

class TaskArtifact(Base):
    __tablename__ = "task_artifacts"
    __table_args__ = (
        UniqueConstraint("task_id", "asset_id", name="uq_task_artifact_asset"),
        UniqueConstraint("task_id", "position", name="uq_task_artifact_position"),
        Index("ix_task_artifact_company_created", "company_id", "created_at"),
        Index(
            "ix_task_artifact_personal_created",
            "personal_workspace_id",
            "created_at",
        ),
        Index("ix_task_artifact_task_created", "task_id", "created_at"),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_task_artifact_scope",
        ),
        CheckConstraint(
            "media_type IN ('image', 'video')",
            name="ck_task_artifact_media_type",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_task_artifact_size_nonnegative"),
        CheckConstraint("size_bytes > 0", name="ck_task_artifact_size_positive"),
        CheckConstraint("position >= 0", name="ck_task_artifact_position_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False,
        index=True,
    )
    asset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(TaskArtifact, "before_update")
def _prevent_task_artifact_update(*_) -> None:
    raise RuntimeError("task artifacts are immutable")


@event.listens_for(TaskArtifact, "before_delete")
def _prevent_task_artifact_delete(*_) -> None:
    raise RuntimeError("task artifacts are immutable")


class PublisherConnection(TimestampMixin, Base):
    __tablename__ = "publisher_connections"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "provider",
            "external_account_id",
            name="uq_publisher_connection_account",
        ),
        Index(
            "ix_publisher_connection_company_status_created",
            "company_id",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "length(provider) > 0 AND provider = lower(provider)",
            name="ck_publisher_connection_provider_normalized",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[PublisherConnectionStatus] = mapped_column(
        Enum(PublisherConnectionStatus, **enum_kwargs),
        default=PublisherConnectionStatus.ACTIVE,
        nullable=False,
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PublisherOAuthSession(Base):
    """One-time, tenant-bound OAuth state for publisher account linking.

    Only a SHA-256 digest of the browser-visible state is persisted.  The
    provider authorization code and access token are never stored here.
    """

    __tablename__ = "publisher_oauth_sessions"
    __table_args__ = (
        UniqueConstraint(
            "state_sha256", name="uq_publisher_oauth_session_state_sha256"
        ),
        Index(
            "ix_publisher_oauth_session_company_created",
            "company_id",
            "created_at",
        ),
        CheckConstraint(
            "length(state_sha256) = 64 AND state_sha256 = lower(state_sha256)",
            name="ck_publisher_oauth_session_state_sha256",
        ),
        CheckConstraint(
            "length(provider) > 0 AND provider = lower(provider)",
            name="ck_publisher_oauth_session_provider_normalized",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PublicationJob(TimestampMixin, Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_publication_job_company_idempotency",
        ),
        UniqueConstraint(
            "connection_id",
            "external_post_id",
            name="uq_publication_job_connection_external_post",
        ),
        Index(
            "ix_publication_job_company_status_created",
            "company_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_publication_job_dispatch",
            "status",
            "next_attempt_at",
            "scheduled_at",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="ck_publication_job_attempt_count"
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_publication_job_lease_complete",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    task_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("task_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("publisher_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PublicationJobStatus] = mapped_column(
        Enum(PublicationJobStatus, **enum_kwargs),
        default=PublicationJobStatus.PENDING_APPROVAL,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    caption: Mapped[str] = mapped_column(Text, default="", nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Shanghai", nullable=False
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_post_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    external_post_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submit_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PublicationAttempt(Base):
    __tablename__ = "publication_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_number", name="uq_publication_attempt_number"
        ),
        Index(
            "ix_publication_attempt_company_created",
            "company_id",
            "created_at",
        ),
        CheckConstraint(
            "attempt_number > 0", name="ck_publication_attempt_number_positive"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("publication_jobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationAttemptStatus] = mapped_column(
        Enum(PublicationAttemptStatus, **enum_kwargs), nullable=False
    )
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    external_post_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    external_post_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class InputAsset(TimestampMixin, Base):
    __tablename__ = "input_assets"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_input_asset_object_key"),
        UniqueConstraint(
            "company_id",
            "uploaded_by_user_id",
            "idempotency_key",
            name="uq_input_asset_uploader_idempotency",
        ),
        Index(
            "ix_input_asset_company_status_created",
            "company_id",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "source_task_artifact_id IS NULL OR idempotency_key IS NOT NULL",
            name="ck_input_asset_promotion_has_idempotency",
        ),
        CheckConstraint("size_bytes > 0", name="ck_input_asset_size_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_task_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_artifacts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[InputAssetStatus] = mapped_column(
        Enum(InputAssetStatus, **enum_kwargs),
        default=InputAssetStatus.ACTIVE,
        nullable=False,
    )


class TaskInputAsset(Base):
    __tablename__ = "task_input_assets"
    __table_args__ = (
        UniqueConstraint("task_id", "position", name="uq_task_input_position"),
        Index("ix_task_input_asset_asset", "asset_id"),
        CheckConstraint("position >= 0", name="ck_task_input_position_nonnegative"),
    )

    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("input_assets.id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class RelaySubmissionOutbox(TimestampMixin, Base):
    __tablename__ = "relay_submission_outbox"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_relay_outbox_task"),
        Index("ix_relay_outbox_dispatch", "status", "next_attempt_at", "created_at"),
        CheckConstraint("attempt_count >= 0", name="ck_relay_attempt_nonnegative"),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_relay_outbox_scope",
        ),
        CheckConstraint(
            "length(relay_backend_id) > 0",
            name="ck_relay_outbox_backend_id_nonempty",
        ),
        CheckConstraint(
            "length(relay_contract_revision) > 0",
            name="ck_relay_outbox_contract_revision_nonempty",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RelayOutboxStatus] = mapped_column(
        Enum(RelayOutboxStatus, **enum_kwargs),
        default=RelayOutboxStatus.PENDING,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    relay_backend_id: Mapped[str] = mapped_column(
        String(64),
        default=NEW_API_RELAY_BACKEND_ID,
        server_default=NEW_API_RELAY_BACKEND_ID,
        nullable=False,
    )
    relay_contract_revision: Mapped[str] = mapped_column(
        String(64),
        default=NEW_API_RELAY_CONTRACT_REVISION,
        server_default=NEW_API_RELAY_CONTRACT_REVISION,
        nullable=False,
    )
    relay_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    materialized_relay_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    relay_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    relay_submit_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submission_outcome_uncertain_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RelayCallbackEvent(Base):
    __tablename__ = "relay_callback_events"
    __table_args__ = (
        Index(
            "ix_relay_callback_event_task_received",
            "task_id",
            "received_at",
        ),
        Index(
            "ix_relay_callback_event_company_received",
            "company_id",
            "received_at",
        ),
        Index(
            "ix_relay_callback_event_personal_received",
            "personal_workspace_id",
            "received_at",
        ),
        Index(
            "ix_relay_callback_event_status_received",
            "relay_status",
            "received_at",
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_relay_callback_event_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"), nullable=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    relay_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relay_status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(RelayCallbackEvent, "before_update")
def _prevent_relay_callback_event_update(*_) -> None:
    raise RuntimeError("relay callback events are immutable")


@event.listens_for(RelayCallbackEvent, "before_delete")
def _prevent_relay_callback_event_delete(*_) -> None:
    raise RuntimeError("relay callback events are immutable")


class RelayTaskStageEvent(Base):
    __tablename__ = "relay_task_stage_events"
    __table_args__ = (
        Index("ix_relay_task_stage_task_occurred", "task_id", "occurred_at"),
        Index(
            "ix_relay_task_stage_company_occurred",
            "company_id",
            "occurred_at",
        ),
        Index("ix_relay_task_stage_stage_occurred", "stage", "occurred_at"),
        CheckConstraint("schema_version = 1", name="ck_relay_task_stage_schema_v1"),
        CheckConstraint(
            "duration_ms IS NULL OR (duration_ms >= 0 AND "
            "duration_ms <= 9223372036854775807)",
            name="ck_relay_task_stage_duration_range",
        ),
        CheckConstraint(
            "route_id IS NULL OR route_id > 0",
            name="ck_relay_task_stage_route_positive",
        ),
        CheckConstraint(
            "(channel_key = '' AND channel_type IS NULL) OR "
            "(channel_key <> '' AND channel_type IS NOT NULL)",
            name="ck_relay_task_stage_channel_binding",
        ),
        *_sha256_check_constraints(
            "payload_sha256",
            constraint_name="ck_relay_task_stage_payload_sha256",
        ),
    )

    # The signed delivery event UUID is the immutable idempotency identity.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    relay_job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    stage: Mapped[RelayTaskStage] = mapped_column(
        Enum(RelayTaskStage, **enum_kwargs), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    channel_key: Mapped[str] = mapped_column(
        String(120), default="", nullable=False
    )
    channel_type: Mapped[ChannelType | None] = mapped_column(
        Enum(ChannelType, **enum_kwargs), nullable=True
    )
    route_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_task_id: Mapped[str] = mapped_column(
        String(191), default="", nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    delivery_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(RelayTaskStageEvent, "before_update")
def _prevent_relay_task_stage_event_update(*_) -> None:
    raise RuntimeError("relay task stage events are immutable")


@event.listens_for(RelayTaskStageEvent, "before_delete")
def _prevent_relay_task_stage_event_delete(*_) -> None:
    raise RuntimeError("relay task stage events are immutable")


class RelayProviderAlertEvent(Base):
    __tablename__ = "relay_provider_alert_events"
    __table_args__ = (
        Index(
            "ix_relay_provider_alert_provider_occurred",
            "provider_name",
            "occurred_at",
        ),
        Index(
            "ix_relay_provider_alert_kind_state_occurred",
            "incident_kind",
            "incident_state",
            "occurred_at",
        ),
        CheckConstraint(
            "schema_version = 1", name="ck_relay_provider_alert_schema_v1"
        ),
        CheckConstraint(
            "incident_kind IN ('success_rate_drop', "
            "'widespread_route_failure', 'batch_account_invalidation')",
            name="ck_relay_provider_alert_kind",
        ),
        CheckConstraint(
            "incident_state IN ('triggered', 'recovered')",
            name="ck_relay_provider_alert_state",
        ),
        CheckConstraint(
            "event_type = 'provider_monitor.' || incident_kind || '.' || "
            "incident_state",
            name="ck_relay_provider_alert_event_type",
        ),
        CheckConstraint(
            "generation > 0 AND sample_size >= 0 AND success_count >= 0 AND "
            "success_count <= sample_size AND affected_routes >= 0 AND "
            "total_routes >= 0 AND affected_routes <= total_routes AND "
            "success_rate_basis_points >= 0 AND "
            "success_rate_basis_points <= 10000",
            name="ck_relay_provider_alert_metrics",
        ),
        *_sha256_check_constraints(
            "payload_sha256",
            constraint_name="ck_relay_provider_alert_payload_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(192), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    incident_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    incident_state: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False)
    affected_routes: Mapped[int] = mapped_column(Integer, nullable=False)
    total_routes: Mapped[int] = mapped_column(Integer, nullable=False)
    success_rate_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(RelayProviderAlertEvent, "before_update")
def _prevent_relay_provider_alert_event_update(*_) -> None:
    raise RuntimeError("relay provider alert events are immutable")


@event.listens_for(RelayProviderAlertEvent, "before_delete")
def _prevent_relay_provider_alert_event_delete(*_) -> None:
    raise RuntimeError("relay provider alert events are immutable")


class RelayOperationsSnapshot(Base):
    __tablename__ = "relay_operations_snapshots"
    __table_args__ = (
        Index("ix_relay_operations_snapshot_observed", "observed_at", "id"),
        Index("ix_relay_operations_snapshot_expiry", "expires_at", "observed_at"),
        CheckConstraint("schema_version = 1", name="ck_relay_operations_schema_v1"),
        CheckConstraint(
            "window_started_at < observed_at AND expires_at > observed_at",
            name="ck_relay_operations_time_order",
        ),
        CheckConstraint(
            "monitor_last_completed_at IS NULL OR "
            "monitor_last_completed_at <= observed_at",
            name="ck_relay_operations_monitor_time",
        ),
        CheckConstraint(
            "account_total >= 0 AND account_active >= 0 AND "
            "account_cooling >= 0 AND account_invalid >= 0 AND "
            "account_busy >= 0 AND account_rate_limited >= 0 AND "
            "account_active_tasks >= 0 AND account_task_capacity >= 0",
            name="ck_relay_operations_account_counts",
        ),
        CheckConstraint(
            "task_queued >= 0 AND task_submitting >= 0 AND "
            "task_submission_unknown >= 0 AND task_provider_processing >= 0 AND "
            "task_artifact_transferring >= 0 AND task_succeeded >= 0 AND "
            "task_failed >= 0 AND task_cancelled >= 0 AND "
            "task_rate_limited_count >= 0 AND task_failover_count >= 0",
            name="ck_relay_operations_task_counts",
        ),
        CheckConstraint(
            "delivery_pending_alert_count >= 0 AND "
            "delivery_dead_alert_count >= 0 AND "
            "delivery_pending_cost_count >= 0 AND "
            "delivery_dead_cost_count >= 0 AND "
            "delivery_pending_task_stage_count >= 0 AND "
            "delivery_dead_task_stage_count >= 0 AND "
            "delivery_pending_snapshot_count >= 0 AND "
            "delivery_dead_snapshot_count >= 0",
            name="ck_relay_operations_delivery_counts",
        ),
        CheckConstraint(
            "cost_successful_jobs >= 0 AND cost_explicit_jobs >= 0 AND "
            "cost_delivered_jobs >= 0 AND cost_incomplete_jobs >= 0 AND "
            "cost_native_reconciliation_jobs >= 0",
            name="ck_relay_operations_cost_counts",
        ),
        *_sha256_check_constraints(
            "payload_sha256",
            constraint_name="ck_relay_operations_payload_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    monitor_fresh: Mapped[bool] = mapped_column(Boolean, nullable=False)
    monitor_last_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    account_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_active: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_cooling: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_invalid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_busy: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_rate_limited: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_active_tasks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_task_capacity: Mapped[int] = mapped_column(BigInteger, nullable=False)

    task_queued: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_submitting: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_submission_unknown: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_provider_processing: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_artifact_transferring: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_succeeded: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_failed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_cancelled: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_rate_limited_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_failover_count: Mapped[int] = mapped_column(BigInteger, nullable=False)

    delivery_pending_alert_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_dead_alert_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_oldest_pending_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_pending_cost_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_dead_cost_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_pending_task_stage_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    delivery_dead_task_stage_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    delivery_pending_snapshot_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    delivery_dead_snapshot_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )

    cost_successful_jobs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_explicit_jobs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_delivered_jobs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_incomplete_jobs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_native_reconciliation_jobs: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    cost_reconciliation_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )

    delivery_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RelayRouteOperationsSnapshot(Base):
    __tablename__ = "relay_route_operations_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "route_id", name="uq_relay_route_snapshot"),
        Index(
            "ix_relay_route_operations_channel_snapshot",
            "channel_key",
            "snapshot_id",
        ),
        CheckConstraint("route_id > 0", name="ck_relay_route_id_positive"),
        CheckConstraint(
            "health_status IN ('unknown', 'healthy', 'failed', 'invalidated', "
            "'cooling', 'disabled')",
            name="ck_relay_route_health_status",
        ),
        CheckConstraint(
            "rpm_limit >= 0 AND rpm_used >= 0 AND active_task_count >= 0 AND "
            "task_capacity >= 0 AND cooling_account_count >= 0 AND "
            "invalid_account_count >= 0 AND busy_account_count >= 0 AND "
            "rate_limited_account_count >= 0 AND successful_task_count >= 0 AND "
            "failed_task_count >= 0",
            name="ck_relay_route_metric_counts",
        ),
        CheckConstraint(
            "latency_p50_ms IS NULL OR latency_p50_ms >= 0",
            name="ck_relay_route_latency_p50",
        ),
        CheckConstraint(
            "latency_p95_ms IS NULL OR latency_p95_ms >= 0",
            name="ck_relay_route_latency_p95",
        ),
        CheckConstraint(
            "latency_p50_ms IS NULL OR latency_p95_ms IS NULL OR "
            "latency_p95_ms >= latency_p50_ms",
            name="ck_relay_route_latency_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("relay_operations_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    route_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, **enum_kwargs), nullable=False
    )
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    production_ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    health_status: Mapped[str] = mapped_column(String(24), nullable=False)
    failure_code: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    last_probe_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rpm_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rpm_used: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active_task_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_capacity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cooling_account_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invalid_account_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    busy_account_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rate_limited_account_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    successful_task_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failed_task_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latency_p50_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_p95_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


def _prevent_relay_operations_mutation(*_) -> None:
    raise RuntimeError("relay operations telemetry is immutable")


for _immutable_telemetry_model in (
    RelayOperationsSnapshot,
    RelayRouteOperationsSnapshot,
):
    event.listen(
        _immutable_telemetry_model,
        "before_update",
        _prevent_relay_operations_mutation,
    )
    event.listen(
        _immutable_telemetry_model,
        "before_delete",
        _prevent_relay_operations_mutation,
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_ledger_idempotency"),
        Index("ix_ledger_company_created", "company_id", "created_at"),
        Index(
            "ix_ledger_company_kind_created",
            "company_id",
            "kind",
            "created_at",
        ),
        Index("ix_ledger_kind_created", "kind", "created_at", "id"),
        CheckConstraint("amount_cents >= 0", name="ck_ledger_amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[LedgerKind] = mapped_column(Enum(LedgerKind, **enum_kwargs), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_delta_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_delta_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    note: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(LedgerEntry, "before_update")
def _prevent_ledger_entry_update(*_) -> None:
    raise RuntimeError("ledger entries are immutable")


@event.listens_for(LedgerEntry, "before_delete")
def _prevent_ledger_entry_delete(*_) -> None:
    raise RuntimeError("ledger entries are immutable")


class PersonalLedgerEntry(Base):
    __tablename__ = "personal_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_personal_ledger_idempotency",
        ),
        Index(
            "ix_personal_ledger_workspace_created",
            "workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "amount_points >= 0", name="ck_personal_ledger_amount_nonnegative"
        ),
        CheckConstraint(
            "kind IN ('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE')",
            name="ck_personal_ledger_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[LedgerKind] = mapped_column(
        Enum(LedgerKind, **enum_kwargs), nullable=False
    )
    amount_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_delta_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_delta_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    note: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(PersonalLedgerEntry, "before_update")
def _prevent_personal_ledger_entry_update(*_) -> None:
    raise RuntimeError("personal ledger entries are immutable")


@event.listens_for(PersonalLedgerEntry, "before_delete")
def _prevent_personal_ledger_entry_delete(*_) -> None:
    raise RuntimeError("personal ledger entries are immutable")


class ChannelCostEntry(Base):
    __tablename__ = "channel_cost_entries"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_channel_cost_idempotency"
        ),
        Index(
            "uq_channel_cost_relay_event_id",
            "relay_event_id",
            unique=True,
        ),
        Index("ix_channel_cost_occurred", "occurred_at", "id"),
        Index(
            "ix_channel_cost_channel_occurred",
            "channel_type",
            "channel_key",
            "occurred_at",
        ),
        Index(
            "ix_channel_cost_company_occurred",
            "company_id",
            "occurred_at",
        ),
        Index(
            "ix_channel_cost_personal_occurred",
            "personal_workspace_id",
            "occurred_at",
        ),
        CheckConstraint(
            "amount_cents >= -9000000000000000 "
            "AND amount_cents <= 9000000000000000",
            name="ck_channel_cost_amount_range",
        ),
        CheckConstraint(
            "(relay_event_id IS NULL "
            "AND relay_event_timestamp IS NULL "
            "AND relay_payload_sha256 IS NULL) "
            "OR (relay_event_id IS NOT NULL "
            "AND relay_event_timestamp IS NOT NULL "
            "AND relay_payload_sha256 IS NOT NULL)",
            name="ck_channel_cost_relay_evidence_complete",
        ),
        CheckConstraint(
            "relay_event_id IS NULL OR ("
            "length(relay_event_id) = 36 "
            "AND substr(relay_event_id, 9, 1) = '-' "
            "AND substr(relay_event_id, 14, 1) = '-' "
            "AND substr(relay_event_id, 19, 1) = '-' "
            "AND substr(relay_event_id, 24, 1) = '-' "
            "AND lower(relay_event_id) = relay_event_id "
            "AND replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(relay_event_id, '0', ''), "
            "'1', ''), '2', ''), '3', ''), '4', ''), '5', ''), "
            "'6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), "
            "'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', ''), "
            "'-', '') = '')",
            name="ck_channel_cost_relay_event_id_format",
        ),
        CheckConstraint(
            "relay_payload_sha256 IS NULL OR ("
            "length(relay_payload_sha256) = 64 "
            "AND lower(relay_payload_sha256) = relay_payload_sha256 "
            "AND replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(relay_payload_sha256, '0', ''), "
            "'1', ''), '2', ''), '3', ''), '4', ''), '5', ''), "
            "'6', ''), '7', ''), '8', ''), '9', ''), 'a', ''), "
            "'b', ''), 'c', ''), 'd', ''), 'e', ''), 'f', '') = '')",
            name="ck_channel_cost_relay_payload_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, **enum_kwargs), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    external_reference: Mapped[str] = mapped_column(String(240), nullable=False)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    relay_job_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    relay_event_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    relay_event_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    relay_payload_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    note: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    evidence_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    evidence_reference: Mapped[str | None] = mapped_column(
        String(240), nullable=True
    )
    source_document_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source: Mapped[ChannelCostSource] = mapped_column(
        Enum(ChannelCostSource, **enum_kwargs), nullable=False
    )
    recorded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(ChannelCostEntry, "before_update")
def _prevent_channel_cost_entry_update(*_) -> None:
    raise RuntimeError("channel cost entries are immutable")


@event.listens_for(ChannelCostEntry, "before_delete")
def _prevent_channel_cost_entry_delete(*_) -> None:
    raise RuntimeError("channel cost entries are immutable")


class TaskTimeoutEvent(Base):
    __tablename__ = "task_timeout_events"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_task_timeout_event_task"),
        Index("ix_task_timeout_event_created", "created_at"),
        Index("ix_task_timeout_event_company_created", "company_id", "created_at"),
        Index(
            "ix_task_timeout_event_personal_created",
            "personal_workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "released_cents >= 0", name="ck_task_timeout_released_nonnegative"
        ),
        CheckConstraint(
            "released_points >= 0", name="ck_task_timeout_points_nonnegative"
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND released_points = 0 AND personal_ledger_entry_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND released_cents = 0 AND ledger_entry_id IS NULL)",
            name="ck_task_timeout_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    final_status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    released_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    released_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    ledger_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledger_entries.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    personal_ledger_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_ledger_entries.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    relay_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(TaskTimeoutEvent, "before_update")
def _prevent_task_timeout_event_update(*_) -> None:
    raise RuntimeError("task timeout events are immutable")


@event.listens_for(TaskTimeoutEvent, "before_delete")
def _prevent_task_timeout_event_delete(*_) -> None:
    raise RuntimeError("task timeout events are immutable")


class ShowcaseMedia(Base):
    """Immutable, integrity-verified media approved for the public showcase."""

    __tablename__ = "showcase_media"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_showcase_media_object_key"),
        UniqueConstraint("sha256", name="uq_showcase_media_sha256"),
        UniqueConstraint(
            "created_by_user_id",
            "idempotency_key",
            name="uq_showcase_media_owner_idempotency",
        ),
        UniqueConstraint(
            "source_task_artifact_id",
            name="uq_showcase_media_source_artifact",
        ),
        CheckConstraint(
            "media_type IN ('image', 'video')",
            name="ck_showcase_media_type",
        ),
        CheckConstraint("size_bytes > 0", name="ck_showcase_media_size_positive"),
        *_sha256_check_constraints(
            "sha256", constraint_name="ck_showcase_media_sha256_hex"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_task_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_artifacts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ShowcaseRelease(Base):
    """One immutable, atomically published homepage manifest."""

    __tablename__ = "showcase_releases"
    __table_args__ = (
        UniqueConstraint("version", name="uq_showcase_release_version"),
        UniqueConstraint(
            "publication_version",
            name="uq_showcase_release_publication_version",
        ),
        UniqueConstraint(
            "published_by_user_id",
            "idempotency_key",
            name="uq_showcase_release_owner_idempotency",
        ),
        CheckConstraint("version > 0", name="ck_showcase_release_version_positive"),
        CheckConstraint(
            "publication_version > 0",
            name="ck_showcase_release_publication_version_positive",
        ),
        CheckConstraint(
            "draft_version >= 0", name="ck_showcase_release_draft_version_nonnegative"
        ),
        *_sha256_check_constraints(
            "manifest_sha256", constraint_name="ck_showcase_release_manifest_sha256_hex"
        ),
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_showcase_release_request_sha256_hex",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    publication_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    published_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("showcase_releases.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    release_note: Mapped[str] = mapped_column(String(500), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class ShowcaseChannel(Base):
    """Singleton mutable pointer separating the owner draft from production."""

    __tablename__ = "showcase_channels"
    __table_args__ = (
        CheckConstraint("id = 'home'", name="ck_showcase_channel_singleton"),
        CheckConstraint("draft_version >= 0", name="ck_showcase_channel_draft_nonnegative"),
        CheckConstraint(
            "publication_version >= 0",
            name="ck_showcase_channel_publication_nonnegative",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    draft_version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    publication_version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    current_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("showcase_releases.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ShowcaseDraftItem(TimestampMixin, Base):
    """Mutable owner-only draft row; never read by the public feed."""

    __tablename__ = "showcase_draft_items"
    __table_args__ = (
        Index("ix_showcase_draft_active_order", "retired_at", "sort_order", "id"),
        CheckConstraint("sort_order >= 0", name="ck_showcase_draft_sort_nonnegative"),
        CheckConstraint(
            "section IN ('video', 'template', 'challenge')",
            name="ck_showcase_draft_section",
        ),
        CheckConstraint(
            "category IN ('广告魔法', '电影叙事', '风格艺术', '动漫剧场', "
            "'数字人', '教育学习', '商品展示')",
            name="ck_showcase_draft_category",
        ),
        CheckConstraint(
            "aspect_ratio IN ('auto', '1:1', '3:4', '4:3', '9:16', '16:9')",
            name="ck_showcase_draft_aspect_ratio",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("showcase_media.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    section: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(300), nullable=False)
    public_prompt: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(12), default="auto", nullable=False)
    is_hero: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ShowcaseReleaseItem(Base):
    """Immutable public-safe snapshot row belonging to one release."""

    __tablename__ = "showcase_release_items"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "position", name="uq_showcase_release_item_position"
        ),
        UniqueConstraint(
            "release_id",
            "source_draft_item_id",
            name="uq_showcase_release_item_source",
        ),
        CheckConstraint("position >= 0", name="ck_showcase_release_item_position"),
        CheckConstraint(
            "section IN ('video', 'template', 'challenge')",
            name="ck_showcase_release_item_section",
        ),
        CheckConstraint(
            "category IN ('广告魔法', '电影叙事', '风格艺术', '动漫剧场', "
            "'数字人', '教育学习', '商品展示')",
            name="ck_showcase_release_item_category",
        ),
        CheckConstraint(
            "aspect_ratio IN ('auto', '1:1', '3:4', '4:3', '9:16', '16:9')",
            name="ck_showcase_release_item_aspect_ratio",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("showcase_releases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_draft_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    media_id: Mapped[str] = mapped_column(
        ForeignKey("showcase_media.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    section: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(300), nullable=False)
    public_prompt: Mapped[str] = mapped_column(String(2000), nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(12), nullable=False)
    is_hero: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ShowcasePublicationEvent(Base):
    """Immutable journal entry for an owner-initiated public pointer change."""

    __tablename__ = "showcase_publication_events"
    __table_args__ = (
        UniqueConstraint(
            "publication_version",
            name="uq_showcase_publication_event_version",
        ),
        UniqueConstraint(
            "actor_user_id",
            "idempotency_key",
            name="uq_showcase_publication_event_owner_idempotency",
        ),
        CheckConstraint(
            "action = 'unpublish'",
            name="ck_showcase_publication_event_action",
        ),
        CheckConstraint(
            "expected_draft_version >= 0",
            name="ck_showcase_publication_event_draft_nonnegative",
        ),
        CheckConstraint(
            "publication_version > 0",
            name="ck_showcase_publication_event_version_positive",
        ),
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_showcase_publication_event_request_sha256_hex",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    previous_release_id: Mapped[str] = mapped_column(
        ForeignKey("showcase_releases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_draft_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    publication_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    release_note: Mapped[str] = mapped_column(String(500), nullable=False)
    unpublished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


for _immutable_showcase_model in (
    ShowcaseMedia,
    ShowcaseRelease,
    ShowcaseReleaseItem,
    ShowcasePublicationEvent,
):

    @event.listens_for(_immutable_showcase_model, "before_update")
    def _prevent_showcase_immutable_update(*_) -> None:
        raise RuntimeError("published showcase records are immutable")

    @event.listens_for(_immutable_showcase_model, "before_delete")
    def _prevent_showcase_immutable_delete(*_) -> None:
        raise RuntimeError("published showcase records are immutable")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_created", "created_at"),
        Index("ix_audit_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False)
    before_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    after_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    outcome: Mapped[AuditOutcome] = mapped_column(
        Enum(AuditOutcome, **enum_kwargs),
        default=AuditOutcome.SUCCEEDED,
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RelayChannelOperationJournal(Base):
    """Platform-owned idempotency journal for Relay channel side effects.

    Relay also owns an immutable operation receipt.  This row is the Platform's
    durable approval boundary: it is committed before a Relay POST and is
    unique across every channel and operation kind for one Relay tenant.
    """

    __tablename__ = "relay_channel_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="uq_relay_channel_operation_tenant_operation",
        ),
        Index(
            "ix_relay_channel_operation_channel_created",
            "channel_id",
            "created_at",
        ),
        CheckConstraint(
            "kind IN ('test', 'status')",
            name="ck_relay_channel_operation_kind",
        ),
        CheckConstraint(
            "state IN ('approved', 'completed')",
            name="ck_relay_channel_operation_state",
        ),
        CheckConstraint(
            "(kind = 'test' AND expected_revision IS NULL AND target_status IS NULL) "
            "OR (kind = 'status' AND expected_revision IS NOT NULL "
            "AND target_status IN ('enabled', 'manually_disabled'))",
            name="ck_relay_channel_operation_intent_shape",
        ),
        CheckConstraint(
            "length(intent_sha256) = 64 AND lower(intent_sha256) = intent_sha256",
            name="ck_relay_channel_operation_intent_sha256",
        ),
        CheckConstraint(
            "relay_intent_sha256 IS NULL OR "
            "(length(relay_intent_sha256) = 64 "
            "AND lower(relay_intent_sha256) = relay_intent_sha256)",
            name="ck_relay_channel_operation_relay_sha256",
        ),
        CheckConstraint(
            "(state = 'approved' AND result_audit_id IS NULL "
            "AND completed_at IS NULL) OR "
            "(state = 'completed' AND result_audit_id IS NOT NULL "
            "AND relay_receipt IS NOT NULL AND relay_intent_sha256 IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_relay_channel_operation_completion_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    expected_revision: Mapped[str | None] = mapped_column(String(72), nullable=True)
    target_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    before_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="approved", nullable=False)
    approval_audit_id: Mapped[str] = mapped_column(
        ForeignKey("audit_logs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    result_audit_id: Mapped[str | None] = mapped_column(
        ForeignKey("audit_logs.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    relay_intent_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relay_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    approval_request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    result_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DownloadRecord(Base):
    __tablename__ = "download_records"
    __table_args__ = (
        Index("ix_download_company_created", "company_id", "created_at"),
        Index("ix_download_task_created", "task_id", "created_at"),
        Index(
            "uq_download_gateway_registration_request",
            "gateway_registration_request_id",
            unique=True,
        ),
        Index(
            "uq_download_gateway_ticket",
            "gateway_ticket_id",
            unique=True,
        ),
        Index(
            "uq_download_gateway_transfer_reference",
            "gateway_transfer_reference",
            unique=True,
        ),
        CheckConstraint("expires_seconds > 0", name="ck_download_expiry_positive"),
        CheckConstraint(
            "(storage_binding_version IS NULL "
            "AND storage_provider IS NULL "
            "AND storage_endpoint_host IS NULL "
            "AND storage_bucket IS NULL "
            "AND storage_object_key IS NULL "
            "AND storage_version_id IS NULL "
            "AND source_url_sha256 IS NULL "
            "AND relay_issued_at IS NULL "
            "AND relay_expires_at IS NULL "
            "AND gateway_registration_request_id IS NULL "
            "AND gateway_ticket_id IS NULL "
            "AND gateway_ticket_url_sha256 IS NULL "
            "AND gateway_issued_at IS NULL "
            "AND gateway_expires_at IS NULL "
            "AND gateway_transfer_reference IS NULL) OR "
            "(storage_binding_version = 1 "
            "AND storage_provider IS NOT NULL "
            "AND storage_provider = 'huawei_obs' "
            "AND storage_endpoint_host IS NOT NULL "
            "AND storage_bucket IS NOT NULL "
            "AND storage_object_key IS NOT NULL "
            "AND source_url_sha256 IS NOT NULL "
            "AND length(source_url_sha256) = 64 "
            "AND lower(source_url_sha256) = source_url_sha256 "
            "AND relay_issued_at IS NOT NULL "
            "AND relay_expires_at IS NOT NULL "
            "AND relay_expires_at > relay_issued_at "
            "AND ((gateway_registration_request_id IS NULL "
            "AND gateway_ticket_id IS NULL "
            "AND gateway_ticket_url_sha256 IS NULL "
            "AND gateway_issued_at IS NULL "
            "AND gateway_expires_at IS NULL "
            "AND gateway_transfer_reference IS NULL) OR "
            "(gateway_registration_request_id IS NOT NULL "
            "AND gateway_ticket_id IS NOT NULL "
            "AND gateway_ticket_url_sha256 IS NOT NULL "
            "AND length(gateway_ticket_url_sha256) = 64 "
            "AND lower(gateway_ticket_url_sha256) = gateway_ticket_url_sha256 "
            "AND gateway_issued_at IS NOT NULL "
            "AND gateway_expires_at IS NOT NULL "
            "AND gateway_expires_at > gateway_issued_at "
            "AND expires_at = gateway_expires_at "
            "AND gateway_transfer_reference IS NOT NULL)))",
            name="ck_download_storage_binding_complete",
        ),
        CheckConstraint(
            "source_url_sha256 IS NULL OR ("
            "length(source_url_sha256) = 64 "
            "AND lower(source_url_sha256) = source_url_sha256 "
            "AND source_url_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_download_source_url_sha256_hex",
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            "gateway_ticket_url_sha256 IS NULL OR ("
            "length(gateway_ticket_url_sha256) = 64 "
            "AND lower(gateway_ticket_url_sha256) = gateway_ticket_url_sha256 "
            "AND gateway_ticket_url_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_download_gateway_ticket_url_sha256_hex",
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            "source_url_sha256 IS NULL OR "
            "source_url_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_download_source_url_sha256_hex",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "gateway_ticket_url_sha256 IS NULL OR "
            "gateway_ticket_url_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_download_gateway_ticket_url_sha256_hex",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    expires_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    storage_binding_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    storage_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    storage_endpoint_host: Mapped[str | None] = mapped_column(
        String(253), nullable=True
    )
    storage_bucket: Mapped[str | None] = mapped_column(String(63), nullable=True)
    storage_object_key: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    storage_version_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    source_url_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relay_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    relay_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gateway_registration_request_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    gateway_ticket_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    gateway_ticket_url_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    gateway_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gateway_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gateway_transfer_reference: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PersonalDownloadRecord(Base):
    """Append-only evidence that one personal user requested an artifact URL.

    Personal download evidence intentionally lives outside company reporting.
    The signed URL itself is never persisted; only its digest and the exact
    platform-controlled storage binding are retained.
    """

    __tablename__ = "personal_download_records"
    __table_args__ = (
        Index(
            "ix_personal_download_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index("ix_personal_download_task_created", "task_id", "created_at"),
        CheckConstraint(
            "expires_seconds > 0", name="ck_personal_download_expiry_positive"
        ),
        CheckConstraint(
            "storage_provider = 'huawei_obs'",
            name="ck_personal_download_storage_provider",
        ),
        *_sha256_check_constraints(
            "source_url_sha256",
            constraint_name="ck_personal_download_source_url_sha_hex",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    expires_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_endpoint_host: Mapped[str] = mapped_column(String(253), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    storage_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_version_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    relay_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    relay_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DownloadGatewayRegistrationAttempt(Base):
    __tablename__ = "download_gateway_registration_attempts"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "requested_by_user_id",
            "platform_request_id",
            name="uq_download_gateway_attempt_request",
        ),
        UniqueConstraint(
            "registration_request_id",
            name="uq_download_gateway_attempt_registration",
        ),
        UniqueConstraint(
            "download_record_id",
            name="uq_download_gateway_attempt_record",
        ),
        UniqueConstraint(
            "transfer_reference",
            name="uq_download_gateway_attempt_transfer",
        ),
        Index(
            "ix_download_gateway_attempt_dispatch",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
        ),
        Index(
            "ix_download_gateway_attempt_company_created",
            "company_id",
            "created_at",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_download_gateway_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "expected_size_bytes > 0",
            name="ck_download_gateway_attempt_size_positive",
        ),
        CheckConstraint(
            "ticket_replay_count >= 0 AND "
            "ticket_replay_count <= 9223372036854775807",
            name="ck_download_gateway_attempt_replay_count",
        ),
        CheckConstraint(
            "((lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL))",
            name="ck_download_gateway_attempt_lease_complete",
        ),
        *_sha256_check_constraints(
            "artifact_sha256",
            constraint_name="ck_download_gateway_attempt_artifact_sha_hex",
        ),
        *_sha256_check_constraints(
            "source_url_sha256",
            constraint_name="ck_download_gateway_attempt_source_url_sha_hex",
        ),
        *_sha256_check_constraints(
            "body_sha256",
            constraint_name="ck_download_gateway_attempt_body_sha_shape",
        ),
        *_sha256_check_constraints(
            "response_sha256",
            constraint_name="ck_download_gateway_attempt_response_sha_hex",
            nullable=True,
        ),
        *_sha256_check_constraints(
            "gateway_ticket_url_sha256",
            constraint_name="ck_download_gateway_attempt_ticket_url_sha_hex",
            nullable=True,
        ),
        *_sha256_check_constraints(
            "reconciliation_ack_sha256",
            constraint_name="ck_download_gateway_attempt_reconciliation_ack_sha_hex",
            nullable=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    platform_request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    registration_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    download_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    transfer_reference: Mapped[str] = mapped_column(String(36), nullable=False)
    expected_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_endpoint_host: Mapped[str] = mapped_column(String(253), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    storage_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    relay_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    relay_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    request_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    response_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    gateway_ticket_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gateway_ticket_url_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    gateway_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gateway_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gateway_expires_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    status: Mapped[DownloadGatewayRegistrationStatus] = mapped_column(
        Enum(DownloadGatewayRegistrationStatus, **enum_kwargs),
        default=DownloadGatewayRegistrationStatus.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ticket_replay_count: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    ticket_replayed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_destroy_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_ack_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dead_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DownloadCompletion(Base):
    __tablename__ = "download_completions"
    __table_args__ = (
        UniqueConstraint(
            "download_record_id", name="uq_download_completion_record"
        ),
        UniqueConstraint(
            "external_event_id", name="uq_download_completion_external_event"
        ),
        Index("ix_download_completion_completed", "completed_at", "id"),
        CheckConstraint(
            "bytes_sent >= 0", name="ck_download_completion_bytes_nonnegative"
        ),
        CheckConstraint(
            "(verification_version IS NULL "
            "AND artifact_sha256 IS NULL "
            "AND expected_size_bytes IS NULL "
            "AND http_status IS NULL "
            "AND transfer_scope IS NULL "
            "AND source_evidence IS NULL "
            "AND signed_event_id IS NULL "
            "AND signed_event_timestamp IS NULL "
            "AND signed_payload_sha256 IS NULL "
            "AND verified_at IS NULL) OR "
            "(verification_version = 1 "
            "AND artifact_sha256 IS NOT NULL "
            "AND expected_size_bytes IS NOT NULL "
            "AND expected_size_bytes = bytes_sent "
            "AND http_status = 200 "
            "AND transfer_scope = 'full_body' "
            "AND source_evidence IS NOT NULL "
            "AND signed_event_id IS NOT NULL "
            "AND signed_event_timestamp IS NOT NULL "
            "AND signed_payload_sha256 IS NOT NULL "
            "AND verified_at IS NOT NULL)",
            name="ck_download_completion_verified_evidence_complete",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR ("
            "length(artifact_sha256) = 64 "
            "AND lower(artifact_sha256) = artifact_sha256 "
            "AND replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(artifact_sha256, '0', ''), '1', ''), "
            "'2', ''), '3', ''), '4', ''), '5', ''), '6', ''), '7', ''), "
            "'8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), "
            "'e', ''), 'f', '') = '')",
            name="ck_download_completion_artifact_sha256",
        ),
        CheckConstraint(
            "signed_payload_sha256 IS NULL OR ("
            "length(signed_payload_sha256) = 64 "
            "AND lower(signed_payload_sha256) = signed_payload_sha256 "
            "AND replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(signed_payload_sha256, '0', ''), "
            "'1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), "
            "'7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), "
            "'d', ''), 'e', ''), 'f', '') = '')",
            name="ck_download_completion_payload_sha256",
        ),
        CheckConstraint(
            "signed_event_id IS NULL OR ("
            "length(signed_event_id) = 36 "
            "AND substr(signed_event_id, 9, 1) = '-' "
            "AND substr(signed_event_id, 14, 1) = '-' "
            "AND substr(signed_event_id, 19, 1) = '-' "
            "AND substr(signed_event_id, 24, 1) = '-' "
            "AND lower(signed_event_id) = signed_event_id "
            "AND replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(signed_event_id, '0', ''), "
            "'1', ''), '2', ''), '3', ''), '4', ''), '5', ''), '6', ''), "
            "'7', ''), '8', ''), '9', ''), 'a', ''), 'b', ''), 'c', ''), "
            "'d', ''), 'e', ''), 'f', ''), '-', '') = '')",
            name="ck_download_completion_signed_event_id",
        ),
        # PostgreSQL revision 0021 deliberately installed a NOT VALID check:
        # historical unsigned rows remain readable, while every new row must
        # carry verified source evidence.  SQLite preserves that same split
        # with a compatibility check plus an insert trigger.
        CheckConstraint(
            "verification_version IS NULL OR "
            "source IN ('EDGE_GATEWAY', 'OBS_ACCESS_LOG')",
            name="ck_download_completion_verified_source",
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            "verification_version IS NOT NULL "
            "AND verification_version = 1 "
            "AND source IN ('EDGE_GATEWAY', 'OBS_ACCESS_LOG')",
            name="ck_download_completion_verified_source",
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_download_completion_signed_event",
            "signed_event_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    download_record_id: Mapped[str] = mapped_column(
        ForeignKey("download_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    external_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source: Mapped[DownloadCompletionSource] = mapped_column(
        Enum(DownloadCompletionSource, **enum_kwargs), nullable=False
    )
    bytes_sent: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verification_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    artifact_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    expected_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfer_scope: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    source_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    signed_event_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    signed_event_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signed_payload_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(DownloadRecord, "before_update")
def _prevent_download_record_update(*_) -> None:
    raise RuntimeError("download records are immutable")


@event.listens_for(DownloadRecord, "before_delete")
def _prevent_download_record_delete(*_) -> None:
    raise RuntimeError("download records are immutable")


@event.listens_for(PersonalDownloadRecord, "before_update")
def _prevent_personal_download_record_update(*_) -> None:
    raise RuntimeError("personal download records are immutable")


@event.listens_for(PersonalDownloadRecord, "before_delete")
def _prevent_personal_download_record_delete(*_) -> None:
    raise RuntimeError("personal download records are immutable")


@event.listens_for(DownloadCompletion, "before_update")
def _prevent_download_completion_update(*_) -> None:
    raise RuntimeError("download completions are immutable")


@event.listens_for(DownloadCompletion, "before_insert")
def _require_verified_download_completion(_, __, target: DownloadCompletion) -> None:
    if target.verification_version != 1 or target.source not in {
        DownloadCompletionSource.EDGE_GATEWAY,
        DownloadCompletionSource.OBS_ACCESS_LOG,
    }:
        raise RuntimeError(
            "new download completions require signed source evidence"
        )


@event.listens_for(DownloadCompletion, "before_delete")
def _prevent_download_completion_delete(*_) -> None:
    raise RuntimeError("download completions are immutable")


@event.listens_for(AuditLog, "before_update")
def _prevent_audit_update(*_) -> None:
    raise RuntimeError("audit logs are immutable")


@event.listens_for(AuditLog, "before_delete")
def _prevent_audit_delete(*_) -> None:
    raise RuntimeError("audit logs are immutable")


@event.listens_for(AccountSecurityEvent, "before_update")
def _prevent_account_security_event_update(*_) -> None:
    raise RuntimeError("account security events are immutable")


@event.listens_for(AccountSecurityEvent, "before_delete")
def _prevent_account_security_event_delete(*_) -> None:
    raise RuntimeError("account security events are immutable")
