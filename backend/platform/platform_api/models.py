from __future__ import annotations

import enum
import json
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
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect as sa_inspect,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .relay_identity import (
    NEW_API_RELAY_BACKEND_ID,
    NEW_API_RELAY_CONTRACT_REVISION,
)


def new_id() -> str:
    return str(uuid.uuid4())


def new_director_shot_package_id() -> str:
    """Return an opaque, type-distinguishable identifier for a sealed shot package."""

    return f"dsp_{uuid.uuid4().hex}"


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


class UserAccountType(str, enum.Enum):
    """The single product boundary assigned to one local user account."""

    PERSONAL = "personal"
    COMPANY = "company"
    PLATFORM_ADMIN = "platform_admin"


class ProductContext(str, enum.Enum):
    """The mutually exclusive product surface carried by one BFF session."""

    PERSONAL = "personal"
    COMPANY = "company"
    PLATFORM = "platform"


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
    REFUND_RESERVE = "refund_reserve"
    REFUND_SETTLE = "refund_settle"
    REFUND_RELEASE = "refund_release"
    CHARGEBACK = "chargeback"
    DISPUTE_REVERSAL = "dispute_reversal"
    DEBT_RECOVERY = "debt_recovery"


class BillingUnit(str, enum.Enum):
    """Immutable customer-facing unit attached to a billing contract."""

    CNY_CENT = "CNY_CENT"
    POINT = "POINT"


class PointLedgerKind(str, enum.Enum):
    MIGRATION = "migration"
    CREDIT = "credit"
    RESERVE = "reserve"
    SETTLE = "settle"
    RELEASE = "release"
    REFUND_RESERVE = "refund_reserve"
    REFUND_SETTLE = "refund_settle"
    REFUND_RELEASE = "refund_release"
    CHARGEBACK = "chargeback"
    DISPUTE_REVERSAL = "dispute_reversal"
    DEBT_RECOVERY = "debt_recovery"


class PointLotSourceKind(str, enum.Enum):
    PURCHASED = "purchased"
    CONTRACT = "contract"
    PROMOTIONAL = "promotional"
    COMPENSATION = "compensation"
    LEGACY = "legacy"
    MIGRATION_REMAINDER = "migration_remainder"
    INTERNAL_TEST = "internal_test"


class PointPriceVersionStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class PaymentPurpose(str, enum.Enum):
    POINT_PURCHASE = "point_purchase"
    INVOICE_PAYMENT = "invoice_payment"


class PaymentOrderStatus(str, enum.Enum):
    CREATED = "created"
    PENDING = "pending"
    REQUIRES_ACTION = "requires_action"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PaymentAttemptStatus(str, enum.Enum):
    PENDING = "pending"
    REQUIRES_ACTION = "requires_action"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class PaymentWebhookOutcome(str, enum.Enum):
    PROCESSED = "processed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PaymentTransactionKind(str, enum.Enum):
    CAPTURE = "capture"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    DISPUTE_REVERSAL = "dispute_reversal"
    FEE = "fee"
    PAYOUT = "payout"


