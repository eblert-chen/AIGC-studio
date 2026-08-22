from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ModelDefinition, utcnow
from ..relay_client import RelayModelCatalog, RelayModelResource
from .errors import ConflictError, NotFoundError
from .models import ModelCatalogService
from .task_admission import TaskCapabilityAdmission


class RelayCapabilityService:
    """Approve Relay's physical capability as the ceiling for a platform model."""

    @staticmethod
    def _platform_capability(
        session: Session, model: ModelDefinition
    ) -> dict | None:
        capability_map = ModelCatalogService.capabilities(
            session, model_id=model.id
        )
        if not capability_map:
            return None
        try:
            return TaskCapabilityAdmission.effective_capabilities(
                capability_map=capability_map,
                require_usable=True,
            )
        except ConflictError:
            return None

    @classmethod
    def compatibility(
        cls,
        session: Session,
        *,
        model: ModelDefinition,
        relay_model: RelayModelResource,
    ) -> tuple[str, dict | None]:
        platform_capability = cls._platform_capability(session, model)
        if platform_capability is None:
            return "platform_unconfigured", None
        relay_capability = relay_model.capabilities.model_dump(mode="json")
        try:
            TaskCapabilityAdmission.validate_company_override(
                capability_map={"generation": relay_capability},
                config_override=platform_capability,
            )
        except ConflictError:
            return "unsafe_expansion", platform_capability
        if platform_capability == relay_capability:
            return "identical", platform_capability
        return "compatible_restriction", platform_capability

    @classmethod
    def audit_catalog(
        cls, session: Session, *, catalog: RelayModelCatalog
    ) -> dict:
        platform_models = {
            model.slug: model
            for model in session.scalars(
                select(ModelDefinition).order_by(ModelDefinition.slug)
            ).all()
        }
        items = []
        for relay_model in catalog.data:
            platform_model = platform_models.pop(relay_model.id, None)
            if platform_model is None:
                status = "unmapped"
                platform_capability = None
            else:
                status, platform_capability = cls.compatibility(
                    session,
                    model=platform_model,
                    relay_model=relay_model,
                )
            items.append(
                {
                    "relay_model_id": relay_model.id,
                    "capability_revision": relay_model.capability_revision,
                    "capabilities": relay_model.capabilities.model_dump(
                        mode="json"
                    ),
                    "status": status,
                    "platform_model_id": (
                        platform_model.id if platform_model is not None else None
                    ),
                    "platform_capability_version": (
                        platform_model.capability_version
                        if platform_model is not None
                        else None
                    ),
                    "platform_active": (
                        platform_model.active
                        if platform_model is not None
                        else None
                    ),
                    "approved_revision": (
                        platform_model.relay_capability_revision
                        if platform_model is not None
                        else None
                    ),
                    "platform_capabilities": platform_capability,
                }
            )
        return {
            "catalog_revision": catalog.catalog_revision,
            "items": items,
            "platform_only_model_ids": [
                model.id
                for model in sorted(
                    platform_models.values(), key=lambda item: item.slug
                )
            ],
        }

    @classmethod
    def approve_model_revision(
        cls,
        session: Session,
        *,
        model_id: str,
        expected_capability_version: int,
        relay_model: RelayModelResource,
    ) -> tuple[dict, ModelDefinition, bool, str]:
        model = ModelCatalogService.get_model_for_update(
            session, model_id=model_id
        )
        if model.capability_version != expected_capability_version:
            raise ConflictError("模型能力版本已变化，请刷新后重试")
        if model.slug != relay_model.id:
            raise ConflictError("平台模型标识与 Relay 模型标识不一致")
        status, _ = cls.compatibility(
            session, model=model, relay_model=relay_model
        )
        if status == "platform_unconfigured":
            raise ConflictError("平台模型尚未配置可用的生成能力")
        if status == "unsafe_expansion":
            raise ConflictError(
                "平台模型能力超出 Relay 当前能力，请先停用并收紧模型配置"
            )
        before = ModelCatalogService.response(session, model=model)
        changed = (
            model.relay_capability_revision
            != relay_model.capability_revision
        )
        model.relay_capability_revision = relay_model.capability_revision
        model.relay_capability_synced_at = utcnow()
        session.flush()
        return before, model, changed, status

    @staticmethod
    def relay_model(
        catalog: RelayModelCatalog, *, model_slug: str
    ) -> RelayModelResource:
        relay_model = next(
            (item for item in catalog.data if item.id == model_slug), None
        )
        if relay_model is None:
            raise NotFoundError("Relay 中不存在同标识模型")
        return relay_model
