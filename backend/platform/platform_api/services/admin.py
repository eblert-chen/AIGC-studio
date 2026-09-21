from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..models import (
    BillingUnit,
    Company,
    CompanyMembership,
    CompanyModelGrant,
    CompanyResourceGrant,
    CompanyStatus,
    ModelDefinition,
    ResourceDefinition,
    User,
    UserAccountType,
)
from .companies import CompanyService
from .errors import ConflictError, NotFoundError


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _next_platform_admin_timestamp(session: Session) -> datetime:
    """Keep development bootstrap ownership ordered even on a frozen clock."""

    candidate = datetime.now(timezone.utc)
    latest = _as_utc(
        session.scalar(
            select(func.max(User.created_at)).where(
                User.is_platform_admin.is_(True)
            )
        )
    )
    if latest is not None and candidate <= latest:
        return latest + timedelta(microseconds=1)
    return candidate


class PlatformAdminService:
    @staticmethod
    def bootstrap_admin(
        session: Session, *, email: str, display_name: str
    ) -> User:
        normalized = email.strip().lower()
        if session.scalar(select(User).where(User.email == normalized)):
            raise ConflictError("该邮箱已经存在")
        created_at = _next_platform_admin_timestamp(session)
        user = User(
            email=normalized,
            display_name=display_name.strip(),
            is_platform_admin=True,
            account_type=UserAccountType.PLATFORM_ADMIN,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(user)
        session.flush()
        return user

    @staticmethod
    def first_unambiguous_platform_admin(session: Session) -> User | None:
        """Return the original admin only when its persisted order is provable.

        The fallback is development-only and confers owner authority. Historical
        rows with a colliding earliest timestamp therefore fail closed instead
        of selecting whichever random UUID happens to sort first.
        """

        admins = list(
            session.scalars(
                select(User)
                .where(User.is_platform_admin.is_(True))
                .order_by(User.created_at.asc(), User.id.asc())
                .limit(2)
            ).all()
        )
        if not admins:
            return None
        if (
            len(admins) > 1
            and _as_utc(admins[0].created_at) == _as_utc(admins[1].created_at)
        ):
            return None
        return admins[0]

    @staticmethod
    def page_companies(
        session: Session, *, page: int, page_size: int
    ) -> tuple[int, list[Company]]:
        total = session.scalar(select(func.count(Company.id))) or 0
        companies = list(
            session.scalars(
                select(Company)
                .order_by(Company.created_at.desc(), Company.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return total, companies

    @staticmethod
    def create_company(
        session: Session,
        *,
        name: str,
        owner_email: str,
        owner_display_name: str,
        owner_activation_required: bool = False,
    ) -> tuple[Company, User, CompanyMembership]:
        company, owner, membership = CompanyService.bootstrap_company(
            session,
            company_name=name,
            owner_email=owner_email,
            owner_display_name=owner_display_name,
            owner_activation_required=owner_activation_required,
        )
        return company, owner, membership

    @staticmethod
    def set_company_status(
        session: Session, *, company_id: str, status: CompanyStatus
    ) -> tuple[CompanyStatus, Company]:
        company = session.scalar(
            select(Company).where(Company.id == company_id).with_for_update()
        )
        if company is None:
            raise NotFoundError("公司不存在")
        before = company.status
        company.status = status
        session.flush()
        return before, company

    @staticmethod
    def company_entitlements(session: Session, *, company_id: str) -> dict:
        company = session.get(Company, company_id)
        if company is None:
            raise NotFoundError("公司不存在")
        billing_unit = (
            BillingUnit.POINT
            if company.billing_version == 2
            else BillingUnit.CNY_CENT
        )

        model_rows = session.execute(
            select(ModelDefinition, CompanyModelGrant)
            .outerjoin(
                CompanyModelGrant,
                and_(
                    CompanyModelGrant.model_id == ModelDefinition.id,
                    CompanyModelGrant.company_id == company_id,
                ),
            )
            .order_by(ModelDefinition.slug, ModelDefinition.id)
        ).all()
        resource_rows = session.execute(
            select(ResourceDefinition, CompanyResourceGrant)
            .outerjoin(
                CompanyResourceGrant,
                and_(
                    CompanyResourceGrant.resource_id == ResourceDefinition.id,
                    CompanyResourceGrant.company_id == company_id,
                ),
            )
            .order_by(ResourceDefinition.kind, ResourceDefinition.key)
        ).all()

        models = []
        for model, grant in model_rows:
            if model.published_at is None:
                status = "draft"
            elif model.active:
                status = "published"
            else:
                status = "disabled"
            models.append(
                {
                    "model_id": model.id,
                    "slug": model.slug,
                    "display_name": model.display_name,
                    "status": status,
                    "billing_mode": model.billing_mode,
                    "grant_id": grant.id if grant else None,
                    "enabled": grant.enabled if grant else False,
                    "price_per_second_cents": (
                        grant.price_per_second_cents if grant else None
                    ),
                    "price_per_item_cents": (
                        grant.price_per_item_cents if grant else None
                    ),
                    "price_per_second_points": (
                        grant.price_per_second_points if grant else None
                    ),
                    "price_per_item_points": (
                        grant.price_per_item_points if grant else None
                    ),
                    "point_price_candidate_per_second": (
                        grant.point_price_candidate_per_second if grant else None
                    ),
                    "point_price_candidate_per_item": (
                        grant.point_price_candidate_per_item if grant else None
                    ),
                    "point_price_candidate_revision": (
                        grant.point_price_candidate_revision if grant else None
                    ),
                    "point_price_candidate_created_at": (
                        _as_utc(grant.point_price_candidate_created_at)
                        if grant
                        else None
                    ),
                    "point_price_candidate_version_id": (
                        grant.point_price_candidate_version_id if grant else None
                    ),
                    "point_price_active_version_id": (
                        grant.point_price_active_version_id if grant else None
                    ),
                    "billing_unit": billing_unit,
                    "billing_version": company.billing_version,
                    "config_override": grant.config_override if grant else {},
                    "call_quota": grant.call_quota if grant else None,
                    "concurrency_limit": (
                        grant.concurrency_limit if grant else None
                    ),
                    "effective_at": grant.effective_at if grant else None,
                    "expires_at": grant.expires_at if grant else None,
                    "grant_updated_at": (
                        _as_utc(grant.updated_at) if grant else None
                    ),
                }
            )

        resources = [
            {
                "resource_id": resource.id,
                "key": resource.key,
                "kind": resource.kind,
                "display_name": resource.display_name,
                "active": resource.active,
                "grant_id": grant.id if grant else None,
                "enabled": grant.enabled if grant else False,
                "config_override": grant.config_override if grant else {},
                "call_quota": grant.call_quota if grant else None,
                "concurrency_limit": grant.concurrency_limit if grant else None,
                "effective_at": grant.effective_at if grant else None,
                "expires_at": grant.expires_at if grant else None,
            }
            for resource, grant in resource_rows
        ]
        return {
            "company_id": company_id,
            "billing_unit": billing_unit,
            "billing_version": company.billing_version,
            "models": models,
            "resources": resources,
        }