class PaymentRefundStatus(str, enum.Enum):
    REQUESTED = "requested"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PaymentDisputeStatus(str, enum.Enum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"


class PaymentProviderCommandOperation(str, enum.Enum):
    CREATE_PAYMENT = "create_payment"
    CREATE_REFUND = "create_refund"
    QUERY_PAYMENT = "query_payment"
    QUERY_REFUND = "query_refund"


class PaymentProviderCommandStatus(str, enum.Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class PaymentWebhookInboxStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    BLOCKED = "blocked"
    PROCESSED = "processed"
    DEAD = "dead"


class PaymentMandateStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
    FAILED = "failed"


class PaymentSettlementSourceKind(str, enum.Enum):
    PSP_STATEMENT = "psp_statement"
    BANK_STATEMENT = "bank_statement"


class EnterpriseDunningRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EnterpriseContractStatus(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    TERMINATED = "terminated"


class EnterpriseBillingCycleStatus(str, enum.Enum):
    OPEN = "open"
    FROZEN = "frozen"
    ISSUED = "issued"
    PAID = "paid"
    OVERDUE = "overdue"
    DISPUTED = "disputed"
    CLOSED = "closed"


class EnterpriseInvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    DISPUTED = "disputed"
    VOID = "void"


class ReconciliationRunStatus(str, enum.Enum):
    RUNNING = "running"
    BALANCED = "balanced"
    BALANCED_WITH_EXCEPTIONS = "balanced_with_exceptions"
    FAILED = "failed"
    STALE = "stale"


class ReconciliationDimensionStatus(str, enum.Enum):
    MATCHED = "matched"
    MISSING = "missing"
    MISMATCH = "mismatch"
    DUPLICATE = "duplicate"
    UNATTRIBUTED = "unattributed"
    PENDING = "pending"
    NOT_APPLICABLE = "not_applicable"
    SOURCE_UNAVAILABLE = "source_unavailable"


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
product_context_enum_kwargs: dict[str, Any] = {
    **enum_kwargs,
    "values_callable": lambda enum_type: [item.value for item in enum_type],
    "length": 16,
}


def _default_user_account_type(context: Any) -> UserAccountType:
    """Keep legacy ORM fixtures safe while production callers become explicit.

    SQLAlchemy invokes column defaults after collecting the other INSERT values,
    so an explicitly platform-admin user receives the matching product boundary.
    Ordinary legacy ``User(...)`` construction remains a personal account.  The
    database constraint and membership/workspace triggers still reject a caller
    that tries to use either default across the wrong product boundary.
    """

    parameters = context.get_current_parameters()
    if bool(parameters.get("is_platform_admin", False)):
        return UserAccountType.PLATFORM_ADMIN
    return UserAccountType.PERSONAL


def _default_generation_billing_unit(context: Any) -> BillingUnit:
    parameters = context.get_current_parameters()
    if parameters.get("personal_workspace_id") is not None:
        return BillingUnit.POINT
    return BillingUnit.CNY_CENT


def _default_generation_billing_version(context: Any) -> int:
    return 2 if _default_generation_billing_unit(context) == BillingUnit.POINT else 1


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "billing_version IN (1, 2)", name="ck_company_billing_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[CompanyStatus] = mapped_column(
        Enum(CompanyStatus, **enum_kwargs), default=CompanyStatus.ACTIVE, nullable=False
    )
    billing_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    @property
    def billing_unit(self) -> BillingUnit:
        if self.billing_version == 2:
            return BillingUnit.POINT
        return BillingUnit.CNY_CENT


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("auth_version >= 1", name="ck_users_auth_version"),
        CheckConstraint(
            "(status = 'DEACTIVATED' AND deactivated_at IS NOT NULL) OR "
            "(status <> 'DEACTIVATED' AND deactivated_at IS NULL)",
            name="ck_users_status_deactivated",
        ),
        CheckConstraint(
            "(account_type = 'PLATFORM_ADMIN') OR "
            "(account_type IN ('PERSONAL', 'COMPANY') AND is_platform_admin = false)",
            name="ck_users_account_type_admin_consistency",
        ),
        CheckConstraint(
            "account_type IN ('PERSONAL', 'COMPANY', 'PLATFORM_ADMIN')",
            name="ck_users_account_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    account_type: Mapped[UserAccountType] = mapped_column(
        Enum(UserAccountType, **enum_kwargs),
        default=_default_user_account_type,
        server_default="PERSONAL",
        nullable=False,
    )
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
        CheckConstraint(
            "active_product_context IN ('personal', 'company', 'platform')",
            name="ck_auth_session_product_context",
        ),
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
    active_product_context: Mapped[ProductContext] = mapped_column(
        Enum(ProductContext, **product_context_enum_kwargs),
        server_default="personal",
        nullable=False,
    )
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

    __table_args__ = (
        UniqueConstraint(
            "owner_self_identity_id",
            name="uq_personal_workspace_owner_self_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_self_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_identities.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


class AuthProductContextSwitch(Base):
    """Durable idempotency and audit linkage for a session context rotation."""

    __tablename__ = "auth_product_context_switches"
    __table_args__ = (
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_auth_product_context_switch_fingerprint_sha256",
        ),
        CheckConstraint(
            "source_context IN ('personal', 'company', 'platform')",
            name="ck_auth_product_context_switch_source",
        ),
        CheckConstraint(
            "target_context IN ('personal', 'company', 'platform')",
            name="ck_auth_product_context_switch_target",
        ),
        CheckConstraint(
            "source_context <> target_context",
            name="ck_auth_product_context_switch_distinct",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_auth_product_context_switch_user_key",
        ),
        UniqueConstraint(
            "source_session_id",
            name="uq_auth_product_context_switch_source_session",
        ),
        UniqueConstraint(
            "target_session_id",
            name="uq_auth_product_context_switch_target_session",
        ),
        Index(
            "ix_auth_product_context_switch_user_created",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_session_id: Mapped[str] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    target_session_id: Mapped[str] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    source_context: Mapped[ProductContext] = mapped_column(
        Enum(ProductContext, **product_context_enum_kwargs), nullable=False
    )
    target_context: Mapped[ProductContext] = mapped_column(
        Enum(ProductContext, **product_context_enum_kwargs), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


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
    # Relay discovery and Platform approval are deliberately separate.  A
    # channel/key/routing release may move the Relay catalog revision without
    # changing the capability revision, and therefore must not invalidate a
    # customer-facing model approval.
    relay_capability_candidate_revision: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    relay_capability_candidate_catalog_revision: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    relay_capability_candidate: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    relay_capability_candidate_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # This is the immutable decision ceiling captured when the current
    # relay_capability_revision was approved.  Platform model edits may equal
    # or narrow this document, but can never expand beyond it.
    relay_capability_approved_ceiling: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    relay_capability_approved_catalog_revision: Mapped[str | None] = mapped_column(
        String(71), nullable=True
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
            "price_per_second_points IS NULL OR price_per_second_points > 0",
            name="ck_grant_second_points_positive",
        ),
        CheckConstraint(
            "price_per_item_points IS NULL OR price_per_item_points > 0",
            name="ck_grant_item_points_positive",
        ),
        CheckConstraint(
            "(price_per_second_cents IS NULL AND price_per_item_cents IS NULL "
            "AND price_per_second_points IS NULL AND price_per_item_points IS NULL) OR "
            "((price_per_second_cents IS NOT NULL AND price_per_item_cents IS NULL) "
            "OR (price_per_second_cents IS NULL AND price_per_item_cents IS NOT NULL)) "
            "AND price_per_second_points IS NULL AND price_per_item_points IS NULL "
            "OR ((price_per_second_points IS NOT NULL AND price_per_item_points IS NULL) "
            "OR (price_per_second_points IS NULL AND price_per_item_points IS NOT NULL)) "
            "AND price_per_second_cents IS NULL AND price_per_item_cents IS NULL",
            name="ck_grant_exactly_one_price",
        ),
        CheckConstraint(
            "point_price_candidate_per_second IS NULL "
            "OR point_price_candidate_per_second > 0",
            name="ck_grant_candidate_second_points_positive",
        ),
        CheckConstraint(
            "point_price_candidate_per_item IS NULL "
            "OR point_price_candidate_per_item > 0",
            name="ck_grant_candidate_item_points_positive",
        ),
        CheckConstraint(
            "point_price_candidate_per_second IS NULL "
            "OR point_price_candidate_per_item IS NULL",
            name="ck_grant_candidate_one_mode",
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
    price_per_second_points: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_per_item_points: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    point_price_candidate_per_second: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    point_price_candidate_per_item: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    point_price_candidate_revision: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    point_price_candidate_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    point_price_candidate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "company_point_price_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_grant_point_candidate_version",
        ),
        nullable=True,
    )
    point_price_active_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "company_point_price_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_grant_point_active_version",
        ),
        nullable=True,
    )
    config_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    call_quota: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CompanyPointPriceVersion(Base):
    """Immutable price history; grants only point at the selected versions."""

    __tablename__ = "company_point_price_versions"
    __table_args__ = (
        Index(
            "ix_company_point_price_version_grant_created",
            "grant_id",
            "created_at",
            "id",
        ),
        CheckConstraint(
            "status IN ('CANDIDATE', 'ACTIVE', 'SUPERSEDED')",
            name="ck_company_point_price_version_status",
        ),
        CheckConstraint(
            "billing_mode IN ('per_second', 'per_item')",
            name="ck_company_point_price_version_mode",
        ),
        CheckConstraint(
            "unit_price_points > 0",
            name="ck_company_point_price_version_positive",
        ),
        CheckConstraint(
            "source_price_cents IS NULL OR source_price_cents > 0",
            name="ck_company_point_price_version_source_positive",
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_company_point_price_version_sha_length",
        ),
        CheckConstraint(
            "(created_by_user_id IS NOT NULL AND created_by_system_key IS NULL) OR "
            "(created_by_user_id IS NULL AND created_by_system_key IS NOT NULL)",
            name="ck_company_point_price_version_actor",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grant_id: Mapped[str] = mapped_column(
        ForeignKey("company_model_grants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[PointPriceVersionStatus] = mapped_column(
        Enum(PointPriceVersionStatus, **enum_kwargs), nullable=False
    )
    billing_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_price_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    formula_version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_point_price_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_by_system_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
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
        CheckConstraint(
            "call_quota IS NULL OR call_quota > 0",
            name="ck_personal_retail_call_quota_positive",
        ),
        CheckConstraint(
            "concurrency_limit IS NULL OR concurrency_limit > 0",
            name="ck_personal_retail_concurrency_positive",
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
    call_quota: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ModelCommercialReleasePlan(Base):
    """Immutable Platform-owner decision for one Relay capability candidate.

    Provider contract evidence and the FX snapshot are deliberately stored next
    to the derived customer prices.  A route/key becoming ready can therefore
    consume a previously approved decision, but can never invent a cost or a
    price while reconciling the Relay catalog.
    """

    __tablename__ = "model_commercial_release_plans"
    __table_args__ = (
        UniqueConstraint(
            "model_id",
            "candidate_revision",
            "revision",
            name="uq_model_commercial_plan_revision",
        ),
        UniqueConstraint(
            "supersedes_plan_id", name="uq_model_commercial_plan_successor",
        ),
        CheckConstraint(
            "(revision = 1 AND supersedes_plan_id IS NULL) OR "
            "(revision > 1 AND supersedes_plan_id IS NOT NULL)",
            name="ck_model_commercial_plan_revision",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_model_commercial_plan_idempotency"
        ),
        CheckConstraint(
            "billing_mode IN ('per_second', 'per_item')",
            name="ck_model_commercial_plan_billing_mode",
        ),
        CheckConstraint(
            "provider_cost_currency IN ('CNY', 'USD')",
            name="ck_model_commercial_plan_currency",
        ),
        CheckConstraint(
            "provider_cost_micros > 0 AND provider_cost_cny_micros > 0",
            name="ck_model_commercial_plan_cost_positive",
        ),
        CheckConstraint(
            "fx_cny_micros_per_currency_unit > 0",
            name="ck_model_commercial_plan_fx_positive",
        ),
        CheckConstraint(
            "points_per_cny = 10",
            name="ck_model_commercial_plan_points_exchange",
        ),
        CheckConstraint(
            "target_margin_bps = 3000",
            name="ck_model_commercial_plan_margin",
        ),
        CheckConstraint(
            "minimum_price_points > 0 "
            "AND personal_price_points >= minimum_price_points "
            "AND enterprise_price_points >= minimum_price_points",
            name="ck_model_commercial_plan_prices",
        ),
        CheckConstraint(
            "enterprise_distribution_scope = "
            "'all_active_point_companies_at_release'",
            name="ck_model_commercial_plan_distribution_scope",
        ),
        *_sha256_check_constraints(
            "provider_cost_evidence_sha256",
            constraint_name="ck_model_commercial_plan_cost_sha",
        ),
        *_sha256_check_constraints(
            "fx_evidence_sha256",
            constraint_name="ck_model_commercial_plan_fx_sha",
        ),
        *_sha256_check_constraints(
            "content_sha256",
            constraint_name="ck_model_commercial_plan_content_sha",
        ),
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_model_commercial_plan_request_sha",
        ),
        CheckConstraint(
            "(approved_route_identity IS NULL AND "
            "approved_route_identity_sha256 IS NULL) OR "
            "(approved_route_identity IS NOT NULL AND "
            "approved_route_identity_sha256 IS NOT NULL)",
            name="ck_model_commercial_plan_route_identity_complete",
        ),
        *_sha256_check_constraints(
            "approved_route_identity_sha256",
            constraint_name="ck_model_commercial_plan_route_identity_sha",
        ),
        Index(
            "ix_model_commercial_plan_model_created",
            "model_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_model_commercial_plan_batch",
            "batch_id",
            "model_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_revision: Mapped[str] = mapped_column(String(71), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    supersedes_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_commercial_release_plans.id", ondelete="RESTRICT"), nullable=True,
    )
    # Bound at INSERT time only: this table is immutable, so a plan can never be
    # moved into or out of a batch afterwards.  NULL keeps every pre-batch plan
    # working exactly as before.
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_commercial_release_batches.id", ondelete="RESTRICT"),
        nullable=True,
    )
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_catalog_revision: Mapped[str] = mapped_column(String(71), nullable=False)
    capability_version: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_cost_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider_cost_formula: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    provider_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_cost_cny_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_cost_evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_cost_evidence_reference: Mapped[str] = mapped_column(
        String(500), nullable=False
    )
    provider_cost_evidence_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    provider_cost_effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fx_cny_micros_per_currency_unit: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    fx_source: Mapped[str] = mapped_column(String(120), nullable=False)
    fx_version: Mapped[str] = mapped_column(String(160), nullable=False)
    fx_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fx_effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    points_per_cny: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    target_margin_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    minimum_price_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    personal_price_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    enterprise_price_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    enterprise_distribution_scope: Mapped[str] = mapped_column(
        String(80), nullable=False
    )
    personal_config_override: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    enterprise_config_override: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    approval_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    approved_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    approved_route_identity: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    approved_route_identity_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ModelCommercialReleaseExecution(TimestampMixin, Base):
    """Mutable, locked state machine for one immutable commercial plan."""

    __tablename__ = "model_commercial_release_executions"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_model_commercial_execution_plan"),
        CheckConstraint(
            "state IN ('approved', 'blocked', 'released', 'superseded')",
            name="ck_model_commercial_execution_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND company_grant_count >= 0",
            name="ck_model_commercial_execution_counts",
        ),
        CheckConstraint(
            "(state = 'released' AND released_at IS NOT NULL "
            "AND last_blocker_code IS NULL AND last_blocker_message IS NULL) OR "
            "(state <> 'released' AND released_at IS NULL)",
            name="ck_model_commercial_execution_terminal",
        ),
        CheckConstraint(
            "(publication_receipt IS NULL AND publication_receipt_sha256 IS NULL "
            "AND released_route_identity_sha256 IS NULL) OR "
            "(publication_receipt IS NOT NULL AND publication_receipt_sha256 IS NOT NULL "
            "AND released_route_identity_sha256 IS NOT NULL)",
            name="ck_model_commercial_execution_receipt_complete",
        ),
        *_sha256_check_constraints(
            "publication_receipt_sha256",
            constraint_name="ck_model_commercial_execution_receipt_sha",
        ),
        *_sha256_check_constraints(
            "released_route_identity_sha256",
            constraint_name="ck_model_commercial_execution_route_sha",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("model_commercial_release_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(20), default="approved", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_blocker_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_blocker_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    route_release_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    released_route_identity_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    publication_receipt: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    publication_receipt_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    personal_grant_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_retail_model_grants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    company_grant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    company_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


@event.listens_for(ModelCommercialReleasePlan, "before_update")
def _prevent_model_commercial_plan_update(*_) -> None:
    raise RuntimeError("model commercial release plans are immutable")


@event.listens_for(ModelCommercialReleasePlan, "before_delete")
def _prevent_model_commercial_plan_delete(*_) -> None:
    raise RuntimeError("model commercial release plans are immutable")


class ModelCommercialReleaseBatch(TimestampMixin, Base):
    """Atomic grouping for a set of immutable commercial release plans.

    Each plan stays the durable owner decision for exactly one model.  This row
    adds only the grouping plus the all-or-none activation primitive: activation
    commits the journal row, every plan's release mutations, every immutable
    audit row and the completed result in one transaction, so a normally
    committed row is always ``released``.

    ``ModelCommercialReleasePlan`` is immutable, so ``batch_id`` is bound at
    plan INSERT time and never rewritten.  A batch row must therefore be
    inserted before its plans, in the same transaction.

    ``attempt_count`` / ``last_failure_*`` describe a *failed* activation and
    are deliberately written by a separate post-rollback transaction: the
    atomic activation transaction must leave no partial trace, but an operator
    still needs to see why the batch did not release.
    """

    __tablename__ = "model_commercial_release_batches"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_model_commercial_batch_idempotency",
        ),
        CheckConstraint(
            "state IN ('approved', 'released', 'abandoned')",
            name="ck_model_commercial_batch_state",
        ),
        CheckConstraint("model_count >= 1", name="ck_model_commercial_batch_size"),
        CheckConstraint(
            "length(request_sha256) = 64",
            name="ck_model_commercial_batch_request_sha256",
        ),
        CheckConstraint(
            "length(catalog_revision) > 0",
            name="ck_model_commercial_batch_catalog_revision",
        ),
        CheckConstraint(
            "(state = 'released' AND result_payload IS NOT NULL "
            "AND released_at IS NOT NULL) OR "
            "(state IN ('approved', 'abandoned') AND result_payload IS NULL "
            "AND released_at IS NULL)",
            name="ck_model_commercial_batch_result_shape",
        ),
        CheckConstraint(
            "(state = 'released' AND activated_by_user_id IS NOT NULL) OR "
            "(state IN ('approved', 'abandoned') AND activated_by_user_id IS NULL)",
            name="ck_model_commercial_batch_activator_shape",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_model_commercial_batch_attempts"),
        Index(
            "ix_model_commercial_batch_state_created",
            "state",
            "created_at",
            "id",
        ),
        Index(
            "ix_model_commercial_batch_actor_created",
            "approved_by_user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="approved"
    )
    model_count: Mapped[int] = mapped_column(Integer, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_revision: Mapped[str] = mapped_column(String(71), nullable=False)
    approved_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    activated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_failure_code: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    last_failure_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )


class PersonalModelGrantBatchJournal(TimestampMixin, Base):
    """Unique transaction journal for platform-wide personal model batches.

    Audit rows remain the immutable human-readable history.  This journal is
    the database-enforced concurrency primitive: an idempotency key can be
    claimed exactly once even when two owner sessions submit simultaneously.
    The successful result is stored here so a retry never depends on current
    Relay availability or on an eventually queried audit row.
    """

    __tablename__ = "personal_model_grant_batch_journals"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_personal_model_grant_batch_idempotency",
        ),
        CheckConstraint(
            "state IN ('pending', 'succeeded')",
            name="ck_personal_model_grant_batch_state",
        ),
        CheckConstraint(
            "length(request_sha256) = 71 "
            "AND substr(request_sha256, 1, 7) = 'sha256:'",
            name="ck_personal_model_grant_batch_request_sha256",
        ),
        CheckConstraint(
            "length(expected_snapshot) = 71 "
            "AND substr(expected_snapshot, 1, 7) = 'sha256:'",
            name="ck_personal_model_grant_batch_snapshot_sha256",
        ),
        CheckConstraint(
            "(state = 'pending' AND result_payload IS NULL) OR "
            "(state = 'succeeded' AND result_payload IS NOT NULL)",
            name="ck_personal_model_grant_batch_result_shape",
        ),
        Index(
            "ix_personal_model_grant_batch_actor_created",
            "actor_user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_snapshot: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class CompanyEntitlementBatchJournal(TimestampMixin, Base):
    """Unique all-or-none journal for company entitlement mutations.

    The journal row, grant mutations, immutable audit row, and completed result
    are committed in one database transaction. A normally committed row is
    therefore always completed. A persisted ``pending`` row is treated as an
    anomalous outcome requiring operator reconciliation and is never retried
    automatically.
    """

    __tablename__ = "company_entitlement_batch_journals"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_company_entitlement_batch_idempotency",
        ),
        CheckConstraint(
            "state IN ('pending', 'completed', 'frozen')",
            name="ck_company_entitlement_batch_state",
        ),
        CheckConstraint(
            "length(request_sha256) = 64",
            name="ck_company_entitlement_batch_request_sha256",
        ),
        CheckConstraint(
            "length(expected_snapshot) = 64",
            name="ck_company_entitlement_batch_snapshot_sha256",
        ),
        CheckConstraint(
            "(state = 'pending' AND result_payload IS NULL) OR "
            "(state IN ('completed', 'frozen') AND result_payload IS NOT NULL)",
            name="ck_company_entitlement_batch_result_shape",
        ),
        Index(
            "ix_company_entitlement_batch_actor_created",
            "actor_user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )


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


class CompanyPointWalletAccount(TimestampMixin, Base):
    """Company-only points projection; never shared with personal workspaces."""

    __tablename__ = "company_point_wallet_accounts"
    __table_args__ = (
        CheckConstraint(
            "available_points >= 0", name="ck_company_point_wallet_available"
        ),
        CheckConstraint(
            "reserved_points >= 0", name="ck_company_point_wallet_reserved"
        ),
        CheckConstraint(
            "reversal_reserved_points >= 0",
            name="ck_company_point_wallet_reversal_reserved",
        ),
        CheckConstraint(
            "debt_points >= 0", name="ck_company_point_wallet_debt"
        ),
        CheckConstraint(
            "migrated_from_available_cents >= 0",
            name="ck_company_point_wallet_migrated_cents",
        ),
        CheckConstraint(
            "migration_remainder_cents BETWEEN 0 AND 9",
            name="ck_company_point_wallet_remainder",
        ),
        CheckConstraint(
            "migration_rounding_grant_points IN (0, 1)",
            name="ck_company_point_wallet_rounding_grant",
        ),
    )

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    available_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reversal_reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    debt_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    migration_idempotency_key: Mapped[str] = mapped_column(
        String(120), nullable=False
    )
    migrated_from_available_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    migration_remainder_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    migration_rounding_grant_points: Mapped[int] = mapped_column(
        Integer, nullable=False
    )


class CompanyPointLot(TimestampMixin, Base):
    __tablename__ = "company_point_lots"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key", name="uq_company_point_lot_idempotency"
        ),
        Index(
            "ix_company_point_lot_spend_order",
            "company_id",
            "expires_at",
            "created_at",
            "id",
        ),
        CheckConstraint("original_points > 0", name="ck_company_point_lot_original"),
        CheckConstraint(
            "source_kind IN ('PURCHASED', 'CONTRACT', 'PROMOTIONAL', "
            "'COMPENSATION', 'LEGACY', 'MIGRATION_REMAINDER', 'INTERNAL_TEST')",
            name="ck_company_point_lot_source_kind",
        ),
        CheckConstraint("available_points >= 0", name="ck_company_point_lot_available"),
        CheckConstraint("reserved_points >= 0", name="ck_company_point_lot_reserved"),
        CheckConstraint(
            "reversal_reserved_points >= 0",
            name="ck_company_point_lot_reversal_reserved",
        ),
        CheckConstraint("settled_points >= 0", name="ck_company_point_lot_settled"),
        CheckConstraint("reversed_points >= 0", name="ck_company_point_lot_reversed"),
        CheckConstraint("cash_basis_cents >= 0", name="ck_company_point_lot_cash_basis"),
        CheckConstraint(
            "receivable_basis_cents >= 0",
            name="ck_company_point_lot_receivable_basis",
        ),
        CheckConstraint("subsidy_cents >= 0", name="ck_company_point_lot_subsidy"),
        CheckConstraint(
            "cash_basis_cents + receivable_basis_cents + subsidy_cents "
            "= original_points * 10",
            name="ck_company_point_lot_value_basis",
        ),
        CheckConstraint(
            "original_points = available_points + reserved_points + "
            "reversal_reserved_points + settled_points + reversed_points",
            name="ck_company_point_lot_conservation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_kind: Mapped[PointLotSourceKind] = mapped_column(
        Enum(PointLotSourceKind, **enum_kwargs), nullable=False
    )
    original_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reversal_reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    settled_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reversed_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    cash_basis_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    receivable_basis_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    subsidy_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )
    contract_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "company_billing_contract_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    billing_cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_billing_cycles.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )


class CompanyPointLedgerEntry(Base):
    __tablename__ = "company_point_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_company_point_ledger_idempotency",
        ),
        Index(
            "ix_company_point_ledger_created", "company_id", "created_at", "id"
        ),
        CheckConstraint("amount_points >= 0", name="ck_company_point_ledger_amount"),
        CheckConstraint(
            "kind IN ('MIGRATION', 'CREDIT', 'RESERVE', 'SETTLE', 'RELEASE', "
            "'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', "
            "'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY')",
            name="ck_company_point_ledger_kind",
        ),
        CheckConstraint(
            "(kind = 'MIGRATION' AND amount_points >= 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'CREDIT' AND amount_points > 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'RESERVE' AND amount_points > 0 "
            "AND available_delta_points = -amount_points "
            "AND reserved_delta_points = amount_points "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'SETTLE' AND amount_points > 0 "
            "AND available_delta_points = 0 "
            "AND reserved_delta_points = -amount_points "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'RELEASE' AND amount_points > 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = -amount_points "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'REFUND_RESERVE' AND amount_points > 0 "
            "AND available_delta_points = -amount_points AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR "
            "(kind = 'REFUND_SETTLE' AND amount_points > 0 "
            "AND available_delta_points = 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
            "(kind = 'REFUND_RELEASE' AND amount_points > 0 "
            "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
            "(kind = 'CHARGEBACK' AND amount_points > 0 "
            "AND available_delta_points <= 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 "
            "AND -available_delta_points + debt_delta_points = amount_points) OR "
            "(kind = 'DISPUTE_REVERSAL' AND amount_points > 0 "
            "AND available_delta_points >= 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 "
            "AND available_delta_points - debt_delta_points = amount_points) OR "
            "(kind = 'DEBT_RECOVERY' AND amount_points > 0 "
            "AND available_delta_points = 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 "
            "AND debt_delta_points = -amount_points)",
            name="ck_company_point_ledger_delta_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[PointLedgerKind] = mapped_column(
        Enum(PointLedgerKind, **enum_kwargs), nullable=False
    )
    amount_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_delta_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_delta_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reversal_reserved_delta_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    debt_delta_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    payment_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    payment_refund_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_refunds.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    payment_dispute_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_disputes.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    note: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TaskPointLotAllocation(Base):
    __tablename__ = "task_point_lot_allocations"
    __table_args__ = (
        UniqueConstraint("task_id", "lot_id", name="uq_task_point_lot_allocation"),
        Index("ix_task_point_lot_allocation_task", "task_id", "id"),
        CheckConstraint("allocated_points > 0", name="ck_task_point_allocation_total"),
        CheckConstraint("reserved_points >= 0", name="ck_task_point_allocation_reserved"),
        CheckConstraint("settled_points >= 0", name="ck_task_point_allocation_settled"),
        CheckConstraint("released_points >= 0", name="ck_task_point_allocation_released"),
        CheckConstraint(
            "allocated_points = reserved_points + settled_points + released_points",
            name="ck_task_point_allocation_conservation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    lot_id: Mapped[str] = mapped_column(
        ForeignKey("company_point_lots.id", ondelete="RESTRICT"), nullable=False
    )
    allocated_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    released_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PersonalWalletAccount(TimestampMixin, Base):
    __tablename__ = "personal_wallet_accounts"
    __table_args__ = (
        CheckConstraint(
            "available_points >= 0", name="ck_personal_wallet_available_nonnegative"
        ),
        CheckConstraint(
            "reserved_points >= 0", name="ck_personal_wallet_reserved_nonnegative"
        ),
        CheckConstraint(
            "reversal_reserved_points >= 0",
            name="ck_personal_wallet_reversal_reserved_nonnegative",
        ),
        CheckConstraint(
            "debt_points >= 0", name="ck_personal_wallet_debt_nonnegative"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    available_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    reversal_reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    debt_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )


class PersonalPointLot(TimestampMixin, Base):
    """Source-aware personal points; legacy balances are never refundable."""

    __tablename__ = "personal_point_lots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_personal_point_lot_idempotency"
        ),
        Index(
            "ix_personal_point_lot_spend_order",
            "workspace_id",
            "expires_at",
            "created_at",
            "id",
        ),
        CheckConstraint("original_points > 0", name="ck_personal_point_lot_original"),
        CheckConstraint("available_points >= 0", name="ck_personal_point_lot_available"),
        CheckConstraint("reserved_points >= 0", name="ck_personal_point_lot_reserved"),
        CheckConstraint(
            "reversal_reserved_points >= 0",
            name="ck_personal_point_lot_reversal_reserved",
        ),
        CheckConstraint("settled_points >= 0", name="ck_personal_point_lot_settled"),
        CheckConstraint("reversed_points >= 0", name="ck_personal_point_lot_reversed"),
        CheckConstraint("cash_basis_cents >= 0", name="ck_personal_point_lot_cash_basis"),
        CheckConstraint(
            "receivable_basis_cents >= 0",
            name="ck_personal_point_lot_receivable_basis",
        ),
        CheckConstraint("subsidy_cents >= 0", name="ck_personal_point_lot_subsidy"),
        CheckConstraint(
            "cash_basis_cents + receivable_basis_cents + subsidy_cents "
            "= original_points * 10",
            name="ck_personal_point_lot_value_basis",
        ),
        CheckConstraint(
            "original_points = available_points + reserved_points + "
            "reversal_reserved_points + settled_points + reversed_points",
            name="ck_personal_point_lot_conservation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_kind: Mapped[PointLotSourceKind] = mapped_column(
        Enum(PointLotSourceKind, **enum_kwargs), nullable=False
    )
    original_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reversal_reserved_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    settled_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reversed_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    cash_basis_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    receivable_basis_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    subsidy_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    refundable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )


class PersonalTaskPointLotAllocation(Base):
    __tablename__ = "personal_task_point_lot_allocations"
    __table_args__ = (
        UniqueConstraint("task_id", "lot_id", name="uq_personal_task_point_lot_allocation"),
        Index("ix_personal_task_point_lot_allocation_task", "task_id", "id"),
        CheckConstraint("allocated_points > 0", name="ck_personal_task_point_allocation_total"),
        CheckConstraint("reserved_points >= 0", name="ck_personal_task_point_allocation_reserved"),
        CheckConstraint("settled_points >= 0", name="ck_personal_task_point_allocation_settled"),
        CheckConstraint("released_points >= 0", name="ck_personal_task_point_allocation_released"),
        CheckConstraint(
            "allocated_points = reserved_points + settled_points + released_points",
            name="ck_personal_task_point_allocation_conservation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    lot_id: Mapped[str] = mapped_column(
        ForeignKey("personal_point_lots.id", ondelete="RESTRICT"), nullable=False
    )
    allocated_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    released_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
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
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL AND ("
            "(billing_unit = 'CNY_CENT' AND billing_version = 1 "
            "AND quote_cents > 0 AND quote_points IS NULL "
            "AND reserved_points = 0 AND actual_cost_points IS NULL) OR "
            "(billing_unit = 'POINT' AND billing_version = 2 "
            "AND quote_cents IS NULL AND quote_points > 0 "
            "AND reserved_cents = 0 AND actual_cost_cents IS NULL))) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND billing_unit = 'POINT' AND billing_version = 2 "
            "AND quote_cents IS NULL AND quote_points > 0 "
            "AND reserved_cents = 0 AND actual_cost_cents IS NULL)",
            name="ck_task_scope_quote",
        ),
        CheckConstraint(
            "billing_version IN (1, 2)", name="ck_task_billing_version"
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
        CheckConstraint(
            "(provider_route_evidence IS NULL AND "
            "provider_route_evidence_sha256 IS NULL) OR "
            "(provider_route_evidence IS NOT NULL AND "
            "provider_route_evidence_sha256 IS NOT NULL)",
            name="ck_task_provider_route_evidence_complete",
        ),
        *_sha256_check_constraints(
            "provider_route_evidence_sha256",
            constraint_name="ck_task_provider_route_evidence_sha",
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
    billing_unit: Mapped[BillingUnit] = mapped_column(
        Enum(BillingUnit, **enum_kwargs),
        default=_default_generation_billing_unit,
        server_default=BillingUnit.CNY_CENT.value,
        nullable=False,
    )
    billing_version: Mapped[int] = mapped_column(
        Integer,
        default=_default_generation_billing_version,
        server_default="1",
        nullable=False,
    )
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
    provider_route_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    provider_route_evidence_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
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

def _immutable_json_value(value: Any) -> str:
    # Preserve JSON scalar types (True must not compare equal to 1) while
    # allowing harmless object-key ordering/formatting differences.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


@event.listens_for(GenerationTask, "before_update")
def _prevent_task_pricing_snapshot_update(mapper, connection, target) -> None:
    if not sa_inspect(target).attrs.pricing_snapshot.history.has_changes():
        return
    original = connection.scalar(select(GenerationTask.__table__.c.pricing_snapshot)
                                 .where(GenerationTask.__table__.c.id == target.id))
    if _immutable_json_value(original) != _immutable_json_value(target.pricing_snapshot):
        raise RuntimeError("task pricing snapshot is immutable")


@event.listens_for(GenerationTask, "before_delete")
def _prevent_task_quote_deletion(*_) -> None:
    raise RuntimeError("execution contract facts are durable")


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
        UniqueConstraint(
            "personal_workspace_id",
            "uploaded_by_user_id",
            "idempotency_key",
            name="uq_personal_input_asset_uploader_idempotency",
        ),
        Index(
            "ix_input_asset_company_status_created",
            "company_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_input_asset_personal_status_created",
            "personal_workspace_id",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_input_asset_scope",
        ),
        CheckConstraint(
            "source_task_artifact_id IS NULL OR idempotency_key IS NOT NULL",
            name="ck_input_asset_promotion_has_idempotency",
        ),
        CheckConstraint("size_bytes > 0", name="ck_input_asset_size_positive"),
        CheckConstraint(
            "media_metadata_version IS NULL OR media_metadata_version = 1",
            name="ck_input_asset_media_metadata_version",
        ),
        CheckConstraint(
            "(width_px IS NULL AND height_px IS NULL) OR "
            "(width_px > 0 AND height_px > 0)",
            name="ck_input_asset_dimensions_positive",
        ),
        CheckConstraint(
            "media_metadata_version IS NULL OR "
            "(width_px IS NOT NULL AND height_px IS NOT NULL AND "
            "((media_type = 'image' AND media_container IS NULL AND "
            "video_codec IS NULL AND video_fps IS NULL AND duration_ms IS NULL "
            "AND video_has_audio IS NULL) OR "
            "(media_type = 'video' AND media_container IS NOT NULL AND "
            "video_codec IS NOT NULL AND video_fps > 0 AND duration_ms > 0 "
            "AND video_has_audio IS NOT NULL)))",
            name="ck_input_asset_trusted_metadata_shape",
        ),
        CheckConstraint(
            "normalization_profile IS NULL OR "
            "(normalization_profile = 'director_previs_mp4_v1' AND "
            "source_sha256 IS NOT NULL AND source_sha256 <> sha256 AND "
            "media_metadata_version = 1 AND "
            "media_type = 'video' AND content_type = 'video/mp4' AND "
            "media_container = 'mp4' AND video_codec = 'h264' AND "
            "video_fps >= 23.99 AND video_fps <= 24.01 AND "
            "video_has_audio = false)",
            name="ck_input_asset_normalization_profile",
        ),
        *_sha256_check_constraints(
            "source_sha256",
            constraint_name="ck_input_asset_source_sha256",
            nullable=True,
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
    media_metadata_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    video_fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_has_audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    normalization_profile: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


class DirectorShotPackage(TimestampMixin, Base):
    """Immutable, scope-bound 3D composition evidence used by one or more tasks.

    The manifest deliberately contains no model bytes, data URLs, signed URLs,
    credentials, pricing, or provider controls.  Its composition image remains
    a normal private InputAsset and is independently linked to every task that
    consumes the package.
    """

    __tablename__ = "director_shot_packages"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_director_shot_package_company_idempotency",
        ),
        UniqueConstraint(
            "personal_workspace_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_director_shot_package_personal_idempotency",
        ),
        Index(
            "ix_director_shot_package_company_created",
            "company_id",
            "created_at",
        ),
        Index(
            "ix_director_shot_package_personal_created",
            "personal_workspace_id",
            "created_at",
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_director_shot_package_scope",
        ),
        CheckConstraint(
            "length(id) = 36 AND substr(id, 1, 4) = 'dsp_'",
            name="ck_director_shot_package_id",
        ),
        CheckConstraint(
            "schema_version IN (1, 2)",
            name="ck_director_shot_package_schema_version",
        ),
        *_sha256_check_constraints(
            "manifest_sha256",
            constraint_name="ck_director_shot_package_manifest_sha",
        ),
        *_sha256_check_constraints(
            "scene_revision_sha256",
            constraint_name="ck_director_shot_package_scene_revision_sha",
        ),
        *_sha256_check_constraints(
            "sealed_revision_sha256",
            constraint_name="ck_director_shot_package_sealed_revision_sha",
        ),
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_director_shot_package_request_fingerprint_sha",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_director_shot_package_id
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    composition_asset_id: Mapped[str] = mapped_column(
        ForeignKey("input_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(DirectorShotPackage, "before_update")
def _prevent_director_shot_package_update(*_) -> None:
    raise RuntimeError("director shot packages are immutable")


@event.listens_for(DirectorShotPackage, "before_delete")
def _prevent_director_shot_package_delete(*_) -> None:
    raise RuntimeError("director shot packages are immutable")


class TaskDirectorShotPackage(Base):
    """Immutable exact package/hash binding for a generation task."""

    __tablename__ = "task_director_shot_packages"
    __table_args__ = (
        Index("ix_task_director_shot_package_package", "package_id"),
        *_sha256_check_constraints(
            "manifest_sha256",
            constraint_name="ck_task_director_shot_package_manifest_sha",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    package_id: Mapped[str] = mapped_column(
        ForeignKey("director_shot_packages.id", ondelete="RESTRICT"), nullable=False
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


@event.listens_for(TaskDirectorShotPackage, "before_update")
def _prevent_task_director_shot_package_update(*_) -> None:
    raise RuntimeError("task director shot package bindings are immutable")


@event.listens_for(TaskDirectorShotPackage, "before_delete")
def _prevent_task_director_shot_package_delete(*_) -> None:
    raise RuntimeError("task director shot package bindings are immutable")


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


def _outbox_execution_fragment(payload: Any) -> tuple[bool, Any]:
    present = isinstance(payload, dict) and "execution_contract" in payload
    return present, payload["execution_contract"] if present else None


@event.listens_for(RelaySubmissionOutbox, "before_update")
def _prevent_outbox_execution_contract_update(mapper, connection, target) -> None:
    if not sa_inspect(target).attrs.relay_payload.history.has_changes():
        return
    original = connection.scalar(select(RelaySubmissionOutbox.__table__.c.relay_payload)
                                 .where(RelaySubmissionOutbox.__table__.c.id == target.id))
    if _immutable_json_value(_outbox_execution_fragment(original)) != _immutable_json_value(
        _outbox_execution_fragment(target.relay_payload)
    ):
        raise RuntimeError("outbox execution contract is immutable")


@event.listens_for(RelaySubmissionOutbox, "before_delete")
def _prevent_outbox_execution_contract_deletion(*_) -> None:
    raise RuntimeError("execution contract facts are durable")


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
        Index(
            "ix_relay_task_stage_provider_account",
            "provider_name",
            "provider_account_id",
            "occurred_at",
        ),
        CheckConstraint(
            "schema_version IN (1, 2)", name="ck_relay_task_stage_schema"
        ),
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
        CheckConstraint(
            "provider_identity_status IN "
            "('unassigned', 'bound', 'legacy_unknown')",
            name="ck_relay_task_stage_provider_identity_status",
        ),
        CheckConstraint(
            "(provider_identity_status = 'bound' AND schema_version = 2 "
            "AND route_id IS NOT NULL AND provider_name IS NOT NULL "
            "AND provider_account_id IS NOT NULL AND provider_channel_id > 0 "
            "AND provider_route_id = route_id AND provider_key_index >= 0 "
            "AND provider_key_fingerprint IS NOT NULL "
            "AND length(provider_key_fingerprint) = 64 "
            "AND provider_credential_version IS NOT NULL "
            "AND routing_release_sha256 IS NOT NULL "
            "AND length(routing_release_sha256) = 71) OR "
            "(provider_identity_status IN ('unassigned', 'legacy_unknown') "
            "AND provider_name IS NULL AND provider_account_id IS NULL "
            "AND provider_channel_id IS NULL AND provider_route_id IS NULL "
            "AND provider_key_index IS NULL "
            "AND provider_key_fingerprint IS NULL "
            "AND provider_credential_version IS NULL "
            "AND routing_release_sha256 IS NULL)",
            name="ck_relay_task_stage_provider_identity_complete",
        ),
        CheckConstraint(
            "provider_identity_status <> 'unassigned' OR route_id IS NULL",
            name="ck_relay_task_stage_unassigned_route",
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
    provider_identity_status: Mapped[str] = mapped_column(
        String(24), default="legacy_unknown", nullable=False
    )
    provider_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_account_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_route_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_key_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_key_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_credential_version: Mapped[str | None] = mapped_column(String(36), nullable=True)
    routing_release_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
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


@event.listens_for(CompanyPointLedgerEntry, "before_update")
def _prevent_company_point_ledger_entry_update(*_) -> None:
    raise RuntimeError("company point ledger entries are immutable")


@event.listens_for(CompanyPointLedgerEntry, "before_delete")
def _prevent_company_point_ledger_entry_delete(*_) -> None:
    raise RuntimeError("company point ledger entries are immutable")


@event.listens_for(CompanyPointPriceVersion, "before_update")
def _prevent_company_point_price_version_update(*_) -> None:
    raise RuntimeError("company point price versions are immutable")


@event.listens_for(CompanyPointPriceVersion, "before_delete")
def _prevent_company_point_price_version_delete(*_) -> None:
    raise RuntimeError("company point price versions are immutable")


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
            "kind IN ('RECHARGE', 'RESERVE', 'SETTLE', 'RELEASE', "
            "'REFUND_RESERVE', 'REFUND_SETTLE', 'REFUND_RELEASE', "
            "'CHARGEBACK', 'DISPUTE_REVERSAL', 'DEBT_RECOVERY')",
            name="ck_personal_ledger_kind",
        ),
        CheckConstraint(
            "(kind = 'RECHARGE' AND amount_points > 0 "
            "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'RESERVE' AND amount_points > 0 "
            "AND available_delta_points = -amount_points "
            "AND reserved_delta_points = amount_points "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'SETTLE' AND amount_points >= 0 "
            "AND available_delta_points >= 0 AND reserved_delta_points <= 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'RELEASE' AND amount_points > 0 "
            "AND available_delta_points = amount_points "
            "AND reserved_delta_points = -amount_points "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points = 0) OR "
            "(kind = 'REFUND_RESERVE' AND amount_points > 0 "
            "AND available_delta_points = -amount_points AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = amount_points AND debt_delta_points = 0) OR "
            "(kind = 'REFUND_SETTLE' AND amount_points > 0 "
            "AND available_delta_points = 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
            "(kind = 'REFUND_RELEASE' AND amount_points > 0 "
            "AND available_delta_points = amount_points AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = -amount_points AND debt_delta_points = 0) OR "
            "(kind = 'CHARGEBACK' AND amount_points > 0 "
            "AND available_delta_points <= 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points >= 0 "
            "AND -available_delta_points + debt_delta_points = amount_points) OR "
            "(kind = 'DISPUTE_REVERSAL' AND amount_points > 0 "
            "AND available_delta_points >= 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 AND debt_delta_points <= 0 "
            "AND available_delta_points - debt_delta_points = amount_points) OR "
            "(kind = 'DEBT_RECOVERY' AND amount_points > 0 "
            "AND available_delta_points = 0 AND reserved_delta_points = 0 "
            "AND reversal_reserved_delta_points = 0 "
            "AND debt_delta_points = -amount_points)",
            name="ck_personal_ledger_delta_shape",
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
    reversal_reserved_delta_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    debt_delta_points: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    payment_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    payment_refund_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_refunds.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    payment_dispute_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_disputes.id", ondelete="RESTRICT", use_alter=True),
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


class PaymentOrder(TimestampMixin, Base):
    """Provider-neutral cash intent; point fulfillment is a separate fact."""

    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key", name="uq_payment_order_company_idempotency"
        ),
        UniqueConstraint(
            "personal_workspace_id",
            "idempotency_key",
            name="uq_payment_order_personal_idempotency",
        ),
        UniqueConstraint(
            "provider",
            "merchant_account",
            "provider_order_id",
            name="uq_payment_order_provider_order",
        ),
        Index("ix_payment_order_status_created", "status", "created_at", "id"),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_payment_order_scope",
        ),
        CheckConstraint("currency = 'CNY'", name="ck_payment_order_currency"),
        CheckConstraint("amount_cents > 0", name="ck_payment_order_amount"),
        CheckConstraint("points >= 0", name="ck_payment_order_points"),
        CheckConstraint(
            "(purpose = 'POINT_PURCHASE' AND points > 0 "
            "AND purpose_reference_id IS NULL) OR "
            "(purpose = 'INVOICE_PAYMENT' AND points = 0 "
            "AND purpose_reference_id IS NOT NULL)",
            name="ck_payment_order_purpose",
        ),
        CheckConstraint(
            "captured_amount_cents >= 0 AND refunded_amount_cents >= 0 "
            "AND disputed_amount_cents >= 0 AND fee_amount_cents >= 0 "
            "AND captured_amount_cents <= amount_cents "
            "AND refunded_amount_cents + disputed_amount_cents "
            "<= captured_amount_cents",
            name="ck_payment_order_amount_totals",
        ),
        CheckConstraint(
            "(automatic = true AND payment_mandate_id IS NOT NULL "
            "AND provider_customer_reference IS NOT NULL) OR "
            "(automatic = false AND payment_mandate_id IS NULL)",
            name="ck_payment_order_automatic_mandate",
        ),
        *_sha256_check_constraints(
            "request_fingerprint", constraint_name="ck_payment_order_fingerprint_sha256"
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
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    purpose: Mapped[PaymentPurpose] = mapped_column(
        Enum(PaymentPurpose, **enum_kwargs), nullable=False
    )
    purpose_reference_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[PaymentOrderStatus] = mapped_column(
        Enum(PaymentOrderStatus, **enum_kwargs),
        default=PaymentOrderStatus.CREATED,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    captured_amount_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    refunded_amount_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    disputed_amount_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    fee_amount_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    checkout_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    provider_customer_reference: Mapped[str | None] = mapped_column(
        String(240), nullable=True
    )
    payment_mandate_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_mandates.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentAttempt(TimestampMixin, Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("order_id", "sequence", name="uq_payment_attempt_sequence"),
        UniqueConstraint(
            "provider", "provider_attempt_id", name="uq_payment_attempt_provider_id"
        ),
        UniqueConstraint("idempotency_key", name="uq_payment_attempt_idempotency"),
        CheckConstraint("sequence > 0", name="ck_payment_attempt_sequence_positive"),
        *_sha256_check_constraints(
            "request_sha256", constraint_name="ck_payment_attempt_request_sha256"
        ),
        *_sha256_check_constraints(
            "response_sha256",
            constraint_name="ck_payment_attempt_response_sha256",
            nullable=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_attempt_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[PaymentAttemptStatus] = mapped_column(
        Enum(PaymentAttemptStatus, **enum_kwargs), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentRefund(TimestampMixin, Base):
    __tablename__ = "payment_refunds"
    __table_args__ = (
        UniqueConstraint("order_id", "idempotency_key", name="uq_payment_refund_order_key"),
        UniqueConstraint(
            "provider", "provider_refund_id", name="uq_payment_refund_provider_id"
        ),
        CheckConstraint("amount_cents > 0", name="ck_payment_refund_amount"),
        CheckConstraint("points >= 0", name="ck_payment_refund_points"),
        CheckConstraint("currency = 'CNY'", name="ck_payment_refund_currency"),
        *_sha256_check_constraints(
            "request_fingerprint", constraint_name="ck_payment_refund_fingerprint_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[PaymentRefundStatus] = mapped_column(
        Enum(PaymentRefundStatus, **enum_kwargs), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentDispute(TimestampMixin, Base):
    __tablename__ = "payment_disputes"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_dispute_id", name="uq_payment_dispute_provider_id"
        ),
        CheckConstraint("amount_cents > 0", name="ck_payment_dispute_amount"),
        CheckConstraint(
            "points >= 0 AND recovered_available_points >= 0 "
            "AND debt_points >= 0 "
            "AND recovered_available_points + debt_points = points",
            name="ck_payment_dispute_points",
        ),
        CheckConstraint("currency = 'CNY'", name="ck_payment_dispute_currency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_dispute_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[PaymentDisputeStatus] = mapped_column(
        Enum(PaymentDisputeStatus, **enum_kwargs), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    recovered_available_points: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    debt_points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentProviderCommand(TimestampMixin, Base):
    """Durable provider operation; identity is committed before network I/O."""

    __tablename__ = "payment_provider_commands"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_provider_command_key"),
        UniqueConstraint("dedupe_key", name="uq_payment_provider_command_dedupe"),
        Index(
            "ix_payment_provider_command_dispatch",
            "status",
            "next_attempt_at",
            "created_at",
            "id",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_payment_provider_command_attempts"),
        CheckConstraint(
            "(operation IN ('CREATE_PAYMENT', 'QUERY_PAYMENT') "
            "AND order_id IS NOT NULL AND refund_id IS NULL) OR "
            "(operation IN ('CREATE_REFUND', 'QUERY_REFUND') "
            "AND order_id IS NOT NULL AND refund_id IS NOT NULL)",
            name="ck_payment_provider_command_target",
        ),
        CheckConstraint(
            "(status = 'CLAIMED' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'CLAIMED')",
            name="ck_payment_provider_command_lease",
        ),
        *_sha256_check_constraints(
            "request_sha256", constraint_name="ck_payment_provider_command_request_sha256"
        ),
        *_sha256_check_constraints(
            "response_sha256",
            constraint_name="ck_payment_provider_command_response_sha256",
            nullable=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation: Mapped[PaymentProviderCommandOperation] = mapped_column(
        Enum(PaymentProviderCommandOperation, **enum_kwargs), nullable=False
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    refund_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_refunds.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PaymentProviderCommandStatus] = mapped_column(
        Enum(PaymentProviderCommandStatus, **enum_kwargs),
        default=PaymentProviderCommandStatus.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lease_token: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_resource_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentWebhookInboxEvent(TimestampMixin, Base):
    """Verified event inbox whose delivery state may be retried independently."""

    __tablename__ = "payment_webhook_inbox_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "merchant_account",
            "provider_event_id",
            name="uq_payment_webhook_inbox_provider_event",
        ),
        Index(
            "ix_payment_webhook_inbox_replay",
            "status",
            "next_attempt_at",
            "received_at",
            "id",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_payment_webhook_inbox_attempts"),
        CheckConstraint(
            "(status = 'PROCESSING' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'PROCESSING')",
            name="ck_payment_webhook_inbox_lease",
        ),
        *_sha256_check_constraints(
            "payload_sha256", constraint_name="ck_payment_webhook_inbox_payload_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    signature_key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    signature_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    signature_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[PaymentWebhookInboxStatus] = mapped_column(
        Enum(PaymentWebhookInboxStatus, **enum_kwargs),
        default=PaymentWebhookInboxStatus.RECEIVED,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lease_token: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    blocked_on: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    processed_receipt_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "payment_webhook_receipts.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_payment_webhook_inbox_receipt",
        ),
        nullable=True,
        unique=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentWebhookReceipt(Base):
    __tablename__ = "payment_webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "provider", "merchant_account", "provider_event_id",
            name="uq_payment_webhook_provider_event",
        ),
        Index("ix_payment_webhook_received", "received_at", "id"),
        *_sha256_check_constraints(
            "payload_sha256", constraint_name="ck_payment_webhook_payload_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    signature_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    outcome: Mapped[PaymentWebhookOutcome] = mapped_column(
        Enum(PaymentWebhookOutcome, **enum_kwargs), nullable=False
    )
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    refund_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_refunds.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    dispute_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_disputes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_transaction_id",
            name="uq_payment_transaction_provider_id",
        ),
        Index("ix_payment_transaction_order_created", "order_id", "created_at", "id"),
        CheckConstraint("amount_cents > 0", name="ck_payment_transaction_amount"),
        CheckConstraint("currency = 'CNY'", name="ck_payment_transaction_currency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    refund_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_refunds.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    dispute_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_disputes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    webhook_receipt_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_webhook_receipts.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[PaymentTransactionKind] = mapped_column(
        Enum(PaymentTransactionKind, **enum_kwargs), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PaymentDisputeDebtRecoveryAllocation(Base):
    """Immutable attribution of a later paid purchase to one dispute debt."""

    __tablename__ = "payment_dispute_debt_recovery_allocations"
    __table_args__ = (
        UniqueConstraint(
            "dispute_id",
            "recovery_payment_transaction_id",
            name="uq_payment_dispute_debt_recovery_transaction",
        ),
        UniqueConstraint("idempotency_key", name="uq_payment_dispute_debt_recovery_key"),
        Index(
            "ix_dispute_debt_recovery_transaction",
            "recovery_payment_transaction_id",
        ),
        Index(
            "ix_dispute_debt_recovery_personal",
            "personal_workspace_id",
        ),
        CheckConstraint("recovered_points > 0", name="ck_payment_dispute_debt_recovery_points"),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_payment_dispute_debt_recovery_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dispute_id: Mapped[str] = mapped_column(
        ForeignKey("payment_disputes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    recovery_payment_transaction_id: Mapped[str] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"), nullable=True
    )
    recovered_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PaymentDisputeDebtRecoveryReversal(Base):
    """Immutable compensation proving a recovered debt allocation was restored."""

    __tablename__ = "payment_dispute_debt_recovery_reversals"
    __table_args__ = (
        UniqueConstraint("allocation_id", name="uq_payment_dispute_debt_reversal_allocation"),
        UniqueConstraint("idempotency_key", name="uq_payment_dispute_debt_reversal_key"),
        CheckConstraint("restored_points > 0", name="ck_payment_dispute_debt_reversal_points"),
        CheckConstraint(
            "(company_ledger_entry_id IS NOT NULL AND personal_ledger_entry_id IS NULL "
            "AND company_point_lot_id IS NOT NULL AND personal_point_lot_id IS NULL) OR "
            "(company_ledger_entry_id IS NULL AND personal_ledger_entry_id IS NOT NULL "
            "AND company_point_lot_id IS NULL AND personal_point_lot_id IS NOT NULL)",
            name="ck_payment_dispute_debt_reversal_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    allocation_id: Mapped[str] = mapped_column(
        ForeignKey("payment_dispute_debt_recovery_allocations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dispute_id: Mapped[str] = mapped_column(
        ForeignKey("payment_disputes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    restored_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company_ledger_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_point_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    personal_ledger_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    company_point_lot_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_point_lots.id", ondelete="RESTRICT"), nullable=True
    )
    personal_point_lot_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_point_lots.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PaymentMandate(TimestampMixin, Base):
    """Server-owned consent and token binding for off-session payments."""

    __tablename__ = "payment_mandates"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_mandate_key"),
        UniqueConstraint(
            "provider",
            "merchant_account",
            "provider_mandate_reference",
            name="uq_payment_mandate_provider_reference",
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_payment_mandate_scope",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND provider_customer_reference IS NOT NULL "
            "AND provider_payment_method_reference IS NOT NULL "
            "AND provider_mandate_reference IS NOT NULL "
            "AND consented_at IS NOT NULL AND verified_at IS NOT NULL "
            "AND revoked_at IS NULL) OR status <> 'ACTIVE'",
            name="ck_payment_mandate_active_evidence",
        ),
        *_sha256_check_constraints(
            "consent_sha256", constraint_name="ck_payment_mandate_consent_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[PaymentMandateStatus] = mapped_column(
        Enum(PaymentMandateStatus, **enum_kwargs), nullable=False
    )
    provider_customer_reference: Mapped[str | None] = mapped_column(String(240), nullable=True)
    provider_payment_method_reference: Mapped[str | None] = mapped_column(
        String(240), nullable=True
    )
    provider_mandate_reference: Mapped[str | None] = mapped_column(String(240), nullable=True)
    consent_version: Mapped[str] = mapped_column(String(80), nullable=False)
    consent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)


class AutoRechargeRule(TimestampMixin, Base):
    __tablename__ = "auto_recharge_rules"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_auto_recharge_company"),
        UniqueConstraint("personal_workspace_id", name="uq_auto_recharge_personal"),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_auto_recharge_scope",
        ),
        CheckConstraint("threshold_points >= 0", name="ck_auto_recharge_threshold"),
        CheckConstraint("top_up_points > 0", name="ck_auto_recharge_top_up"),
        CheckConstraint("monthly_cap_cents > 0", name="ck_auto_recharge_monthly_cap"),
        CheckConstraint("cooldown_seconds >= 60", name="ck_auto_recharge_cooldown"),
        Index("ix_auto_recharge_due", "enabled", "next_check_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    personal_workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_customer_reference: Mapped[str] = mapped_column(String(240), nullable=False)
    mandate_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_mandates.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    threshold_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    top_up_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    monthly_cap_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutoRechargeExecution(Base):
    __tablename__ = "auto_recharge_executions"
    __table_args__ = (
        UniqueConstraint("rule_id", "trigger_key", name="uq_auto_recharge_trigger"),
        CheckConstraint("observed_available_points >= 0", name="ck_auto_recharge_observed"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("auto_recharge_rules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    trigger_key: Mapped[str] = mapped_column(String(160), nullable=False)
    observed_available_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PointLotSettlementValueAllocation(Base):
    """Immutable value attribution for one task-lot settlement."""

    __tablename__ = "point_lot_settlement_value_allocations"
    __table_args__ = (
        UniqueConstraint(
            "company_task_allocation_id", name="uq_point_value_company_allocation"
        ),
        UniqueConstraint(
            "personal_task_allocation_id", name="uq_point_value_personal_allocation"
        ),
        CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL "
            "AND company_task_allocation_id IS NOT NULL "
            "AND personal_task_allocation_id IS NULL "
            "AND company_settle_ledger_id IS NOT NULL "
            "AND personal_settle_ledger_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND company_task_allocation_id IS NULL "
            "AND personal_task_allocation_id IS NOT NULL "
            "AND company_settle_ledger_id IS NULL "
            "AND personal_settle_ledger_id IS NOT NULL)",
            name="ck_point_value_allocation_scope",
        ),
        CheckConstraint("settled_points > 0", name="ck_point_value_settled_points"),
        CheckConstraint(
            "cash_basis_cents >= 0 AND receivable_basis_cents >= 0 "
            "AND subsidy_cents >= 0",
            name="ck_point_value_nonnegative",
        ),
        CheckConstraint(
            "cash_basis_cents + receivable_basis_cents + subsidy_cents "
            "= settled_points * 10",
            name="ck_point_value_conservation",
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
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_task_allocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_point_lot_allocations.id", ondelete="RESTRICT"), nullable=True
    )
    personal_task_allocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_task_point_lot_allocations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    company_settle_ledger_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_point_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    personal_settle_ledger_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    settled_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cash_basis_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receivable_basis_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subsidy_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CompanyBillingContractVersion(Base):
    __tablename__ = "company_billing_contract_versions"
    __table_args__ = (
        UniqueConstraint("content_sha256", name="uq_company_billing_contract_content"),
        Index("ix_company_billing_contract_company_effective", "company_id", "effective_at"),
        CheckConstraint("currency = 'CNY'", name="ck_company_billing_contract_currency"),
        CheckConstraint("cycle_day BETWEEN 1 AND 28", name="ck_company_billing_cycle_day"),
        CheckConstraint(
            "payment_terms_days BETWEEN 0 AND 180", name="ck_company_billing_terms"
        ),
        CheckConstraint("credit_limit_points > 0", name="ck_company_billing_credit_limit"),
        CheckConstraint(
            "receivable_per_point_cents = 10",
            name="ck_company_billing_point_anchor",
        ),
        *_sha256_check_constraints(
            "content_sha256", constraint_name="ck_company_billing_contract_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[EnterpriseContractStatus] = mapped_column(
        Enum(EnterpriseContractStatus, **enum_kwargs), nullable=False
    )
    contract_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai", nullable=False)
    cycle_day: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_limit_points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receivable_per_point_cents: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "company_billing_contract_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
        unique=True,
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class CompanyBillingAccount(TimestampMixin, Base):
    __tablename__ = "company_billing_accounts"
    __table_args__ = (
        CheckConstraint("unbilled_receivable_cents >= 0", name="ck_company_unbilled_receivable"),
        CheckConstraint("dunning_level >= 0", name="ck_company_dunning_level"),
        CheckConstraint(
            "(billing_hold = false AND billing_hold_since IS NULL "
            "AND billing_hold_reason IS NULL AND dunning_level = 0) OR billing_hold = true",
            name="ck_company_billing_hold_evidence",
        ),
    )

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    active_contract_version_id: Mapped[str] = mapped_column(
        ForeignKey("company_billing_contract_versions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    unbilled_receivable_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    billing_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    billing_hold_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    billing_hold_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dunning_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CompanyBillingCycle(TimestampMixin, Base):
    __tablename__ = "company_billing_cycles"
    __table_args__ = (
        UniqueConstraint("company_id", "period_start", "period_end", name="uq_company_billing_period"),
        CheckConstraint("period_end > period_start", name="ck_company_billing_period_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contract_version_id: Mapped[str] = mapped_column(
        ForeignKey("company_billing_contract_versions.id", ondelete="RESTRICT"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[EnterpriseBillingCycleStatus] = mapped_column(
        Enum(EnterpriseBillingCycleStatus, **enum_kwargs), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompanyInvoice(TimestampMixin, Base):
    __tablename__ = "company_invoices"
    __table_args__ = (
        UniqueConstraint("cycle_id", name="uq_company_invoice_cycle"),
        UniqueConstraint("invoice_number", name="uq_company_invoice_number"),
        CheckConstraint(
            "subtotal_cents >= 0 AND credit_cents >= 0 AND tax_cents >= 0 "
            "AND total_cents >= 0 AND paid_cents >= 0 "
            "AND paid_cents <= total_cents",
            name="ck_company_invoice_totals_nonnegative",
        ),
        CheckConstraint(
            "total_cents = subtotal_cents - credit_cents + tax_cents",
            name="ck_company_invoice_total",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("company_billing_cycles.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[EnterpriseInvoiceStatus] = mapped_column(
        Enum(EnterpriseInvoiceStatus, **enum_kwargs), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credit_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    paid_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompanyInvoiceLine(Base):
    __tablename__ = "company_invoice_lines"
    __table_args__ = (
        UniqueConstraint(
            "value_allocation_id", name="uq_company_invoice_value_allocation"
        ),
        CheckConstraint("points > 0", name="ck_company_invoice_line_points"),
        CheckConstraint("amount_cents >= 0", name="ck_company_invoice_line_amount"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("company_invoices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    value_allocation_id: Mapped[str] = mapped_column(
        ForeignKey("point_lot_settlement_value_allocations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_version_id: Mapped[str] = mapped_column(
        ForeignKey("company_billing_contract_versions.id", ondelete="RESTRICT"), nullable=False
    )
    points: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AccountsReceivableLedgerEntry(Base):
    __tablename__ = "accounts_receivable_ledger_entries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_accounts_receivable_idempotency"),
        CheckConstraint(
            "debit_cents >= 0 AND credit_cents >= 0 "
            "AND ((debit_cents > 0 AND credit_cents = 0) OR "
            "(debit_cents = 0 AND credit_cents > 0))",
            name="ck_accounts_receivable_entry_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_invoices.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    payment_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="RESTRICT"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    debit_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credit_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    note: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class EnterpriseDunningRun(Base):
    __tablename__ = "enterprise_dunning_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_enterprise_dunning_run_key"),
        *_sha256_check_constraints(
            "intent_sha256", constraint_name="ck_enterprise_dunning_intent_sha256"
        ),
        CheckConstraint(
            "scanned_count >= 0 AND overdue_count >= 0 AND hold_count >= 0",
            name="ck_enterprise_dunning_run_counts",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[EnterpriseDunningRunStatus] = mapped_column(
        Enum(EnterpriseDunningRunStatus, **enum_kwargs), nullable=False
    )
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overdue_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hold_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnterpriseDunningAction(Base):
    __tablename__ = "enterprise_dunning_actions"
    __table_args__ = (
        UniqueConstraint(
            "invoice_id", "action", "stage", name="uq_enterprise_dunning_invoice_action"
        ),
        CheckConstraint("stage >= 1", name="ck_enterprise_dunning_action_stage"),
        CheckConstraint(
            "action IN ('mark_overdue','apply_hold','clear_hold')",
            name="ck_enterprise_dunning_action_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("enterprise_dunning_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("company_invoices.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PaymentSettlementBatch(Base):
    """Immutable source bytes and parsed control totals; authenticity is separate."""

    __tablename__ = "payment_settlement_batches"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "merchant_account",
            "source_kind",
            "source_document_sha256",
            name="uq_payment_settlement_batch_document",
        ),
        UniqueConstraint(
            "provider",
            "merchant_account",
            "source_kind",
            "lines_sha256",
            name="uq_payment_settlement_batch_lines",
        ),
        CheckConstraint("period_end > period_start", name="ck_payment_settlement_batch_period"),
        CheckConstraint("currency = 'CNY'", name="ck_payment_settlement_batch_currency"),
        CheckConstraint(
            "line_count > 0 AND source_size_bytes > 0 AND fee_total_cents >= 0",
            name="ck_payment_settlement_batch_counts",
        ),
        CheckConstraint(
            "length(source_document_bytes) = source_size_bytes "
            "AND source_size_bytes <= 52428800",
            name="ck_payment_settlement_source_bytes",
        ),
        CheckConstraint(
            "net_total_cents = gross_total_cents - fee_total_cents",
            name="ck_payment_settlement_batch_totals",
        ),
        *_sha256_check_constraints(
            "source_document_sha256",
            constraint_name="ck_payment_settlement_batch_document_sha256",
        ),
        *_sha256_check_constraints(
            "lines_sha256", constraint_name="ck_payment_settlement_batch_lines_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    source_kind: Mapped[PaymentSettlementSourceKind] = mapped_column(
        Enum(PaymentSettlementSourceKind, **enum_kwargs), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_document_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_document_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_object_version: Mapped[str] = mapped_column(String(160), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verification_method: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    lines_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PaymentSettlementEntry(Base):
    __tablename__ = "payment_settlement_entries"
    __table_args__ = (
        UniqueConstraint(
            "provider", "merchant_account", "provider_line_id",
            name="uq_payment_settlement_provider_line",
        ),
        CheckConstraint("currency = 'CNY'", name="ck_payment_settlement_currency"),
        CheckConstraint(
            "line_type IN ('capture','refund','chargeback','dispute_reversal',"
            "'fee','payout','bank_deposit')",
            name="ck_payment_settlement_line_type",
        ),
        CheckConstraint(
            "fee_amount_cents >= 0 "
            "AND net_amount_cents = gross_amount_cents - fee_amount_cents",
            name="ck_payment_settlement_amounts",
        ),
        *_sha256_check_constraints(
            "source_document_sha256",
            constraint_name="ck_payment_settlement_document_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("payment_settlement_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_account: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_line_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    related_provider_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    line_type: Mapped[str] = mapped_column(String(40), nullable=False)
    gross_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_amount_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    net_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ProviderCostStatementBatch(Base):
    """Immutable supplier invoice/usage statement control record."""

    __tablename__ = "provider_cost_statement_batches"
    __table_args__ = (
        UniqueConstraint(
            "supplier",
            "supplier_account",
            "source_document_sha256",
            name="uq_provider_cost_batch_document",
        ),
        UniqueConstraint(
            "supplier",
            "supplier_account",
            "lines_sha256",
            name="uq_provider_cost_batch_lines",
        ),
        CheckConstraint("period_end > period_start", name="ck_provider_cost_batch_period"),
        CheckConstraint("currency = 'CNY'", name="ck_provider_cost_batch_currency"),
        CheckConstraint(
            "line_count > 0 AND source_size_bytes > 0 AND total_cost_cents >= 0",
            name="ck_provider_cost_batch_totals",
        ),
        CheckConstraint(
            "length(source_document_bytes) = source_size_bytes "
            "AND source_size_bytes <= 52428800",
            name="ck_provider_cost_statement_source_bytes",
        ),
        *_sha256_check_constraints(
            "source_document_sha256",
            constraint_name="ck_provider_cost_batch_document_sha256",
        ),
        *_sha256_check_constraints(
            "lines_sha256", constraint_name="ck_provider_cost_batch_lines_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier: Mapped[str] = mapped_column(String(120), nullable=False)
    supplier_account: Mapped[str] = mapped_column(String(160), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_document_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_document_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_object_version: Mapped[str] = mapped_column(String(160), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verification_method: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    lines_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ProviderCostStatementLine(Base):
    __tablename__ = "provider_cost_statement_lines"
    __table_args__ = (
        UniqueConstraint(
            "supplier",
            "supplier_account",
            "provider_line_id",
            name="uq_provider_cost_statement_line",
        ),
        CheckConstraint("amount_cents >= 0", name="ck_provider_cost_statement_amount"),
        CheckConstraint("currency = 'CNY'", name="ck_provider_cost_statement_currency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("provider_cost_statement_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    supplier: Mapped[str] = mapped_column(String(120), nullable=False)
    supplier_account: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_line_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_job_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FinanceReconciliationRun(Base):
    __tablename__ = "finance_reconciliation_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_finance_reconciliation_key"),
        CheckConstraint("period_end > period_start", name="ck_finance_reconciliation_period"),
        *_sha256_check_constraints(
            "snapshot_sha256",
            constraint_name="ck_finance_reconciliation_snapshot_sha256",
            nullable=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merchant_account: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[ReconciliationRunStatus] = mapped_column(
        Enum(ReconciliationRunStatus, **enum_kwargs), nullable=False
    )
    source_watermarks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    control_totals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FinanceReconciliationRunSource(Base):
    """Immutable binding from one conclusion to the exact imported batches."""

    __tablename__ = "finance_reconciliation_run_sources"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "payment_settlement_batch_id",
            name="uq_finance_reconciliation_run_payment_batch",
        ),
        UniqueConstraint(
            "run_id",
            "provider_cost_batch_id",
            name="uq_finance_reconciliation_run_cost_batch",
        ),
        CheckConstraint(
            "(source_kind = 'payment_settlement' "
            "AND payment_settlement_batch_id IS NOT NULL "
            "AND provider_cost_batch_id IS NULL) OR "
            "(source_kind = 'provider_cost' "
            "AND payment_settlement_batch_id IS NULL "
            "AND provider_cost_batch_id IS NOT NULL)",
            name="ck_finance_reconciliation_run_source_kind",
        ),
        *_sha256_check_constraints(
            "document_sha256",
            constraint_name="ck_finance_reconciliation_run_source_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("finance_reconciliation_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_settlement_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_settlement_batches.id", ondelete="RESTRICT"), nullable=True
    )
    provider_cost_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_cost_statement_batches.id", ondelete="RESTRICT"), nullable=True
    )
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FinanceReconciliationSnapshot(Base):
    __tablename__ = "finance_reconciliation_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "dimension", name="uq_finance_reconciliation_dimension"),
        *_sha256_check_constraints(
            "evidence_sha256", constraint_name="ck_finance_snapshot_evidence_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("finance_reconciliation_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[ReconciliationDimensionStatus] = mapped_column(
        Enum(ReconciliationDimensionStatus, **enum_kwargs), nullable=False
    )
    totals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FinanceReconciliationException(Base):
    __tablename__ = "finance_reconciliation_exceptions"
    __table_args__ = (
        Index("ix_finance_reconciliation_exception_run", "run_id", "dimension", "code"),
        *_sha256_check_constraints(
            "evidence_sha256", constraint_name="ck_finance_exception_evidence_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("finance_reconciliation_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[str] = mapped_column(String(24), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expected_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actual_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FinanceReconciliationResolution(Base):
    __tablename__ = "finance_reconciliation_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "exception_id", "idempotency_key", name="uq_finance_resolution_key"
        ),
        *_sha256_check_constraints(
            "evidence_sha256", constraint_name="ck_finance_resolution_evidence_sha256"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    exception_id: Mapped[str] = mapped_column(
        ForeignKey("finance_reconciliation_exceptions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


def _prevent_commercial_fact_mutation(*_) -> None:
    raise RuntimeError("commercial billing facts are immutable")


for _immutable_commercial_model in (
    PaymentDisputeDebtRecoveryAllocation,
    PaymentDisputeDebtRecoveryReversal,
    PaymentWebhookReceipt,
    PaymentTransaction,
    PointLotSettlementValueAllocation,
    CompanyBillingContractVersion,
    CompanyInvoiceLine,
    AccountsReceivableLedgerEntry,
    EnterpriseDunningAction,
    PaymentSettlementBatch,
    PaymentSettlementEntry,
    ProviderCostStatementBatch,
    ProviderCostStatementLine,
    FinanceReconciliationRunSource,
    FinanceReconciliationSnapshot,
    FinanceReconciliationException,
    FinanceReconciliationResolution,
):
    event.listen(_immutable_commercial_model, "before_update", _prevent_commercial_fact_mutation)
    event.listen(_immutable_commercial_model, "before_delete", _prevent_commercial_fact_mutation)


_PAYMENT_DELIVERY_IDENTITY_FIELDS = {
    PaymentProviderCommand: frozenset({
        "id", "operation", "order_id", "refund_id", "provider", "merchant_account",
        "idempotency_key", "dedupe_key", "request_payload", "request_sha256", "created_at",
    }),
    PaymentWebhookInboxEvent: frozenset({
        "id", "provider", "merchant_account", "provider_event_id", "event_type",
        "payload_sha256", "payload_json", "signature_key_id", "signature_timestamp",
        "signature_verified_at", "provider_occurred_at", "received_at", "created_at",
    }),
}


def _guard_payment_delivery_identity(_, __, target) -> None:
    identity_fields = _PAYMENT_DELIVERY_IDENTITY_FIELDS[type(target)]
    if any(sa_inspect(target).attrs[name].history.has_changes() for name in identity_fields):
        raise RuntimeError("payment delivery identity is immutable")


def _prevent_payment_delivery_delete(*_) -> None:
    raise RuntimeError("payment delivery records are durable")


for _payment_delivery_model in _PAYMENT_DELIVERY_IDENTITY_FIELDS:
    event.listen(_payment_delivery_model, "before_update", _guard_payment_delivery_identity)
    event.listen(_payment_delivery_model, "before_delete", _prevent_payment_delivery_delete)


@event.listens_for(FinanceReconciliationRun, "before_update")
def _guard_finance_reconciliation_finalization(_, __, target: FinanceReconciliationRun) -> None:
    state = sa_inspect(target)
    changed = {
        attribute.key
        for attribute in state.attrs
        if attribute.history.has_changes()
    }
    allowed = {"status", "control_totals", "snapshot_sha256", "completed_at"}
    if changed - allowed:
        raise RuntimeError("finance reconciliation identity is immutable")
    history = state.attrs.status.history
    old_status = history.deleted[0] if history.deleted else target.status
    if old_status != ReconciliationRunStatus.RUNNING:
        raise RuntimeError("finance reconciliation conclusion is immutable")


@event.listens_for(FinanceReconciliationRun, "before_delete")
def _prevent_finance_reconciliation_run_delete(*_) -> None:
    raise RuntimeError("finance reconciliation runs are immutable")


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
        Index(
            "ix_channel_cost_provider_account",
            "provider_name",
            "provider_account_id",
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
        CheckConstraint(
            "schema_version IN (1, 2)", name="ck_channel_cost_schema"
        ),
        CheckConstraint(
            "provider_identity_status IN "
            "('unassigned', 'bound', 'legacy_unknown')",
            name="ck_channel_cost_provider_identity_status",
        ),
        CheckConstraint(
            "(provider_identity_status = 'bound' AND schema_version = 2 "
            "AND route_id > 0 AND provider_name IS NOT NULL "
            "AND provider_account_id IS NOT NULL AND provider_channel_id > 0 "
            "AND provider_route_id = route_id AND provider_key_index >= 0 "
            "AND provider_key_fingerprint IS NOT NULL "
            "AND length(provider_key_fingerprint) = 64 "
            "AND provider_credential_version IS NOT NULL "
            "AND routing_release_sha256 IS NOT NULL "
            "AND length(routing_release_sha256) = 71) OR "
            "(provider_identity_status IN ('unassigned', 'legacy_unknown') "
            "AND provider_name IS NULL AND provider_account_id IS NULL "
            "AND provider_channel_id IS NULL AND provider_route_id IS NULL "
            "AND provider_key_index IS NULL "
            "AND provider_key_fingerprint IS NULL "
            "AND provider_credential_version IS NULL "
            "AND routing_release_sha256 IS NULL)",
            name="ck_channel_cost_provider_identity_complete",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(
        Enum(ChannelType, **enum_kwargs), nullable=False
    )
    route_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_identity_status: Mapped[str] = mapped_column(
        String(24), default="unassigned", nullable=False
    )
    provider_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_account_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_route_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_key_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_key_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_credential_version: Mapped[str | None] = mapped_column(String(36), nullable=True)
    routing_release_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
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

    @property
    def provider_key_fingerprint_prefix(self) -> str | None:
        if self.provider_key_fingerprint is None:
            return None
        return self.provider_key_fingerprint[:12]


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
            "AND personal_ledger_entry_id IS NULL AND ("
            "(released_cents = 0 AND released_points = 0 "
            "AND (ledger_entry_id IS NULL "
            "OR company_point_ledger_entry_id IS NULL)) OR "
            "(released_cents > 0 AND released_points = 0 "
            "AND ledger_entry_id IS NOT NULL "
            "AND company_point_ledger_entry_id IS NULL) OR "
            "(released_cents = 0 AND released_points > 0 "
            "AND ledger_entry_id IS NULL "
            "AND company_point_ledger_entry_id IS NOT NULL))) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL "
            "AND released_cents = 0 AND ledger_entry_id IS NULL "
            "AND company_point_ledger_entry_id IS NULL)",
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
    company_point_ledger_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("company_point_ledger_entries.id", ondelete="RESTRICT"),
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
        Index("ix_audit_system_actor_created", "actor_key", "created_at"),
        CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL "
            "AND actor_key IS NULL) OR "
            "(actor_kind = 'system' AND actor_user_id IS NULL "
            "AND actor_key IS NOT NULL)",
            name="ck_audit_log_actor_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    actor_kind: Mapped[str] = mapped_column(
        String(16), default="user", server_default="user", nullable=False
    )
    actor_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True
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
