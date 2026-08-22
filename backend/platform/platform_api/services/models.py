from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CompanyModelGrant,
    GenerationTask,
    ModelCapability,
    ModelDefinition,
    utcnow,
)
from .errors import ConflictError, NotFoundError
from .entitlement_policy import normalize_entitlement_policy
from .quote_revision import model_grant_quote_revision
from .task_admission import TaskCapabilityAdmission


MAX_MONEY_CENTS = 9_000_000_000_000_000


class ModelCatalogService:
    @staticmethod
    def _required_text(value: str, *, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ConflictError(f"{field_name} must not be blank")
        return normalized

    @staticmethod
    def _normalized_capabilities(
        capabilities: list[tuple[str, dict]],
        *,
        billing_mode: str | None = None,
    ) -> dict[str, dict]:
        normalized: dict[str, dict] = {}
        for key, config in capabilities:
            normalized_key = key.strip()
            if not normalized_key:
                raise ConflictError("Model capability key must not be blank")
            if not config:
                raise ConflictError(
                    f"Model capability {normalized_key!r} must not be empty"
                )
            if normalized_key in normalized:
                raise ConflictError("模型能力键不能重复")
            normalized[normalized_key] = config
        if normalized:
            canonical = TaskCapabilityAdmission.validate_catalog(
                normalized, require_usable=True
            )
            ModelCatalogService._validate_billing_capabilities(
                canonical, billing_mode=billing_mode
            )
            return {"generation": canonical}
        return {}

    @staticmethod
    def _validate_billing_capabilities(
        effective_capabilities: dict,
        *,
        billing_mode: str | None,
    ) -> None:
        if billing_mode != "per_second":
            return
        for mode, capability in effective_capabilities.get("modes", {}).items():
            if capability["limits"]["output_counts"] != [1]:
                raise ConflictError(
                    f"按秒计费模型的 {mode} 模式只能声明单产物 output_counts=[1]"
                )

    @staticmethod
    def capabilities(session: Session, *, model_id: str) -> dict[str, dict]:
        rows = session.scalars(
            select(ModelCapability)
            .where(ModelCapability.model_id == model_id)
            .order_by(ModelCapability.capability_key)
        ).all()
        return {row.capability_key: row.config for row in rows}

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @classmethod
    def response(cls, session: Session, *, model: ModelDefinition) -> dict:
        if model.published_at is None:
            status = "draft"
        elif model.active:
            status = "published"
        else:
            status = "disabled"
        capabilities = cls.capabilities(session, model_id=model.id)
        return {
            "id": model.id,
            "slug": model.slug,
            "display_name": model.display_name,
            "provider_key": model.provider_key,
            "billing_mode": model.billing_mode,
            "capability_version": model.capability_version,
            "relay_capability_revision": model.relay_capability_revision,
            "relay_capability_synced_at": cls._as_utc(
                model.relay_capability_synced_at
            ),
            "active": model.active,
            "status": status,
            "capabilities": capabilities,
            "effective_capabilities": (
                TaskCapabilityAdmission.effective_capabilities(
                    capability_map=capabilities
                )
            ),
            "published_at": cls._as_utc(model.published_at),
            "created_at": cls._as_utc(model.created_at),
            "updated_at": cls._as_utc(model.updated_at),
        }

    @classmethod
    def list_models(cls, session: Session) -> list[dict]:
        models = session.scalars(
            select(ModelDefinition).order_by(
                ModelDefinition.created_at.desc(), ModelDefinition.id
            )
        ).all()
        return [cls.response(session, model=model) for model in models]

    @classmethod
    def get_model(cls, session: Session, *, model_id: str) -> ModelDefinition:
        model = session.get(ModelDefinition, model_id)
        if model is None:
            raise NotFoundError("模型不存在")
        return model

    @classmethod
    def get_model_for_update(
        cls, session: Session, *, model_id: str
    ) -> ModelDefinition:
        model = session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .with_for_update()
        )
        if model is None:
            raise NotFoundError("模型不存在")
        return model

    @classmethod
    def create_model(
        cls,
        session: Session,
        *,
        slug: str,
        display_name: str,
        provider_key: str,
        capability_version: int,
        capabilities: list[tuple[str, dict]],
        billing_mode: str = "per_second",
        active: bool = True,
    ) -> ModelDefinition:
        if billing_mode not in {"per_second", "per_item"}:
            raise ConflictError("模型计费方式无效")
        if session.scalar(select(ModelDefinition).where(ModelDefinition.slug == slug)):
            raise ConflictError("模型标识已存在")
        normalized = cls._normalized_capabilities(
            capabilities, billing_mode=billing_mode
        )
        normalized_display_name = cls._required_text(
            display_name, field_name="display_name"
        )
        normalized_provider_key = cls._required_text(
            provider_key, field_name="provider_key"
        )
        model = ModelDefinition(
            slug=slug.strip(),
            display_name=normalized_display_name,
            provider_key=normalized_provider_key,
            billing_mode=billing_mode,
            capability_version=capability_version,
            active=active,
            published_at=utcnow() if active else None,
        )
        session.add(model)
        session.flush()
        for key, config in sorted(normalized.items()):
            session.add(
                ModelCapability(
                    model_id=model.id,
                    capability_key=key,
                    config=config,
                )
            )
        session.flush()
        return model

    @classmethod
    def create_draft(
        cls,
        session: Session,
        *,
        slug: str,
        display_name: str,
        provider_key: str,
        capabilities: list[tuple[str, dict]],
        billing_mode: str = "per_second",
    ) -> tuple[ModelDefinition, bool]:
        normalized = cls._normalized_capabilities(
            capabilities, billing_mode=billing_mode
        )
        normalized_slug = slug.strip()
        normalized_display_name = cls._required_text(
            display_name, field_name="display_name"
        )
        normalized_provider_key = cls._required_text(
            provider_key, field_name="provider_key"
        )
        existing = session.scalar(
            select(ModelDefinition).where(ModelDefinition.slug == normalized_slug)
        )
        if existing is not None:
            same = (
                existing.published_at is None
                and not existing.active
                and existing.capability_version == 1
                and existing.display_name == normalized_display_name
                and existing.provider_key == normalized_provider_key
                and existing.billing_mode == billing_mode
                and cls.capabilities(session, model_id=existing.id) == normalized
            )
            if same:
                return existing, False
            raise ConflictError("模型标识已存在")
        model = cls.create_model(
            session,
            slug=normalized_slug,
            display_name=normalized_display_name,
            provider_key=normalized_provider_key,
            capability_version=1,
            capabilities=list(normalized.items()),
            billing_mode=billing_mode,
            active=False,
        )
        # The ORM default marks legacy direct inserts as published. An explicit
        # platform-admin create is a draft and clears that default after insert.
        model.published_at = None
        session.flush()
        return model, True

    @classmethod
    def update_model(
        cls,
        session: Session,
        *,
        model_id: str,
        display_name: str,
        provider_key: str,
        capabilities: list[tuple[str, dict]],
        expected_capability_version: int,
        billing_mode: str | None = None,
    ) -> tuple[dict, ModelDefinition, bool]:
        # Billing mode and company grants form one pricing invariant. Lock the
        # model first so model edits, grant writes, and task pricing snapshots
        # all use the same model -> grant lock order.
        model = cls.get_model_for_update(session, model_id=model_id)
        if model.active:
            raise ConflictError("已发布模型必须先停用再修改")
        normalized_display_name = cls._required_text(
            display_name, field_name="display_name"
        )
        normalized_provider_key = cls._required_text(
            provider_key, field_name="provider_key"
        )
        next_billing_mode = billing_mode or model.billing_mode
        if next_billing_mode not in {"per_second", "per_item"}:
            raise ConflictError("模型计费方式无效")
        normalized = cls._normalized_capabilities(
            capabilities, billing_mode=next_billing_mode
        )
        if next_billing_mode != model.billing_mode and session.scalar(
            select(CompanyModelGrant.id).where(
                CompanyModelGrant.model_id == model.id
            )
        ):
            raise ConflictError("已授权给公司的模型不能变更计费方式")
        before = cls.response(session, model=model)
        same_content = (
            model.display_name == normalized_display_name
            and model.provider_key == normalized_provider_key
            and model.billing_mode == next_billing_mode
            and before["capabilities"] == normalized
        )
        if model.capability_version != expected_capability_version:
            if same_content:
                return before, model, False
            raise ConflictError("模型能力版本已变化，请刷新后重试")
        if same_content:
            return before, model, False

        capabilities_changed = before["capabilities"] != normalized
        model.display_name = normalized_display_name
        model.provider_key = normalized_provider_key
        model.billing_mode = next_billing_mode
        # This field is the model configuration revision used for optimistic
        # concurrency as well as capability snapshots. Every editable field
        # must advance it, otherwise a stale full-replacement PUT could still
        # overwrite a newer display/provider-only edit after waiting on the row.
        model.capability_version += 1
        if capabilities_changed:
            session.execute(
                delete(ModelCapability).where(ModelCapability.model_id == model.id)
            )
            for key, config in sorted(normalized.items()):
                session.add(
                    ModelCapability(
                        model_id=model.id,
                        capability_key=key,
                        config=config,
                    )
                )
        session.flush()
        return before, model, True

    @classmethod
    def publish(
        cls,
        session: Session,
        *,
        model_id: str,
        require_relay_capability_revision: bool = False,
    ) -> tuple[dict, ModelDefinition, bool]:
        model = cls.get_model_for_update(session, model_id=model_id)
        before = cls.response(session, model=model)
        if model.active:
            return before, model, False
        if (
            require_relay_capability_revision
            and model.relay_capability_revision is None
        ):
            raise ConflictError(
                "请先确认中转站模型能力版本，再发布模型"
            )
        capability_map = cls.capabilities(session, model_id=model.id)
        effective = TaskCapabilityAdmission.validate_catalog(
            capability_map, require_usable=True
        )
        cls._validate_billing_capabilities(
            effective, billing_mode=model.billing_mode
        )
        enabled_grants = session.scalars(
            select(CompanyModelGrant)
            .where(
                CompanyModelGrant.model_id == model.id,
                CompanyModelGrant.enabled.is_(True),
            )
            .order_by(CompanyModelGrant.company_id, CompanyModelGrant.id)
            .with_for_update()
        ).all()
        for grant in enabled_grants:
            try:
                TaskCapabilityAdmission.validate_company_override(
                    capability_map=capability_map,
                    config_override=grant.config_override,
                )
            except ConflictError as exc:
                raise ConflictError(
                    "模型能力与现有公司授权不兼容，请先修正或停用授权 "
                    f"(company_id={grant.company_id})"
                ) from exc
        model.active = True
        if model.published_at is None:
            model.published_at = utcnow()
        session.flush()
        return before, model, True

    @classmethod
    def disable(
        cls, session: Session, *, model_id: str
    ) -> tuple[dict, ModelDefinition, bool]:
        model = cls.get_model_for_update(session, model_id=model_id)
        before = cls.response(session, model=model)
        if model.published_at is None:
            raise ConflictError("草稿模型尚未发布，不能执行停用")
        if not model.active:
            return before, model, False
        model.active = False
        session.flush()
        return before, model, True

    @classmethod
    def delete_draft(cls, session: Session, *, model_id: str) -> dict:
        model = cls.get_model_for_update(session, model_id=model_id)
        if model.published_at is not None:
            raise ConflictError("已发布过的模型必须保留审计历史，只能停用")
        if session.scalar(
            select(CompanyModelGrant.id).where(CompanyModelGrant.model_id == model.id)
        ) or session.scalar(
            select(GenerationTask.id).where(GenerationTask.model_id == model.id)
        ):
            raise ConflictError("模型已被授权或使用，不能删除")
        before = cls.response(session, model=model)
        session.execute(
            delete(ModelCapability).where(ModelCapability.model_id == model.id)
        )
        session.delete(model)
        session.flush()
        return before


