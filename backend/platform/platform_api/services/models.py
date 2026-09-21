from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..relay_client import RelayModelReleaseEvidence

from ..models import (
    BillingUnit,
    Company,
    CompanyModelGrant,
    CompanyPointPriceVersion,
    GenerationTask,
    ModelCapability,
    ModelDefinition,
    PersonalRetailModelGrant,
    PointPriceVersionStatus,
    new_id,
    utcnow,
)
from .errors import ConflictError, NotFoundError
from .commercial_pricing import CommercialPricingPolicy
from .entitlement_policy import normalize_entitlement_policy
from .generation_resources import (
    GenerationModelGrantAdmission,
    GenerationResourceAdmission,
)
from .quote_revision import model_grant_quote_revision
from .model_release_guard import require_model_release_snapshot
from .task_admission import TaskCapabilityAdmission


MAX_MONEY_CENTS = 9_000_000_000_000_000
_EXPECTED_GRANT_VERSION_NOT_CHECKED = object()


def _active_point_price_content_sha256(
    *,
    company_id: str,
    grant_id: str,
    model_id: str,
    billing_mode: str,
    unit_price_points: int,
    supersedes_version_id: str | None,
) -> str:
    canonical = json.dumps(
        {
            "schema_version": 1,
            "company_id": company_id,
            "grant_id": grant_id,
            "model_id": model_id,
            "status": "active",
            "billing_mode": billing_mode,
            "unit_price_points": unit_price_points,
            "formula_version": "admin-approved-manual:v1",
            "supersedes_version_id": supersedes_version_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _matches_active_point_price(
    version: CompanyPointPriceVersion | None,
    *,
    company_id: str,
    grant_id: str,
    model_id: str,
    billing_mode: str,
    unit_price_points: int,
) -> bool:
    return bool(
        version is not None
        and version.company_id == company_id
        and version.grant_id == grant_id
        and version.model_id == model_id
        and version.status == PointPriceVersionStatus.ACTIVE
        and version.billing_mode == billing_mode
        and version.unit_price_points == unit_price_points
        and version.source_price_cents is None
        and version.formula_version == "admin-approved-manual:v1"
    )


class ModelCatalogService:
    @staticmethod
    def relay_candidate_is_approved(model: ModelDefinition) -> bool:
        """Return whether discovery and the last explicit approval agree.

        A missing candidate is kept compatible with deployments that do not
        configure Relay.  Production callers separately require a non-empty
        approved revision and ceiling.
        """

        return (
            model.relay_capability_candidate_revision is None
            or model.relay_capability_candidate_revision
            == model.relay_capability_revision
        )

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
        if model.relay_capability_candidate_revision is None:
            relay_approval_status = "unavailable"
        elif model.relay_capability_revision is None:
            relay_approval_status = "unapproved"
        elif model.relay_capability_approved_ceiling is None:
            relay_approval_status = "legacy_approved"
        elif (
            model.relay_capability_candidate_revision
            == model.relay_capability_revision
        ):
            relay_approval_status = "approved"
        else:
            relay_approval_status = "pending"
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
            "relay_capability_candidate_revision": (
                model.relay_capability_candidate_revision
            ),
            "relay_capability_candidate_catalog_revision": (
                model.relay_capability_candidate_catalog_revision
            ),
            "relay_capability_candidate": model.relay_capability_candidate,
            "relay_capability_candidate_synced_at": cls._as_utc(
                model.relay_capability_candidate_synced_at
            ),
            "relay_capability_approved_ceiling": (
                model.relay_capability_approved_ceiling
            ),
            "relay_capability_approved_catalog_revision": (
                model.relay_capability_approved_catalog_revision
            ),
            "relay_capability_approval_status": relay_approval_status,
            "relay_capability_requires_approval": (
                relay_approval_status != "approved"
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
            .execution_options(populate_existing=True)
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
        capability_schema_downgrade_reason: str | None = None,
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
        previous_schema = before["capabilities"].get("generation", {}).get("schema_version", 1)
        next_schema = normalized.get("generation", {}).get("schema_version", 1)
        if capabilities_changed and next_schema < previous_schema:
            if (
                capability_schema_downgrade_reason is None
                or not 3 <= len(capability_schema_downgrade_reason.strip()) <= 500
            ):
                raise ConflictError(
                    "降低模型能力 schema 版本会移除输入语义，必须明确填写降级原因；"
                    "普通编辑请保留当前 schema 和输入角色字段"
                )
        if capabilities_changed and model.relay_capability_approved_ceiling is not None:
            next_platform_capability = normalized.get("generation")
            if next_platform_capability is None:
                raise ConflictError(
                    "已批准 Relay 能力的模型不能移除全部生成能力"
                )
            try:
                TaskCapabilityAdmission.validate_full_restriction(
                    ceiling=model.relay_capability_approved_ceiling,
                    candidate=next_platform_capability,
                )
            except ConflictError as exc:
                raise ConflictError(
                    "平台模型能力超出已批准的 Relay 能力上限，"
                    "请先同步并批准新版本"
                ) from exc
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
        expected_release_snapshot: Mapping[str, Any] | None = None,
        publishing_plan_id: str | None = None,
    ) -> tuple[dict, ModelDefinition, bool]:
        statement = (
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        with session.no_autoflush:
            model = session.scalar(statement)
        if model is None:
            raise NotFoundError("模型不存在")
        require_model_release_snapshot(
            model=model,
            expected_snapshot=expected_release_snapshot,
        )
        before = cls.response(session, model=model)
        if (
            require_relay_capability_revision
            and (
                model.relay_capability_revision is None
                or model.relay_capability_approved_ceiling is None
                or not cls.relay_candidate_is_approved(model)
            )
        ):
            raise ConflictError(
                "请先同步并批准中转站模型能力版本，再发布模型"
            )
        capability_map = cls.capabilities(session, model_id=model.id)
        effective = TaskCapabilityAdmission.validate_catalog(
            capability_map, require_usable=True
        )
        cls._validate_billing_capabilities(
            effective, billing_mode=model.billing_mode
        )
        CommercialPricingPolicy.require_publication(
            session,
            model=model,
            expected_release_snapshot=expected_release_snapshot,
            publishing_plan_id=publishing_plan_id,
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
            point_price = grant.price_per_second_points or grant.price_per_item_points
            CommercialPricingPolicy.require_price(
                session,
                model=model,
                unit_price=point_price or grant.price_per_second_cents or grant.price_per_item_cents,
                billing_unit="POINT" if point_price is not None else "CNY_CENT",
                enabled=True,
                current_unit_price=point_price or grant.price_per_second_cents or grant.price_per_item_cents,
                config_override=grant.config_override,
                scope="company",
                expected_release_snapshot=expected_release_snapshot,
                publishing_plan_id=publishing_plan_id,
            )
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
        personal_grant = session.scalar(select(PersonalRetailModelGrant).where(
            PersonalRetailModelGrant.model_id == model.id,
            PersonalRetailModelGrant.enabled.is_(True),
        ).with_for_update())
        if personal_grant is not None:
            price = personal_grant.price_per_second_points or personal_grant.price_per_item_points
            CommercialPricingPolicy.require_price(
                session, model=model, unit_price=price, billing_unit="POINT", enabled=True,
                current_unit_price=price, config_override=personal_grant.config_override,
                scope="personal", expected_release_snapshot=expected_release_snapshot,
                publishing_plan_id=publishing_plan_id,
            )
        if model.active:
            return before, model, False
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
    def response(grant: CompanyModelGrant, *, billing_version: int) -> dict:
        if billing_version == 2:
            billing_unit = BillingUnit.POINT
        elif billing_version == 1:
            billing_unit = BillingUnit.CNY_CENT
        else:
            raise ConflictError("公司计费版本无效")
        return {
            "id": grant.id,
            "company_id": grant.company_id,
            "model_id": grant.model_id,
            "enabled": grant.enabled,
            "price_per_second_cents": grant.price_per_second_cents,
            "price_per_item_cents": grant.price_per_item_cents,
            "price_per_second_points": grant.price_per_second_points,
            "price_per_item_points": grant.price_per_item_points,
            "point_price_candidate_per_second": grant.point_price_candidate_per_second,
            "point_price_candidate_per_item": grant.point_price_candidate_per_item,
            "point_price_candidate_revision": grant.point_price_candidate_revision,
            "point_price_candidate_created_at": grant.point_price_candidate_created_at,
            "point_price_candidate_version_id": grant.point_price_candidate_version_id,
            "point_price_active_version_id": grant.point_price_active_version_id,
            "billing_unit": billing_unit,
            "billing_version": billing_version,
            "config_override": grant.config_override,
            "call_quota": grant.call_quota,
            "concurrency_limit": grant.concurrency_limit,
            "effective_at": ModelCatalogService._as_utc(grant.effective_at),
            "expires_at": ModelCatalogService._as_utc(grant.expires_at),
            "updated_at": ModelCatalogService._as_utc(grant.updated_at),
        }

    @staticmethod
    def list_available_models(
        session: Session,
        *,
        company_id: str,
        require_relay_approval: bool = False,
        release_evidence: RelayModelReleaseEvidence | None = None,
    ) -> list[dict]:
        now = datetime.now(timezone.utc)
        company = session.get(Company, company_id)
        if company is None:
            raise NotFoundError("公司不存在")
        if company.billing_version == 2:
            billing_unit = BillingUnit.POINT
        elif company.billing_version == 1:
            billing_unit = BillingUnit.CNY_CENT
        else:
            raise ConflictError("公司计费版本无效")
        relay_conditions = (
            (
                ModelDefinition.relay_capability_revision.is_not(None),
                ModelDefinition.relay_capability_approved_ceiling.is_not(None),
                ModelDefinition.relay_capability_candidate_revision
                == ModelDefinition.relay_capability_revision,
            )
            if require_relay_approval
            else (
                or_(
                    ModelDefinition.relay_capability_candidate_revision.is_(None),
                    ModelDefinition.relay_capability_candidate_revision
                    == ModelDefinition.relay_capability_revision,
                ),
            )
        )
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
                *relay_conditions,
            )
            .order_by(ModelDefinition.display_name)
        ).all()
        result: list[dict] = []
        for model, grant in rows:
            if billing_unit == BillingUnit.POINT:
                has_second = grant.price_per_second_points is not None
                has_item = grant.price_per_item_points is not None
                second_price = grant.price_per_second_points
                item_price = grant.price_per_item_points
                if grant.point_price_active_version_id is None:
                    continue
                price_version = session.get(
                    CompanyPointPriceVersion,
                    grant.point_price_active_version_id,
                )
                mutable_price = second_price if has_second else item_price
                if (
                    price_version is None
                    or price_version.status != PointPriceVersionStatus.ACTIVE
                    or price_version.company_id != company_id
                    or price_version.grant_id != grant.id
                    or price_version.model_id != model.id
                    or price_version.billing_mode != model.billing_mode
                    or price_version.unit_price_points != mutable_price
                ):
                    continue
                if has_second:
                    second_price = price_version.unit_price_points
                else:
                    item_price = price_version.unit_price_points
            else:
                has_second = grant.price_per_second_cents is not None
                has_item = grant.price_per_item_cents is not None
                second_price = grant.price_per_second_cents
                item_price = grant.price_per_item_cents
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
            effective_capabilities = TaskCapabilityAdmission.effective_capabilities(
                capability_map=capability_map,
                config_override=grant.config_override,
                require_usable=True,
            )
            model_grant_evaluation = GenerationModelGrantAdmission.evaluate(
                session,
                company_id=company_id,
                model_id=model.id,
                model_name=model.display_name,
                grant=grant,
            )
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
                    "effective_capabilities": effective_capabilities,
                    "readiness_checked_at": now,
                    "mode_readiness": (
                        GenerationResourceAdmission.mode_readiness(
                            session,
                            company_id=company_id,
                            effective_capabilities=effective_capabilities,
                            checked_at=now,
                            base_blockers=(
                                *model_grant_evaluation.blockers,
                                *CommercialPricingPolicy.distribution_blockers(
                                    session, model=model, release_evidence=release_evidence
                                ),
                            ),
                        )
                    ),
                    "pricing_mode": model.billing_mode,
                    "billing_unit": billing_unit,
                    "billing_version": company.billing_version,
                    "unit_price_cents": (
                        (second_price if has_second else item_price)
                        if billing_unit == BillingUnit.CNY_CENT
                        else None
                    ),
                    "unit_price_points": (
                        (second_price if has_second else item_price)
                        if billing_unit == BillingUnit.POINT
                        else None
                    ),
                    "price_version_id": (
                        grant.point_price_active_version_id
                        if billing_unit == BillingUnit.POINT
                        else model_grant_quote_revision(model=model, grant=grant)
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
        price_per_second_points: int | None = None,
        price_per_item_points: int | None = None,
        actor_user_id: str | None = None,
        call_quota: int | None = None,
        concurrency_limit: int | None = None,
        effective_at: datetime | None = None,
        expires_at: datetime | None = None,
        expected_updated_at: datetime | None | object = (
            _EXPECTED_GRANT_VERSION_NOT_CHECKED
        ),
        return_before: bool = False,
        expected_release_snapshot: Mapping[str, Any] | None = None,
        publishing_plan_id: str | None = None,
    ) -> CompanyModelGrant | tuple[dict, CompanyModelGrant]:
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
        company = session.scalar(
            select(Company).where(Company.id == company_id).with_for_update()
        )
        if company is None:
            raise NotFoundError("公司不存在")
        cents_prices = (price_per_second_cents, price_per_item_cents)
        point_prices = (price_per_second_points, price_per_item_points)
        if company.billing_version == 1:
            if any(price is not None for price in point_prices):
                raise ConflictError("旧计费企业不能配置积分价格")
            configured_prices = sum(price is not None for price in cents_prices)
            configured_price = next(
                (price for price in cents_prices if price is not None), None
            )
            price_unit_label = "分"
        elif company.billing_version == 2:
            if any(price is not None for price in cents_prices):
                raise ConflictError("积分计费企业不能配置旧币种价格")
            configured_prices = sum(price is not None for price in point_prices)
            configured_price = next(
                (price for price in point_prices if price is not None), None
            )
            price_unit_label = "积分"
        else:
            raise ConflictError("公司计费版本无效")
        if configured_prices != 1:
            raise ConflictError("按秒价格与按条价格必须且只能配置一种")
        if (
            configured_price is None
            or configured_price <= 0
            or configured_price > MAX_MONEY_CENTS
        ):
            raise ConflictError(f"计费价格必须大于 0 {price_unit_label}")
        model_statement = (
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        with session.no_autoflush:
            model = session.scalar(model_statement)
        if model is None:
            raise NotFoundError("模型不存在")
        require_model_release_snapshot(
            model=model,
            expected_snapshot=expected_release_snapshot,
        )
        if enabled and publishing_plan_id is None and (model.published_at is None or not model.active):
            raise ConflictError("只有已发布且启用的模型可以授权给公司")
        if enabled and not ModelCatalogService.relay_candidate_is_approved(model):
            raise ConflictError("模型存在尚未批准的 Relay 能力候选版本")
        requested_mode = (
            "per_second"
            if (
                price_per_second_cents is not None
                or price_per_second_points is not None
            )
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
        current_price = (
            (grant.price_per_second_points or grant.price_per_item_points)
            if grant is not None and company.billing_version == 2
            else (grant.price_per_second_cents or grant.price_per_item_cents)
            if grant is not None else None
        )
        CommercialPricingPolicy.require_price(
            session,
            model=model,
            unit_price=configured_price,
            billing_unit="POINT" if company.billing_version == 2 else "CNY_CENT",
            enabled=enabled,
            current_unit_price=current_price,
            config_override=config_override,
            scope="company",
            expected_release_snapshot=expected_release_snapshot,
            publishing_plan_id=publishing_plan_id,
        )
        before = (
            {
                "company_id": grant.company_id,
                "model_id": grant.model_id,
                "enabled": grant.enabled,
                "price_per_second_cents": grant.price_per_second_cents,
                "price_per_item_cents": grant.price_per_item_cents,
                "price_per_second_points": grant.price_per_second_points,
                "price_per_item_points": grant.price_per_item_points,
                "point_price_active_version_id": grant.point_price_active_version_id,
                "point_price_candidate_version_id": grant.point_price_candidate_version_id,
                "config_override": deepcopy(grant.config_override),
                "call_quota": grant.call_quota,
                "concurrency_limit": grant.concurrency_limit,
                "effective_at": (
                    ModelCatalogService._as_utc(grant.effective_at).isoformat()
                    if grant.effective_at is not None
                    else None
                ),
                "expires_at": (
                    ModelCatalogService._as_utc(grant.expires_at).isoformat()
                    if grant.expires_at is not None
                    else None
                ),
                "updated_at": ModelCatalogService._as_utc(
                    grant.updated_at
                ).isoformat(),
            }
            if grant is not None
            else {}
        )
        if expected_updated_at is not _EXPECTED_GRANT_VERSION_NOT_CHECKED:
            if grant is None:
                if expected_updated_at is not None:
                    raise ConflictError(
                        "公司模型授权已变化，请刷新授权矩阵后重试"
                    )
            elif (
                expected_updated_at is None
                or ModelCatalogService._as_utc(grant.updated_at)
                != ModelCatalogService._as_utc(expected_updated_at)
            ):
                raise ConflictError(
                    "公司模型授权已变化，请刷新授权矩阵后重试"
                )
        if grant is None:
            grant = CompanyModelGrant(company_id=company_id, model_id=model_id)
            session.add(grant)
            session.flush()
        grant.enabled = enabled
        if company.billing_version == 1:
            grant.price_per_second_cents = price_per_second_cents
            grant.price_per_item_cents = price_per_item_cents
            grant.price_per_second_points = None
            grant.price_per_item_points = None
            grant.point_price_active_version_id = None
        else:
            grant.price_per_second_cents = None
            grant.price_per_item_cents = None
            grant.price_per_second_points = price_per_second_points
            grant.price_per_item_points = price_per_item_points
            current_price_version = (
                session.get(
                    CompanyPointPriceVersion,
                    grant.point_price_active_version_id,
                )
                if grant.point_price_active_version_id is not None
                else None
            )
            if _matches_active_point_price(
                current_price_version,
                company_id=company_id,
                grant_id=grant.id,
                model_id=model_id,
                billing_mode=requested_mode,
                unit_price_points=configured_price,
            ):
                price_version = current_price_version
                assert price_version is not None
            else:
                supersedes_version_id = (
                    grant.point_price_active_version_id
                    or grant.point_price_candidate_version_id
                )
                content_sha256 = _active_point_price_content_sha256(
                    company_id=company_id,
                    grant_id=grant.id,
                    model_id=model_id,
                    billing_mode=requested_mode,
                    unit_price_points=configured_price,
                    supersedes_version_id=supersedes_version_id,
                )
                price_version = session.scalar(
                    select(CompanyPointPriceVersion).where(
                        CompanyPointPriceVersion.content_sha256 == content_sha256
                    )
                )
                if price_version is None:
                    price_version = CompanyPointPriceVersion(
                        id=new_id(),
                        company_id=company_id,
                        grant_id=grant.id,
                        model_id=model_id,
                        status=PointPriceVersionStatus.ACTIVE,
                        billing_mode=requested_mode,
                        unit_price_points=configured_price,
                        source_price_cents=None,
                        formula_version="admin-approved-manual:v1",
                        content_sha256=content_sha256,
                        supersedes_version_id=supersedes_version_id,
                        created_by_user_id=actor_user_id,
                        created_by_system_key=(
                            None
                            if actor_user_id is not None
                            else "platform-api:model-grant-upsert"
                        ),
                    )
                    session.add(price_version)
                    # The version refers back to this grant. Persist it before
                    # linking the grant: the non-deferred scope trigger cannot
                    # accept a pointer to an INSERT still pending in this flush.
                    session.flush()
            grant.point_price_active_version_id = price_version.id
        grant.config_override = config_override
        grant.call_quota = call_quota
        grant.concurrency_limit = concurrency_limit
        grant.effective_at = effective_at
        grant.expires_at = expires_at
        session.flush()
        if return_before:
            return before, grant
        return grant

    @staticmethod
    def list_company_grants(
        session: Session, *, company_id: str
    ) -> list[dict]:
        company = session.get(Company, company_id)
        if company is None:
            raise NotFoundError("公司不存在")
        return [
            ModelGrantService.response(
                grant, billing_version=company.billing_version
            )
            for grant in session.scalars(
                select(CompanyModelGrant)
                .where(CompanyModelGrant.company_id == company_id)
                .order_by(CompanyModelGrant.created_at)
            ).all()
        ]
