from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CompanyResourceGrant,
    ResourceDefinition,
    ResourceKind,
)
from .errors import ConflictError, NotFoundError
from .entitlement_policy import normalize_entitlement_policy


class ResourceGrantService:
    @staticmethod
    def _required_text(value: str, *, field_name: str, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ConflictError(f"{field_name} must not be blank")
        if len(normalized) > max_length:
            raise ConflictError(f"{field_name} is too long")
        return normalized

    @staticmethod
    def create_definition(
        session: Session,
        *,
        key: str,
        kind: ResourceKind,
        display_name: str,
        description: str,
        active: bool,
    ) -> ResourceDefinition:
        normalized_key = ResourceGrantService._required_text(
            key, field_name="key", max_length=120
        )
        normalized_display_name = ResourceGrantService._required_text(
            display_name, field_name="display_name", max_length=160
        )
        normalized_description = description.strip()
        if len(normalized_description) > 500:
            raise ConflictError("description is too long")
        if session.scalar(
            select(ResourceDefinition).where(
                ResourceDefinition.key == normalized_key
            )
        ):
            raise ConflictError("资源标识已存在")
        resource = ResourceDefinition(
            key=normalized_key,
            kind=kind,
            display_name=normalized_display_name,
            description=normalized_description,
            active=active,
        )
        session.add(resource)
        session.flush()
        return resource

    @staticmethod
    def list_definitions(session: Session) -> list[ResourceDefinition]:
        return list(
            session.scalars(
                select(ResourceDefinition).order_by(
                    ResourceDefinition.kind, ResourceDefinition.key
                )
            ).all()
        )

    @staticmethod
    def update_definition(
        session: Session,
        *,
        resource_id: str,
        display_name: str,
        description: str,
        active: bool,
    ) -> tuple[dict, ResourceDefinition, bool]:
        resource = session.scalar(
            select(ResourceDefinition)
            .where(ResourceDefinition.id == resource_id)
            .with_for_update()
        )
        if resource is None:
            raise NotFoundError("资源定义不存在")
        normalized_display_name = ResourceGrantService._required_text(
            display_name, field_name="display_name", max_length=160
        )
        normalized_description = description.strip()
        if len(normalized_description) > 500:
            raise ConflictError("description is too long")
        before = {
            "display_name": resource.display_name,
            "description": resource.description,
            "active": resource.active,
        }
        changed = before != {
            "display_name": normalized_display_name,
            "description": normalized_description,
            "active": active,
        }
        if changed:
            resource.display_name = normalized_display_name
            resource.description = normalized_description
            resource.active = active
            session.flush()
        return before, resource, changed

    @staticmethod
    def upsert_company_grant(
        session: Session,
        *,
        company_id: str,
        resource_id: str,
        enabled: bool,
        config_override: dict,
        call_quota: int | None = None,
        concurrency_limit: int | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> CompanyResourceGrant:
        (
            call_quota,
            concurrency_limit,
            effective_at,
            expires_at,
        ) = normalize_entitlement_policy(
            call_quota=call_quota,
            concurrency_limit=concurrency_limit,
            effective_at=effective_at,
            expires_at=expires_at,
        )
        if session.get(Company, company_id) is None:
            raise NotFoundError("公司不存在")
        resource = session.scalar(
            select(ResourceDefinition)
            .where(ResourceDefinition.id == resource_id)
            .with_for_update()
        )
        if resource is None:
            raise NotFoundError("资源定义不存在")
        if enabled and not resource.active:
            raise ConflictError("已停用资源不能启用公司授权")
        grant = session.scalar(
            select(CompanyResourceGrant).where(
                CompanyResourceGrant.company_id == company_id,
                CompanyResourceGrant.resource_id == resource_id,
            ).with_for_update()
        )
        if grant is None:
            grant = CompanyResourceGrant(
                company_id=company_id, resource_id=resource_id
            )
            session.add(grant)
        grant.enabled = enabled
        grant.config_override = config_override
        grant.call_quota = call_quota
        grant.concurrency_limit = concurrency_limit
        grant.effective_at = effective_at
        grant.expires_at = expires_at
        session.flush()
        return grant

    @staticmethod
    def list_available(session: Session, *, company_id: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        rows = session.execute(
            select(ResourceDefinition, CompanyResourceGrant)
            .join(
                CompanyResourceGrant,
                CompanyResourceGrant.resource_id == ResourceDefinition.id,
            )
            .where(
                CompanyResourceGrant.company_id == company_id,
                CompanyResourceGrant.enabled.is_(True),
                or_(
                    CompanyResourceGrant.effective_at.is_(None),
                    CompanyResourceGrant.effective_at <= now,
                ),
                or_(
                    CompanyResourceGrant.expires_at.is_(None),
                    CompanyResourceGrant.expires_at > now,
                ),
                ResourceDefinition.active.is_(True),
            )
            .order_by(ResourceDefinition.kind, ResourceDefinition.key)
        ).all()
        return [
            {
                "id": resource.id,
                "key": resource.key,
                "kind": resource.kind,
                "display_name": resource.display_name,
                "description": resource.description,
                "config_override": grant.config_override,
                "call_quota": grant.call_quota,
                "concurrency_limit": grant.concurrency_limit,
                "effective_at": grant.effective_at,
                "expires_at": grant.expires_at,
            }
            for resource, grant in rows
        ]