class ModelGrantService:
    @staticmethod
    def list_available_models(session: Session, *, company_id: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        rows = session.execute(
            select(ModelDefinition, CompanyModelGrant)
            .join(
                CompanyModelGrant,
                CompanyModelGrant.model_id == ModelDefinition.id,
            )
            .where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.enabled.is_(True),
                or_(
                    CompanyModelGrant.effective_at.is_(None),
                    CompanyModelGrant.effective_at <= now,
                ),
                or_(
                    CompanyModelGrant.expires_at.is_(None),
                    CompanyModelGrant.expires_at > now,
                ),
                ModelDefinition.active.is_(True),
                ModelDefinition.published_at.is_not(None),
            )
            .order_by(ModelDefinition.display_name)
        ).all()
        result: list[dict] = []
        for model, grant in rows:
            has_second = grant.price_per_second_cents is not None
            has_item = grant.price_per_item_cents is not None
            configured_mode = "per_second" if has_second else "per_item"
            if has_second == has_item or configured_mode != model.billing_mode:
                continue
            capabilities = session.scalars(
                select(ModelCapability)
                .where(ModelCapability.model_id == model.id)
                .order_by(ModelCapability.capability_key)
            ).all()
            capability_map = {
                capability.capability_key: capability.config
                for capability in capabilities
            }
            result.append(
                {
                    "id": model.id,
                    "slug": model.slug,
                    "display_name": model.display_name,
                    "capability_version": model.capability_version,
                    "relay_capability_revision": (
                        model.relay_capability_revision
                    ),
                    "relay_capability_synced_at": (
                        model.relay_capability_synced_at
                    ),
                    "capabilities": capability_map,
                    "effective_capabilities": (
                        TaskCapabilityAdmission.effective_capabilities(
                            capability_map=capability_map,
                            config_override=grant.config_override,
                            require_usable=True,
                        )
                    ),
                    "pricing_mode": model.billing_mode,
                    "unit_price_cents": (
                        grant.price_per_second_cents
                        if has_second
                        else grant.price_per_item_cents
                    ),
                    "quote_revision": model_grant_quote_revision(
                        model=model,
                        grant=grant,
                    ),
                    "config_override": grant.config_override,
                    "call_quota": grant.call_quota,
                    "concurrency_limit": grant.concurrency_limit,
                    "effective_at": grant.effective_at,
                    "expires_at": grant.expires_at,
                }
            )
        return result

    @staticmethod
    def upsert_grant(
        session: Session,
        *,
        company_id: str,
        model_id: str,
        enabled: bool,
        price_per_second_cents: int | None,
        price_per_item_cents: int | None,
        config_override: dict,
        call_quota: int | None = None,
        concurrency_limit: int | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> CompanyModelGrant:
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
        configured_prices = sum(
            price is not None
            for price in (price_per_second_cents, price_per_item_cents)
        )
        if configured_prices != 1:
            raise ConflictError("按秒价格与按条价格必须且只能配置一种")
        configured_price = (
            price_per_second_cents
            if price_per_second_cents is not None
            else price_per_item_cents
        )
        if (
            configured_price is None
            or configured_price <= 0
            or configured_price > MAX_MONEY_CENTS
        ):
            raise ConflictError("计费价格必须大于 0 分")
        if session.get(Company, company_id) is None:
            raise NotFoundError("公司不存在")
        model = session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .with_for_update()
        )
        if model is None:
            raise NotFoundError("模型不存在")
        if enabled and (model.published_at is None or not model.active):
            raise ConflictError("只有已发布且启用的模型可以授权给公司")
        requested_mode = (
            "per_second"
            if price_per_second_cents is not None
            else "per_item"
        )
        if requested_mode != model.billing_mode:
            raise ConflictError("授权价格必须使用模型目录中固定的计费方式")
        capability_map = ModelCatalogService.capabilities(
            session, model_id=model.id
        )
        if enabled:
            TaskCapabilityAdmission.validate_catalog(
                capability_map, require_usable=True
            )
        if enabled:
            TaskCapabilityAdmission.validate_company_override(
                capability_map=capability_map,
                config_override=config_override,
            )
        grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.model_id == model_id,
            ).with_for_update()
        )
        if grant is None:
            grant = CompanyModelGrant(company_id=company_id, model_id=model_id)
            session.add(grant)
        grant.enabled = enabled
        grant.price_per_second_cents = price_per_second_cents
        grant.price_per_item_cents = price_per_item_cents
        grant.config_override = config_override
        grant.call_quota = call_quota
        grant.concurrency_limit = concurrency_limit
        grant.effective_at = effective_at
        grant.expires_at = expires_at
        session.flush()
        return grant

    @staticmethod
    def list_company_grants(
        session: Session, *, company_id: str
    ) -> list[CompanyModelGrant]:
        return list(
            session.scalars(
                select(CompanyModelGrant)
                .where(CompanyModelGrant.company_id == company_id)
                .order_by(CompanyModelGrant.created_at)
            ).all()
        )
