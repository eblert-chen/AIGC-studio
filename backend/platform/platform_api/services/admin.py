from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CompanyMembership,
    CompanyModelGrant,
    CompanyResourceGrant,
    CompanyStatus,
    ModelDefinition,
    ResourceDefinition,
    User,
)
from .companies import CompanyService
from .errors import ConflictError, NotFoundError


class PlatformAdminService:
    @staticmethod
    def bootstrap_admin(
        session: Session, *, email: str, display_name: str
    ) -> User:
        normalized = email.strip().lower()
        if session.scalar(select(User).where(User.email == normalized)):
            raise ConflictError("该邮箱已经存在")
        user = User(
            email=normalized,
            display_name=display_name.strip(),
            is_platform_admin=True,
        )
        session.add(user)
        session.flush()
        return user

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
        if session.get(Company, company_id) is None:
            raise NotFoundError("公司不存在")

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
                    "config_override": grant.config_override if grant else {},
                    "call_quota": grant.call_quota if grant else None,
                    "concurrency_limit": (
                        grant.concurrency_limit if grant else None
                    ),
                    "effective_at": grant.effective_at if grant else None,
                    "expires_at": grant.expires_at if grant else None,
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
        return {"company_id": company_id, "models": models, "resources": resources}
