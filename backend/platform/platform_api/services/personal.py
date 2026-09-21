from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..relay_client import RelayModelReleaseEvidence

from ..models import (
    Company,
    CompanyMembership,
    ExternalIdentity,
    GenerationTask,
    MembershipStatus,
    ModelCapability,
    ModelDefinition,
    PersonalDownloadRecord,
    PersonalModelGrantBatchJournal,
    PersonalRetailModelGrant,
    PersonalWalletAccount,
    PersonalWorkspace,
    ProductContext,
    TaskArtifact,
    TaskStatus,
    User,
    new_id,
    utcnow,
)
from ..relay_backends import (
    DEFAULT_RELAY_CONTRACT_REVISION,
    NEW_API_RELAY_BACKEND_ID,
    RELAY_BACKEND_ID_PATTERN,
    RELAY_CONTRACT_REVISION_PATTERN,
)
from .errors import ConflictError, NotFoundError, PermissionDeniedError
from .commercial_pricing import CommercialPricingPolicy
from .entitlement_policy import normalize_entitlement_policy
from .execution_contracts import freeze_execution_contract
from .account_partition import AccountPartitionService, AccountProductType
from .audit import AuditService
from .models import ModelCatalogService
from .model_release_guard import require_model_release_snapshot
from .task_admission import TaskCapabilityAdmission
from .tasks import MAX_MONEY_CENTS, TaskService


PERSONAL_GENERATION_MODES = frozenset(
    {
        "text_to_video",
        "text_to_image",
        "image_to_image",
        "image_to_video",
        "video_to_video",
    }
)


_PERSONAL_LIMIT_UNSET = object()


def _quote_timestamp(value: datetime) -> str:
    """Canonicalize SQLite's naive UTC reloads and PostgreSQL timestamps alike."""

    stored = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return stored.astimezone(timezone.utc).isoformat()


def _personal_distribution_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=lambda item: item.isoformat(),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _personal_distribution_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _personal_distribution_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_personal_distribution_json(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _retail_quote_revision(
    *, model: ModelDefinition, grant: PersonalRetailModelGrant
) -> str:
    payload = {
        "scope": "personal_retail",
        "model_id": model.id,
        "model_capability_version": model.capability_version,
        "model_billing_mode": model.billing_mode,
        "relay_capability_revision": model.relay_capability_revision,
        "grant_id": grant.id,
        "grant_updated_at": _quote_timestamp(grant.updated_at),
        "enabled": grant.enabled,
        "price_per_second_points": grant.price_per_second_points,
        "price_per_item_points": grant.price_per_item_points,
        "call_quota": grant.call_quota,
        "concurrency_limit": grant.concurrency_limit,
        "config_override": grant.config_override,
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PersonalWorkspaceService:
    @staticmethod
    def ensure(session: Session, *, user_id: str) -> PersonalWorkspace:
        """Explicitly provision a personal account.

        Request handlers use ``require`` instead.  Keeping provisioning separate
        prevents an enterprise invitee from becoming a personal user merely by
        probing a personal endpoint.
        """

        user = session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None:
            raise NotFoundError("用户不存在")
        account_type = AccountPartitionService.resolve(session, user=user)
        if account_type != AccountProductType.PERSONAL:
            raise PermissionDeniedError("当前账号类型不能开通个人空间")
        workspace = session.scalar(
            select(PersonalWorkspace).where(PersonalWorkspace.user_id == user_id)
        )
        if workspace is None:
            workspace_id = new_id()
            timestamp = utcnow()
            values = {
                "id": workspace_id,
                "user_id": user_id,
                "active": True,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            dialect_name = session.get_bind().dialect.name
            if dialect_name == "postgresql":
                insert_statement = postgresql_insert(PersonalWorkspace)
            elif dialect_name == "sqlite":
                insert_statement = sqlite_insert(PersonalWorkspace)
            else:
                raise RuntimeError(
                    f"personal workspace provisioning is not implemented for {dialect_name}"
                )
            session.execute(
                insert_statement.values(**values).on_conflict_do_nothing(
                    index_elements=["user_id"]
                )
            )
            workspace = session.scalar(
                select(PersonalWorkspace).where(PersonalWorkspace.user_id == user_id)
            )
        if workspace is None or not workspace.active:
            raise PermissionDeniedError("个人空间不可用")

        wallet_values = {
            "workspace_id": workspace.id,
            "available_points": 0,
            "reserved_points": 0,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        if session.get_bind().dialect.name == "postgresql":
            wallet_insert = postgresql_insert(PersonalWalletAccount)
        else:
            wallet_insert = sqlite_insert(PersonalWalletAccount)
        session.execute(
            wallet_insert.values(**wallet_values).on_conflict_do_nothing(
                index_elements=["workspace_id"]
            )
        )
        session.flush()
        return workspace

    @staticmethod
    def ensure_owner_self(
        session: Session,
        *,
        user_id: str,
        external_identity_id: str,
    ) -> PersonalWorkspace:
        """Explicitly link the authenticated Platform Owner to a self workspace.

        The caller must first prove the configured Owner subject. This method
        then locks and binds the exact local external identity, and never
        provisions a workspace for another administrator.
        """

        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        identity = session.scalar(
            select(ExternalIdentity)
            .where(
                ExternalIdentity.id == external_identity_id,
                ExternalIdentity.user_id == user_id,
            )
            .with_for_update()
        )
        if (
            user is None
            or identity is None
            or not user.is_platform_admin
            or user.account_type.value != "platform_admin"
        ):
            raise PermissionDeniedError("平台所有者个人创作主体不可用")
        workspace = session.scalar(
            select(PersonalWorkspace)
            .where(PersonalWorkspace.user_id == user_id)
            .with_for_update()
        )
        if workspace is None:
            workspace = PersonalWorkspace(
                user_id=user_id,
                active=True,
                owner_self_identity_id=identity.id,
            )
            session.add(workspace)
        else:
            if workspace.owner_self_identity_id not in (None, identity.id):
                raise PermissionDeniedError("平台所有者个人创作主体身份不匹配")
            workspace.owner_self_identity_id = identity.id
            workspace.active = True
        session.flush()
        PersonalWorkspaceService._ensure_wallet(session, workspace=workspace)
        return workspace

    @staticmethod
    def _ensure_wallet(session: Session, *, workspace: PersonalWorkspace) -> None:
        wallet_values = {
            "workspace_id": workspace.id,
            "available_points": 0,
            "reserved_points": 0,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        if session.get_bind().dialect.name == "postgresql":
            wallet_insert = postgresql_insert(PersonalWalletAccount)
        else:
            wallet_insert = sqlite_insert(PersonalWalletAccount)
        session.execute(
            wallet_insert.values(**wallet_values).on_conflict_do_nothing(
                index_elements=["workspace_id"]
            )
        )
        session.flush()

    @staticmethod
    def require(session: Session, *, user_id: str) -> PersonalWorkspace:
        user = session.get(User, user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        workspace = AccountPartitionService.require_personal(session, user=user)

        PersonalWorkspaceService._ensure_wallet(session, workspace=workspace)
        return workspace

    @staticmethod
    def require_for_product_context(
        session: Session,
        *,
        user_id: str,
        active_product_context: ProductContext,
        external_identity_id: str | None,
    ) -> PersonalWorkspace:
        user = session.get(User, user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        workspace = AccountPartitionService.require_personal_product_context(
            session,
            user=user,
            active_product_context=active_product_context,
            external_identity_id=external_identity_id,
        )
        PersonalWorkspaceService._ensure_wallet(session, workspace=workspace)
        return workspace

    @staticmethod
    def surfaces(
        session: Session,
        *,
        user_id: str,
        active_product_context: ProductContext | None = None,
        external_identity_id: str | None = None,
        platform_owner: bool = False,
    ) -> dict[str, Any]:
        user = session.get(User, user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        persisted_type = AccountPartitionService.resolve(session, user=user)
        if active_product_context is None:
            active_product_context = {
                AccountProductType.PERSONAL: ProductContext.PERSONAL,
                AccountProductType.COMPANY: ProductContext.COMPANY,
                AccountProductType.PLATFORM_ADMIN: ProductContext.PLATFORM,
            }.get(persisted_type)
        workspace = None
        if active_product_context == ProductContext.PERSONAL:
            workspace = PersonalWorkspaceService.require_for_product_context(
                session,
                user_id=user_id,
                active_product_context=active_product_context,
                external_identity_id=external_identity_id,
            )
        companies = (
            list(
                session.execute(
                    select(Company.id, Company.name, Company.status)
                    .join(
                        CompanyMembership,
                        CompanyMembership.company_id == Company.id,
                    )
                    .where(
                        CompanyMembership.user_id == user_id,
                        CompanyMembership.status == MembershipStatus.ACTIVE,
                    )
                    .order_by(Company.name, Company.id)
                ).mappings()
            )
            if active_product_context == ProductContext.COMPANY
            else []
        )
        available_contexts: list[str] = []
        if persisted_type == AccountProductType.PERSONAL:
            available_contexts.append(ProductContext.PERSONAL.value)
        elif persisted_type == AccountProductType.COMPANY:
            available_contexts.append(ProductContext.COMPANY.value)
        elif persisted_type == AccountProductType.PLATFORM_ADMIN:
            available_contexts.append(ProductContext.PLATFORM.value)
            if platform_owner:
                available_contexts.append(ProductContext.PERSONAL.value)
        effective_account_type = {
            ProductContext.PERSONAL: AccountProductType.PERSONAL.value,
            ProductContext.COMPANY: AccountProductType.COMPANY.value,
            ProductContext.PLATFORM: AccountProductType.PLATFORM_ADMIN.value,
        }.get(active_product_context, AccountProductType.UNAVAILABLE.value)
        return {
            "account_type": effective_account_type,
            "active_product_context": (
                active_product_context.value if active_product_context else None
            ),
            "available_product_contexts": available_contexts,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
            },
            "personal": (
                {
                    "kind": "personal",
                    "workspace_id": workspace.id,
                    "label": "个人创作",
                    "capabilities": {
                        "generation": True,
                        "models": True,
                        "tasks": True,
                        "artworks": True,
                        "task_cancel": False,
                        "assets": True,
                        "artifact_access": True,
                        "publishing": False,
                    },
                }
                if workspace is not None
                else None
            ),
            "companies": [
                {
                    "kind": "company",
                    "company_id": company["id"],
                    "name": company["name"],
                    "status": company["status"].value,
                }
                for company in companies
            ],
            "platform_admin": active_product_context == ProductContext.PLATFORM,
        }


class PersonalModelService:
    _CATALOG_REASONS: dict[str, str] = {
        "model_unpublished": "模型仍在接入审核中，尚未发布。",
        "model_disabled": "模型已暂停开放，暂不能开始新任务。",
        "relay_capability_unapproved": "模型能力仍在审核中，暂不能开始生成。",
        "personal_distribution_unconfigured": "个人零售尚未开放，等待平台配置积分价格。",
        "personal_distribution_disabled": "个人零售暂未开放。",
        "personal_price_unavailable": "个人积分价格尚未配置。",
        "personal_capability_unavailable": "该模型暂没有适用于个人空间的生成方式。",
    }

    @staticmethod
    def _effective(
        session: Session,
        *,
        model: ModelDefinition,
        grant: PersonalRetailModelGrant,
        require_usable: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        capabilities = list(
            session.scalars(
                select(ModelCapability)
                .where(ModelCapability.model_id == model.id)
                .order_by(ModelCapability.capability_key)
            ).all()
        )
        capability_map = {
            capability.capability_key: capability.config for capability in capabilities
        }
        effective = TaskCapabilityAdmission.effective_capabilities(
            capability_map=capability_map,
            config_override=grant.config_override,
            require_usable=require_usable,
        )
        supported_modes = {
            mode: {
                **config,
                # Personal workspaces do not have company resource grants and
                # the first retail contract explicitly rejects face handling.
                "supports_face": False,
                "conditional_required_resource_keys": {},
                # Personal per-second retail currently quotes exactly one
                # output. Advertise that same admission rule to the client so
                # the capability contract cannot present a later-rejected
                # output count.
                **(
                    {
                        "limits": {
                            **config.get("limits", {}),
                            "output_counts": [1],
                        }
                    }
                    if model.billing_mode == "per_second"
                    else {}
                ),
            }
            for mode, config in effective.get("modes", {}).items()
            if mode in PERSONAL_GENERATION_MODES
            and not config.get("required_resource_keys")
        }
        effective = {**effective, "modes": supported_modes}
        if require_usable and not supported_modes:
            raise ConflictError("零售模型没有可供个人空间使用的生成模式")
        return capability_map, effective

    @staticmethod
    def usage_blockers(
        session: Session,
        *,
        workspace_id: str,
        model: ModelDefinition,
        grant: PersonalRetailModelGrant,
    ) -> tuple[dict[str, Any], ...]:
        """Evaluate one retail policy against one personal workspace.

        The grant is global retail policy, while consumption is deliberately
        isolated by ``personal_workspace_id + model_id``.  Call quota counts
        every accepted task; concurrency counts only non-terminal tasks.
        """

        blockers: list[dict[str, Any]] = []
        scope_filters = (
            GenerationTask.company_id.is_(None),
            GenerationTask.personal_workspace_id == workspace_id,
            GenerationTask.model_id == model.id,
        )
        if grant.call_quota is not None:
            used_calls = int(
                session.scalar(
                    select(func.count(GenerationTask.id)).where(*scope_filters)
                )
                or 0
            )
            if used_calls >= grant.call_quota:
                blockers.append(
                    {
                        "code": "model_call_quota_exhausted",
                        "message": f"{model.display_name}的个人调用额度已用完。",
                        "resource_key": f"model:{model.id}",
                        "resource_name": model.display_name,
                        "retryable": False,
                    }
                )
        if grant.concurrency_limit is not None:
            active_calls = int(
                session.scalar(
                    select(func.count(GenerationTask.id)).where(
                        *scope_filters,
                        GenerationTask.status.in_(
                            (
                                TaskStatus.DRAFT,
                                TaskStatus.QUEUED,
                                TaskStatus.PROCESSING,
                            )
                        ),
                    )
                )
                or 0
            )
            if active_calls >= grant.concurrency_limit:
                blockers.append(
                    {
                        "code": "model_concurrency_saturated",
                        "message": (
                            f"{model.display_name}当前个人任务已达并发上限，"
                            "请稍后重试。"
                        ),
                        "resource_key": f"model:{model.id}",
                        "resource_name": model.display_name,
                        "retryable": True,
                    }
                )
        return tuple(blockers)

    @classmethod
    def require_usage_admission(
        cls,
        session: Session,
        *,
        workspace_id: str,
        model: ModelDefinition,
        grant: PersonalRetailModelGrant,
    ) -> None:
        blockers = cls.usage_blockers(
            session,
            workspace_id=workspace_id,
            model=model,
            grant=grant,
        )
        if not blockers:
            return
        if blockers[0]["code"] == "model_call_quota_exhausted":
            raise PermissionDeniedError("Personal model call quota is exhausted")
        raise PermissionDeniedError("Personal model concurrency limit is reached")

    @staticmethod
    def list_available(
        session: Session,
        *,
        workspace_id: str,
        require_relay_approval: bool = False,
        release_evidence: RelayModelReleaseEvidence | None = None,
    ) -> list[dict[str, Any]]:
        checked_at = utcnow()
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
        rows = list(
            session.execute(
                select(ModelDefinition, PersonalRetailModelGrant)
                .join(
                    PersonalRetailModelGrant,
                    PersonalRetailModelGrant.model_id == ModelDefinition.id,
                )
                .where(
                    ModelDefinition.active.is_(True),
                    ModelDefinition.published_at.is_not(None),
                    *relay_conditions,
                    PersonalRetailModelGrant.enabled.is_(True),
                )
                .order_by(ModelDefinition.display_name, ModelDefinition.id)
            ).all()
        )
        result: list[dict[str, Any]] = []
        for model, grant in rows:
            _, effective = PersonalModelService._effective(
                session, model=model, grant=grant, require_usable=False
            )
            if not effective.get("modes"):
                continue
            unit_price = (
                grant.price_per_second_points
                if model.billing_mode == "per_second"
                else grant.price_per_item_points
            )
            if unit_price is None or unit_price <= 0:
                continue
            blockers = list(
                PersonalModelService.usage_blockers(
                    session,
                    workspace_id=workspace_id,
                    model=model,
                    grant=grant,
                )
            )
            blockers.extend(
                CommercialPricingPolicy.distribution_blockers(
                    session, model=model, release_evidence=release_evidence
                )
            )
            result.append(
                {
                    "id": model.id,
                    "slug": model.slug,
                    "display_name": model.display_name,
                    "billing_mode": model.billing_mode,
                    "unit_price_points": unit_price,
                    "capability_version": model.capability_version,
                    "quote_revision": _retail_quote_revision(model=model, grant=grant),
                    "call_quota": grant.call_quota,
                    "concurrency_limit": grant.concurrency_limit,
                    "effective_capabilities": effective,
                    "readiness_checked_at": checked_at,
                    "mode_readiness": {
                        mode: {
                            "default": {
                                "ready": not blockers,
                                "status": "ready" if not blockers else "blocked",
                                "blockers": blockers,
                            },
                            "options": {
                                "face_enabled": {
                                    "supported": False,
                                    "ready": False,
                                    "status": "unsupported",
                                    "blockers": [],
                                }
                            },
                        }
                        for mode in effective.get("modes", {})
                    },
                }
            )
        return result

    @classmethod
    def list_catalog(
        cls, session: Session, *, require_relay_approval: bool = False
    ) -> list[dict[str, Any]]:
        """Return a public-safe personal catalog without executable capabilities.

        The generation endpoint remains the authority for selection.  This view
        intentionally exposes only product presentation metadata and a normalized
        availability reason; Relay candidates, routes and provider material never
        cross the personal boundary.
        """
        rows = list(
            session.execute(
                select(ModelDefinition, PersonalRetailModelGrant)
                .outerjoin(
                    PersonalRetailModelGrant,
                    PersonalRetailModelGrant.model_id == ModelDefinition.id,
                )
                .order_by(ModelDefinition.display_name, ModelDefinition.id)
            ).all()
        )
        result: list[dict[str, Any]] = []
        for model, grant in rows:
            reason_code: str | None = None
            if model.published_at is None:
                reason_code = "model_unpublished"
            elif not model.active:
                reason_code = "model_disabled"
            elif require_relay_approval and not (
                model.relay_capability_revision is not None
                and model.relay_capability_approved_ceiling is not None
                and model.relay_capability_candidate_revision
                == model.relay_capability_revision
            ):
                reason_code = "relay_capability_unapproved"
            elif (
                not require_relay_approval
                and model.relay_capability_candidate_revision is not None
                and model.relay_capability_candidate_revision
                != model.relay_capability_revision
            ):
                reason_code = "relay_capability_unapproved"
            elif grant is None:
                reason_code = "personal_distribution_unconfigured"
            elif not grant.enabled:
                reason_code = "personal_distribution_disabled"
            else:
                unit_price = (
                    grant.price_per_second_points
                    if model.billing_mode == "per_second"
                    else grant.price_per_item_points
                )
                if unit_price is None or unit_price <= 0:
                    reason_code = "personal_price_unavailable"
                else:
                    try:
                        _, effective = cls._effective(
                            session,
                            model=model,
                            grant=grant,
                            require_usable=False,
                        )
                    except ConflictError:
                        reason_code = "personal_capability_unavailable"
                    else:
                        if not effective.get("modes"):
                            reason_code = "personal_capability_unavailable"

            result.append(
                {
                    "id": model.id,
                    "slug": model.slug,
                    "display_name": model.display_name,
                    "billing_mode": model.billing_mode,
                    "capability_version": model.capability_version,
                    "available": reason_code is None,
                    "unavailable_reason": (
                        {
                            "code": reason_code,
                            "message": cls._CATALOG_REASONS[reason_code],
                        }
                        if reason_code is not None
                        else None
                    ),
                }
            )
        return result


class PersonalRetailGrantService:
    """Platform-admin retail catalog; never shares company wallet pricing."""

    @staticmethod
    def _model_status(model: ModelDefinition) -> str:
        if model.published_at is None:
            return "draft"
        return "published" if model.active else "disabled"

    @classmethod
    def response(
        cls,
        session: Session,
        *,
        model: ModelDefinition,
        grant: PersonalRetailModelGrant | None,
    ) -> dict[str, Any]:
        preview_grant = grant or PersonalRetailModelGrant(
            model_id=model.id,
            enabled=False,
            price_per_second_points=(
                1 if model.billing_mode == "per_second" else None
            ),
            price_per_item_points=(
                1 if model.billing_mode == "per_item" else None
            ),
            config_override={},
        )
        try:
            _, effective = PersonalModelService._effective(
                session,
                model=model,
                grant=preview_grant,
                require_usable=False,
            )
        except ConflictError:
            effective = {"schema_version": 1, "modes": {}}
        return {
            "model_id": model.id,
            "model_slug": model.slug,
            "model_display_name": model.display_name,
            "model_status": cls._model_status(model),
            "capability_version": model.capability_version,
            "relay_capability_revision": model.relay_capability_revision,
            "grant_id": grant.id if grant is not None else None,
            "enabled": bool(grant.enabled) if grant is not None else False,
            "price_per_second_points": (
                grant.price_per_second_points if grant is not None else None
            ),
            "price_per_item_points": (
                grant.price_per_item_points if grant is not None else None
            ),
            "call_quota": grant.call_quota if grant is not None else None,
            "concurrency_limit": (
                grant.concurrency_limit if grant is not None else None
            ),
            "config_override": (
                grant.config_override if grant is not None else {}
            ),
            "quote_revision": (
                _retail_quote_revision(model=model, grant=grant)
                if grant is not None
                else None
            ),
            "effective_capabilities": effective,
            "created_at": grant.created_at if grant is not None else None,
            "updated_at": grant.updated_at if grant is not None else None,
        }

    @classmethod
    def list_all(cls, session: Session) -> list[dict[str, Any]]:
        rows = session.execute(
            select(ModelDefinition, PersonalRetailModelGrant)
            .outerjoin(
                PersonalRetailModelGrant,
                PersonalRetailModelGrant.model_id == ModelDefinition.id,
            )
            .order_by(ModelDefinition.display_name, ModelDefinition.id)
        ).all()
        return [
            cls.response(session, model=model, grant=grant)
            for model, grant in rows
        ]

    @classmethod
    def upsert(
        cls,
        session: Session,
        *,
        model_id: str,
        expected_capability_version: int,
        expected_quote_revision: str | None,
        enabled: bool,
        price_per_second_points: int | None,
        price_per_item_points: int | None,
        config_override: dict[str, Any],
        require_relay_approval: bool,
        expected_release_snapshot: Mapping[str, Any] | None = None,
        publishing_plan_id: str | None = None,
        call_quota: int | None | object = _PERSONAL_LIMIT_UNSET,
        concurrency_limit: int | None | object = _PERSONAL_LIMIT_UNSET,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
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
        if model.capability_version != expected_capability_version:
            raise ConflictError("模型能力版本已变化，请刷新后重试")
        has_second = price_per_second_points is not None
        has_item = price_per_item_points is not None
        if has_second == has_item:
            raise ConflictError("个人零售模型必须且只能设置一种积分价格")
        configured_mode = "per_second" if has_second else "per_item"
        if configured_mode != model.billing_mode:
            raise ConflictError("个人零售积分价格与模型目录计费方式不一致")
        if enabled and publishing_plan_id is None and (not model.active or model.published_at is None):
            raise ConflictError("只有已发布且启用的模型才能开放个人零售")
        if enabled and require_relay_approval and (
            model.relay_capability_revision is None
            or model.relay_capability_approved_ceiling is None
            or not ModelCatalogService.relay_candidate_is_approved(model)
        ):
            raise ConflictError("模型尚未批准 Relay 能力版本，不能开放个人零售")
        grant = session.scalar(
            select(PersonalRetailModelGrant)
            .where(PersonalRetailModelGrant.model_id == model_id)
            .with_for_update()
        )
        current_quote_revision = (
            _retail_quote_revision(model=model, grant=grant)
            if grant is not None
            else None
        )
        if current_quote_revision != expected_quote_revision:
            raise ConflictError("个人零售模型分发配置已变化，请刷新后重试")
        desired_call_quota = (
            grant.call_quota
            if call_quota is _PERSONAL_LIMIT_UNSET and grant is not None
            else None if call_quota is _PERSONAL_LIMIT_UNSET else call_quota
        )
        desired_concurrency_limit = (
            grant.concurrency_limit
            if concurrency_limit is _PERSONAL_LIMIT_UNSET and grant is not None
            else (
                None
                if concurrency_limit is _PERSONAL_LIMIT_UNSET
                else concurrency_limit
            )
        )
        (
            normalized_call_quota,
            normalized_concurrency_limit,
            _,
            _,
        ) = normalize_entitlement_policy(
            call_quota=desired_call_quota,
            concurrency_limit=desired_concurrency_limit,
            effective_at=None,
            expires_at=None,
        )
        CommercialPricingPolicy.require_price(
            session,
            model=model,
            unit_price=price_per_second_points if has_second else price_per_item_points,
            billing_unit="POINT",
            enabled=enabled,
            current_unit_price=(
                grant.price_per_second_points or grant.price_per_item_points
                if grant is not None else None
            ),
            config_override=config_override,
            scope="personal",
            expected_release_snapshot=expected_release_snapshot,
            publishing_plan_id=publishing_plan_id,
        )
        before = cls.response(session, model=model, grant=grant)
        if grant is None:
            grant = PersonalRetailModelGrant(
                model_id=model.id,
                enabled=enabled,
                price_per_second_points=price_per_second_points,
                price_per_item_points=price_per_item_points,
                call_quota=normalized_call_quota,
                concurrency_limit=normalized_concurrency_limit,
                config_override=config_override,
            )
            session.add(grant)
        else:
            grant.enabled = enabled
            grant.price_per_second_points = price_per_second_points
            grant.price_per_item_points = price_per_item_points
            grant.call_quota = normalized_call_quota
            grant.concurrency_limit = normalized_concurrency_limit
            grant.config_override = config_override
        # This validation both enforces the Platform ceiling and excludes
        # company-only resource/media modes from the personal retail surface.
        PersonalModelService._effective(
            session,
            model=model,
            grant=grant,
            require_usable=enabled,
        )
        session.flush()
        after = cls.response(session, model=model, grant=grant)
        comparable_fields = (
            "enabled",
            "price_per_second_points",
            "price_per_item_points",
            "call_quota",
            "concurrency_limit",
            "config_override",
        )
        changed = any(before[field] != after[field] for field in comparable_fields)
        return before, after, changed

    @staticmethod
    def _normalize_batch_changes(
        changes: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen_model_ids: set[str] = set()
        for raw in changes:
            if not isinstance(raw, dict):
                raise ConflictError("个人模型批量分发项必须是对象")
            model_id = str(raw.get("model_id") or "").strip()
            if not model_id:
                raise ConflictError("个人模型批量分发缺少 model_id")
            if model_id in seen_model_ids:
                raise ConflictError("个人模型批量分发不能重复包含同一模型")
            seen_model_ids.add(model_id)
            expected_version = raw.get("expected_capability_version")
            if isinstance(expected_version, bool) or not isinstance(
                expected_version, int
            ) or expected_version < 1:
                raise ConflictError("个人模型批量分发能力版本无效")
            expected_quote_revision = raw.get("expected_quote_revision")
            if expected_quote_revision is not None and (
                not isinstance(expected_quote_revision, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_quote_revision)
                is None
            ):
                raise ConflictError("个人模型批量分发报价版本无效")
            enabled = raw.get("enabled")
            if not isinstance(enabled, bool):
                raise ConflictError("个人模型批量分发 enabled 必须是布尔值")
            config_override = raw.get("config_override", {})
            if not isinstance(config_override, dict):
                raise ConflictError("个人模型批量分发能力限制必须是对象")
            result.append(
                {
                    "model_id": model_id,
                    "expected_capability_version": expected_version,
                    "expected_quote_revision": expected_quote_revision,
                    "enabled": enabled,
                    "price_per_second_points": raw.get(
                        "price_per_second_points"
                    ),
                    "price_per_item_points": raw.get("price_per_item_points"),
                    "config_override": config_override,
                    **(
                        {"call_quota": raw.get("call_quota")}
                        if "call_quota" in raw
                        else {}
                    ),
                    **(
                        {"concurrency_limit": raw.get("concurrency_limit")}
                        if "concurrency_limit" in raw
                        else {}
                    ),
                }
            )
        if not result:
            raise ConflictError("个人模型批量分发至少需要一项变更")
        if len(result) > 100:
            raise ConflictError("个人模型批量分发最多支持 100 个模型")
        return sorted(result, key=lambda item: item["model_id"])

    @classmethod
    def preview_batch(
        cls,
        session: Session,
        *,
        changes: Iterable[dict[str, Any]],
        require_relay_approval: bool,
        lock_rows: bool = False,
        expected_release_snapshots: Mapping[
            str, Mapping[str, Any]
        ] | None = None,
    ) -> dict[str, Any]:
        """Validate and fingerprint an all-or-none personal distribution batch."""

        normalized = cls._normalize_batch_changes(changes)
        model_ids = [item["model_id"] for item in normalized]
        model_statement = (
            select(ModelDefinition)
            .where(ModelDefinition.id.in_(model_ids))
            .order_by(ModelDefinition.id)
        )
        grant_statement = (
            select(PersonalRetailModelGrant)
            .where(PersonalRetailModelGrant.model_id.in_(model_ids))
            .order_by(PersonalRetailModelGrant.model_id)
        )
        if lock_rows:
            model_statement = model_statement.execution_options(
                populate_existing=True
            ).with_for_update()
            grant_statement = grant_statement.with_for_update()
        models = {model.id: model for model in session.scalars(model_statement)}
        grants = {
            grant.model_id: grant for grant in session.scalars(grant_statement)
        }
        missing = sorted(set(model_ids) - set(models))
        if missing:
            raise NotFoundError(f"模型不存在: {', '.join(missing)}")
        for model_id, snapshot in (expected_release_snapshots or {}).items():
            model = models.get(model_id)
            if model is None:
                raise NotFoundError(f"模型不存在: {model_id}")
            require_model_release_snapshot(
                model=model,
                expected_snapshot=snapshot,
            )

        cells: list[dict[str, Any]] = []
        for desired in normalized:
            model = models[desired["model_id"]]
            grant = grants.get(model.id)
            if model.capability_version != desired["expected_capability_version"]:
                raise ConflictError(
                    f"模型能力版本已变化，请刷新后重试 (model_id={model.id})"
                )
            current_quote_revision = (
                _retail_quote_revision(model=model, grant=grant)
                if grant is not None
                else None
            )
            if current_quote_revision != desired["expected_quote_revision"]:
                raise ConflictError(
                    f"个人零售模型分发配置已变化，请刷新后重试 (model_id={model.id})"
                )
            has_second = desired["price_per_second_points"] is not None
            has_item = desired["price_per_item_points"] is not None
            if has_second == has_item:
                raise ConflictError(
                    f"个人零售模型必须且只能设置一种积分价格 (model_id={model.id})"
                )
            configured_mode = "per_second" if has_second else "per_item"
            if configured_mode != model.billing_mode:
                raise ConflictError(
                    f"个人零售积分价格与模型目录计费方式不一致 (model_id={model.id})"
                )
            if desired["enabled"] and (
                not model.active or model.published_at is None
            ):
                raise ConflictError(
                    f"只有已发布且启用的模型才能开放个人零售 (model_id={model.id})"
                )
            if desired["enabled"] and require_relay_approval and (
                model.relay_capability_revision is None
                or model.relay_capability_approved_ceiling is None
                or not ModelCatalogService.relay_candidate_is_approved(model)
            ):
                raise ConflictError(
                    f"模型尚未批准 Relay 能力版本，不能开放个人零售 (model_id={model.id})"
                )
            desired_call_quota = (
                desired["call_quota"]
                if "call_quota" in desired
                else grant.call_quota if grant is not None else None
            )
            desired_concurrency_limit = (
                desired["concurrency_limit"]
                if "concurrency_limit" in desired
                else grant.concurrency_limit if grant is not None else None
            )
            (
                desired_call_quota,
                desired_concurrency_limit,
                _,
                _,
            ) = normalize_entitlement_policy(
                call_quota=desired_call_quota,
                concurrency_limit=desired_concurrency_limit,
                effective_at=None,
                expires_at=None,
            )
            desired_grant = PersonalRetailModelGrant(
                id=grant.id if grant is not None else f"preview:{model.id}",
                model_id=model.id,
                enabled=desired["enabled"],
                price_per_second_points=desired["price_per_second_points"],
                price_per_item_points=desired["price_per_item_points"],
                call_quota=desired_call_quota,
                concurrency_limit=desired_concurrency_limit,
                config_override=desired["config_override"],
            )
            CommercialPricingPolicy.require_price(
                session,
                model=model,
                unit_price=(desired["price_per_second_points"] if has_second else desired["price_per_item_points"]),
                billing_unit="POINT",
                enabled=desired["enabled"],
                current_unit_price=(
                    grant.price_per_second_points or grant.price_per_item_points
                    if grant is not None else None
                ),
                config_override=desired["config_override"],
                scope="personal",
                expected_release_snapshot=(expected_release_snapshots or {}).get(model.id),
            )
            _, effective = PersonalModelService._effective(
                session,
                model=model,
                grant=desired_grant,
                require_usable=desired["enabled"],
            )
            before = cls.response(session, model=model, grant=grant)
            after = {
                **before,
                "enabled": desired["enabled"],
                "price_per_second_points": desired[
                    "price_per_second_points"
                ],
                "price_per_item_points": desired["price_per_item_points"],
                "call_quota": desired_call_quota,
                "concurrency_limit": desired_concurrency_limit,
                "config_override": desired["config_override"],
                "effective_capabilities": effective,
            }
            comparable_fields = (
                "enabled",
                "price_per_second_points",
                "price_per_item_points",
                "call_quota",
                "concurrency_limit",
                "config_override",
            )
            operation = (
                "noop"
                if all(before[field] == after[field] for field in comparable_fields)
                else ("create" if grant is None else "update")
            )
            cells.append(
                {
                    "model_id": model.id,
                    "model_slug": model.slug,
                    "model_display_name": model.display_name,
                    "capability_version": model.capability_version,
                    "operation": operation,
                    "before": {
                        field: before[field]
                        for field in (
                            *comparable_fields,
                            "quote_revision",
                            "relay_capability_revision",
                        )
                    },
                    "after": {
                        field: after[field] for field in comparable_fields
                    },
                }
            )
        snapshot = _personal_distribution_hash({"cells": cells})
        return {
            "snapshot": snapshot,
            "total_cells": len(cells),
            "changed_cells": sum(cell["operation"] != "noop" for cell in cells),
            "created_cells": sum(cell["operation"] == "create" for cell in cells),
            "updated_cells": sum(cell["operation"] == "update" for cell in cells),
            "cells": cells,
        }

    @classmethod
    def claim_batch(
        cls,
        session: Session,
        *,
        changes: Iterable[dict[str, Any]],
        expected_snapshot: str,
        actor_user_id: str,
        reason: str,
        request_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_reason = reason.strip()
        if not 3 <= len(normalized_reason) <= 500:
            raise ConflictError("变更原因必须包含 3 到 500 个字符")
        normalized_key = idempotency_key.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}", normalized_key) is None:
            raise ConflictError("幂等键必须是 8 到 120 个安全字符")
        normalized = cls._normalize_batch_changes(changes)
        request_hash = _personal_distribution_hash(
            {
                "actor_user_id": actor_user_id,
                "changes": normalized,
                "expected_snapshot": expected_snapshot,
                "reason": normalized_reason,
            }
        )
        journal_id = new_id()
        values = {
            "id": journal_id,
            "idempotency_key": normalized_key,
            "actor_user_id": actor_user_id,
            "request_sha256": request_hash,
            "expected_snapshot": expected_snapshot,
            "state": "pending",
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert_statement = postgresql_insert(
                PersonalModelGrantBatchJournal
            ).values(**values)
        elif dialect_name == "sqlite":
            insert_statement = sqlite_insert(
                PersonalModelGrantBatchJournal
            ).values(**values)
        else:  # pragma: no cover - production supports PostgreSQL; tests use SQLite.
            raise RuntimeError("unsupported Platform database dialect")
        claimed_id = session.scalar(
            insert_statement.on_conflict_do_nothing(
                index_elements=["idempotency_key"]
            ).returning(PersonalModelGrantBatchJournal.id)
        )
        journal = (
            session.get(PersonalModelGrantBatchJournal, claimed_id)
            if claimed_id is not None
            else session.scalar(
                select(PersonalModelGrantBatchJournal)
                .where(
                    PersonalModelGrantBatchJournal.idempotency_key
                    == normalized_key
                )
                .with_for_update()
            )
        )
        if journal is None:
            raise ConflictError("个人模型批量分发幂等记录暂不可用，请安全重试")
        if claimed_id is None:
            if (
                journal.actor_user_id != actor_user_id
                or journal.request_sha256 != request_hash
                or journal.expected_snapshot != expected_snapshot
            ):
                raise ConflictError("幂等键已被另一批个人模型分发占用")
            if journal.state == "succeeded" and isinstance(
                journal.result_payload, dict
            ):
                return {
                    "journal": journal,
                    "normalized": normalized,
                    "normalized_reason": normalized_reason,
                    "request_hash": request_hash,
                    "replay": {
                        **journal.result_payload,
                        "idempotent_replay": True,
                    },
                }
            raise ConflictError("同一幂等键的个人模型批量分发仍在处理中")
        return {
            "journal": journal,
            "normalized": normalized,
            "normalized_reason": normalized_reason,
            "request_hash": request_hash,
            "replay": None,
        }

    @classmethod
    def execute_batch(
        cls,
        session: Session,
        *,
        changes: Iterable[dict[str, Any]],
        expected_snapshot: str,
        actor_user_id: str,
        reason: str,
        request_id: str,
        idempotency_key: str,
        require_relay_approval: bool,
        claim: dict[str, Any] | None = None,
        expected_release_snapshots: Mapping[
            str, Mapping[str, Any]
        ] | None = None,
    ) -> dict[str, Any]:
        batch_claim = claim or cls.claim_batch(
            session,
            changes=changes,
            expected_snapshot=expected_snapshot,
            actor_user_id=actor_user_id,
            reason=reason,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        replay = batch_claim.get("replay")
        if isinstance(replay, dict):
            return replay
        normalized = batch_claim["normalized"]
        normalized_reason = batch_claim["normalized_reason"]
        request_hash = batch_claim["request_hash"]
        journal = batch_claim["journal"]
        normalized_key = journal.idempotency_key
        if journal.state != "pending" or journal.result_payload is not None:
            raise ConflictError("个人模型批量分发幂等记录状态无效")

        preview = cls.preview_batch(
            session,
            changes=normalized,
            require_relay_approval=require_relay_approval,
            lock_rows=True,
            expected_release_snapshots=expected_release_snapshots,
        )
        if preview["snapshot"] != expected_snapshot:
            raise ConflictError("个人模型分发在预览后已变化，请重新预览")

        desired_by_model = {item["model_id"]: item for item in normalized}
        applied: list[dict[str, Any]] = []
        for cell in preview["cells"]:
            if cell["operation"] == "noop":
                continue
            desired = desired_by_model[cell["model_id"]]
            before, after, changed = cls.upsert(
                session,
                model_id=cell["model_id"],
                expected_capability_version=desired[
                    "expected_capability_version"
                ],
                expected_quote_revision=desired["expected_quote_revision"],
                enabled=desired["enabled"],
                price_per_second_points=desired[
                    "price_per_second_points"
                ],
                price_per_item_points=desired["price_per_item_points"],
                config_override=desired["config_override"],
                require_relay_approval=require_relay_approval,
                expected_release_snapshot=(
                    (expected_release_snapshots or {}).get(cell["model_id"])
                ),
                **(
                    {"call_quota": desired["call_quota"]}
                    if "call_quota" in desired
                    else {}
                ),
                **(
                    {"concurrency_limit": desired["concurrency_limit"]}
                    if "concurrency_limit" in desired
                    else {}
                ),
            )
            if not changed:
                continue
            audit_fields = (
                "model_id",
                "model_slug",
                "capability_version",
                "relay_capability_revision",
                "grant_id",
                "enabled",
                "price_per_second_points",
                "price_per_item_points",
                "call_quota",
                "concurrency_limit",
                "config_override",
                "quote_revision",
            )
            AuditService.append(
                session,
                actor_user_id=actor_user_id,
                action="personal_model_grant.upsert",
                target_type="personal_retail_model_grant",
                target_id=cell["model_id"],
                before_summary={
                    **{field: before[field] for field in audit_fields},
                    "reason": normalized_reason,
                    "batch_id": normalized_key,
                },
                after_summary={
                    **{field: after[field] for field in audit_fields},
                    "reason": normalized_reason,
                    "batch_id": normalized_key,
                },
                request_id=request_id,
            )
            applied.append(_personal_distribution_json(after))

        result = {
            "batch_id": normalized_key,
            "snapshot": expected_snapshot,
            "applied_cell_count": len(applied),
            "items": applied,
            "idempotent_replay": False,
        }
        journal.state = "succeeded"
        journal.result_payload = _personal_distribution_json(result)
        journal.updated_at = utcnow()
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="personal_model_grant.batch",
            target_type="personal_model_grant_batch",
            target_id=normalized_key,
            before_summary={
                "reason": normalized_reason,
                "snapshot": expected_snapshot,
                "cells": [
                    {
                        "model_id": cell["model_id"],
                        "value": cell["before"],
                    }
                    for cell in preview["cells"]
                    if cell["operation"] != "noop"
                ],
            },
            after_summary={
                "reason": normalized_reason,
                "request_hash": request_hash,
                "cells": [
                    {
                        "model_id": cell["model_id"],
                        "value": cell["after"],
                    }
                    for cell in preview["cells"]
                    if cell["operation"] != "noop"
                ],
                "result": result,
            },
            request_id=request_id,
        )
        return result


class PersonalTaskService:
    @staticmethod
    def _validate_replay(
        task: GenerationTask, *, user_id: str, request_fingerprint: str
    ) -> GenerationTask:
        if task.user_id != user_id or task.request_fingerprint != request_fingerprint:
            raise ConflictError("幂等键已被个人空间的一笔不同任务请求使用")
        return task

    @staticmethod
    def idempotent_replay(
        session: Session,
        *,
        workspace_id: str,
        user_id: str,
        model_id: str,
        request_payload: dict[str, Any],
        idempotency_key: str,
    ) -> GenerationTask | None:
        """Return an exact accepted request before mutable asset checks."""

        if not isinstance(request_payload, dict):
            raise ConflictError("request_payload must be an object")
        request_fingerprint = TaskService.request_fingerprint(
            model_id=model_id,
            request_payload=request_payload,
        )
        existing = session.scalar(
            select(GenerationTask).where(
                GenerationTask.company_id.is_(None),
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        return PersonalTaskService._validate_replay(
            existing,
            user_id=user_id,
            request_fingerprint=request_fingerprint,
        )

    @staticmethod
    def _quote_and_snapshots(
        session: Session,
        *,
        model: ModelDefinition,
        grant: PersonalRetailModelGrant,
        request_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        mode = request_payload.get("mode", "text_to_video")
        if mode not in PERSONAL_GENERATION_MODES:
            raise ConflictError("个人空间不支持当前生成方式")
        if request_payload.get("face_enabled") is True:
            raise ConflictError("个人空间暂不支持人脸能力")

        capability_map, effective_capabilities = PersonalModelService._effective(
            session, model=model, grant=grant, require_usable=True
        )
        effective_capability = TaskCapabilityAdmission.validate(
            capability_map=capability_map,
            config_override=grant.config_override,
            request_payload=request_payload,
        )
        selected = effective_capability["modes"][mode]
        if selected.get("required_resource_keys"):
            raise PermissionDeniedError("个人空间不具备该模式所需的资源授权")

        has_second_price = grant.price_per_second_points is not None
        has_item_price = grant.price_per_item_points is not None
        if has_second_price == has_item_price:
            raise ConflictError("零售模型没有且仅有一种有效积分价格")
        configured_mode = "per_second" if has_second_price else "per_item"
        if configured_mode != model.billing_mode:
            raise ConflictError("零售积分价格与模型目录计费方式不一致")
        if model.billing_mode == "per_second":
            quantity = TaskService._positive_int(request_payload, "duration_seconds")
            output_count = TaskService._positive_int(
                request_payload, "output_count", default=1
            )
            if output_count != 1:
                raise ConflictError("按秒计费的个人任务只能生成 1 条结果")
            unit_price = grant.price_per_second_points
        else:
            quantity = TaskService._positive_int(
                request_payload, "output_count", default=1
            )
            unit_price = grant.price_per_item_points
        if unit_price is None or unit_price <= 0:
            raise ConflictError("零售模型没有有效积分价格")
        quote_points = unit_price * quantity
        if quote_points > MAX_MONEY_CENTS:
            raise ConflictError("任务积分报价超出系统上限")
        quote_revision = _retail_quote_revision(model=model, grant=grant)
        pricing_snapshot = {
            "schema_version": 2,
            "billing_unit": "POINT",
            "billing_version": 2,
            "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
            "scope": "personal_retail",
            "mode": model.billing_mode,
            "unit_price_points": unit_price,
            "quantity": quantity,
            "quote_points": quote_points,
            "grant_id": grant.id,
            "quote_revision": quote_revision,
            "grant_updated_at": _quote_timestamp(grant.updated_at),
            "call_quota": grant.call_quota,
            "concurrency_limit": grant.concurrency_limit,
        }
        capability_snapshot = {
            "model_id": model.id,
            "model_slug": model.slug,
            "capability_version": model.capability_version,
            "relay_capability_revision": model.relay_capability_revision,
            "capabilities": capability_map,
            "grant_config_override": grant.config_override,
            "effective_capabilities": effective_capabilities,
            "resource_grants": [],
        }
        return quote_points, pricing_snapshot, capability_snapshot

    @staticmethod
    def create(
        session: Session,
        *,
        workspace_id: str,
        user_id: str,
        model_id: str,
        request_payload: dict[str, Any],
        idempotency_key: str,
        expected_capability_version: int | None,
        expected_quote_revision: str | None,
        require_quote_revision: bool,
        require_relay_capability_revision: bool,
        relay_backend_id: str = NEW_API_RELAY_BACKEND_ID,
        relay_contract_revision: str = DEFAULT_RELAY_CONTRACT_REVISION,
        expected_commercial_release_snapshot: Mapping[str, Any] | None = None,
    ) -> tuple[GenerationTask, bool]:
        if not isinstance(request_payload, dict):
            raise ConflictError("request_payload must be an object")
        request_fingerprint = TaskService.request_fingerprint(
            model_id=model_id, request_payload=request_payload
        )
        existing = session.scalar(
            select(GenerationTask).where(
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.company_id.is_(None),
                GenerationTask.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return (
                PersonalTaskService._validate_replay(
                    existing,
                    user_id=user_id,
                    request_fingerprint=request_fingerprint,
                ),
                False,
            )
        if re.fullmatch(RELAY_BACKEND_ID_PATTERN, relay_backend_id) is None:
            raise ConflictError("Relay backend identity is invalid")
        if re.fullmatch(RELAY_CONTRACT_REVISION_PATTERN, relay_contract_revision) is None:
            raise ConflictError("Relay contract revision is invalid")
        if require_quote_revision and expected_quote_revision is None:
            raise ConflictError("A current retail quote revision is required")

        workspace = session.scalar(
            select(PersonalWorkspace)
            .where(
                PersonalWorkspace.id == workspace_id,
                PersonalWorkspace.user_id == user_id,
                PersonalWorkspace.active.is_(True),
            )
            # Still conflicts with workspace disable/update, but permits FK
            # KEY SHARE from a concurrent terminal ledger holding the wallet.
            .with_for_update(key_share=True)
        )
        if workspace is None:
            raise PermissionDeniedError("个人空间不可用")
        model = session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        replay = PersonalTaskService.idempotent_replay(
            session, workspace_id=workspace_id, user_id=user_id, model_id=model_id,
            request_payload=request_payload, idempotency_key=idempotency_key,
        )
        if replay is not None:
            return replay, False
        if model is None or not model.active or model.published_at is None:
            raise NotFoundError("模型不存在或不可用")
        if require_relay_capability_revision and (
            model.relay_capability_revision is None
            or model.relay_capability_approved_ceiling is None
        ):
            raise ConflictError("模型尚未确认中转站能力版本")
        if (
            require_relay_capability_revision
            and not ModelCatalogService.relay_candidate_is_approved(model)
        ):
            raise ConflictError("Relay 模型能力候选待批准，已暂停个人新任务")
        if (
            expected_capability_version is not None
            and expected_capability_version != model.capability_version
        ):
            raise ConflictError("Model capability version changed; refresh and retry")
        grant = session.scalar(
            select(PersonalRetailModelGrant)
            .where(
                PersonalRetailModelGrant.model_id == model_id,
                PersonalRetailModelGrant.enabled.is_(True),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if grant is None:
            raise NotFoundError("该模型尚未开放个人零售使用")
        current_revision = _retail_quote_revision(model=model, grant=grant)
        if expected_quote_revision is not None and expected_quote_revision != current_revision:
            raise ConflictError("Retail model price changed; refresh and retry")
        # The locked retail grant serializes admissions for this model.  Both
        # idempotency replay checks above intentionally precede this mutable
        # quota/concurrency gate.
        PersonalModelService.require_usage_admission(
            session,
            workspace_id=workspace_id,
            model=model,
            grant=grant,
        )
        quote_points, pricing_snapshot, capability_snapshot = (
            PersonalTaskService._quote_and_snapshots(
                session,
                model=model,
                grant=grant,
                request_payload=request_payload,
            )
        )
        commercial_plan = CommercialPricingPolicy.require_price(
            session, model=model, unit_price=pricing_snapshot["unit_price_points"],
            billing_unit="POINT", enabled=True, current_unit_price=None,
            config_override=grant.config_override, scope="personal",
            expected_release_snapshot=expected_commercial_release_snapshot,
        )
        if commercial_plan is not None:
            pricing_snapshot["execution_contract_sha256"] = freeze_execution_contract(
                expected_snapshot=expected_commercial_release_snapshot,
                request_payload=request_payload,
            ).content_sha256()
            pricing_snapshot["commercial_price_authority"] = {
                "plan_id": commercial_plan.id,
                "plan_revision": commercial_plan.revision,
                "plan_content_sha256": commercial_plan.content_sha256,
            }
        task_id = new_id()
        timestamp = utcnow()
        values = {
            "id": task_id,
            "company_id": None,
            "personal_workspace_id": workspace_id,
            "user_id": user_id,
            "model_id": model_id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "status": TaskStatus.DRAFT,
            "request_payload": request_payload,
            "billing_unit": "POINT",
            "billing_version": 2,
            "quote_cents": None,
            "quote_points": quote_points,
            "pricing_snapshot": pricing_snapshot,
            "capability_snapshot": capability_snapshot,
            "reserved_cents": 0,
            "reserved_points": 0,
            "actual_cost_cents": None,
            "actual_cost_points": None,
            "provider_task_id": None,
            "relay_backend_id": relay_backend_id,
            "relay_contract_revision": relay_contract_revision,
            "relay_job_id": None,
            "output_artifacts": [],
            "failure_reason": None,
            "relay_error_snapshot": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert_statement = postgresql_insert(GenerationTask)
        elif dialect_name == "sqlite":
            insert_statement = sqlite_insert(GenerationTask)
        else:
            raise RuntimeError(
                f"personal task idempotency is not implemented for {dialect_name}"
            )
        inserted_task_id = session.scalar(
            insert_statement.values(**values)
            .on_conflict_do_nothing(
                index_elements=["personal_workspace_id", "idempotency_key"]
            )
            .returning(GenerationTask.id)
        )
        if inserted_task_id is not None:
            task = session.get(GenerationTask, inserted_task_id)
            if task is None:
                raise RuntimeError("inserted personal task could not be loaded")
            return task, True
        existing = session.scalar(
            select(GenerationTask).where(
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise RuntimeError("personal task idempotency winner could not be loaded")
        return (
            PersonalTaskService._validate_replay(
                existing,
                user_id=user_id,
                request_fingerprint=request_fingerprint,
            ),
            False,
        )

    @staticmethod
    def response_payloads(
        session: Session, tasks: list[GenerationTask]
    ) -> list[dict[str, Any]]:
        canonical: dict[str, list[dict[str, Any]]] = {}
        task_ids = [task.id for task in tasks]
        if task_ids:
            for artifact in session.scalars(
                select(TaskArtifact)
                .where(TaskArtifact.task_id.in_(task_ids))
                .order_by(TaskArtifact.task_id, TaskArtifact.position)
            ):
                canonical.setdefault(artifact.task_id, []).append(
                    {
                        "artifact_id": artifact.id,
                        "asset_id": artifact.asset_id,
                        "media_type": artifact.media_type,
                        "content_type": artifact.content_type,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                    }
                )
        return [
            {
                "id": task.id,
                "idempotency_key": task.idempotency_key,
                "workspace_id": task.personal_workspace_id,
                "user_id": task.user_id,
                "model_id": task.model_id,
                "status": task.status,
                "request_payload": task.request_payload,
                "billing_unit": "POINT",
                "billing_version": 2,
                "quote_points": task.quote_points,
                "pricing_snapshot": task.pricing_snapshot,
                "capability_snapshot": task.capability_snapshot,
                "reserved_points": task.reserved_points,
                "actual_cost_points": task.actual_cost_points,
                "relay_job_id": task.relay_job_id,
                "output_artifacts": canonical.get(task.id, task.output_artifacts),
                "failure_reason": task.failure_reason,
                "relay_error_snapshot": task.relay_error_snapshot,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
            for task in tasks
        ]

    @staticmethod
    def page(
        session: Session,
        *,
        workspace_id: str,
        user_id: str,
        page: int,
        page_size: int,
        status: TaskStatus | None,
        model_id: str | None,
        media_type: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        filters = [
            GenerationTask.personal_workspace_id == workspace_id,
            GenerationTask.company_id.is_(None),
            GenerationTask.user_id == user_id,
        ]
        if status is not None:
            filters.append(GenerationTask.status == status)
        if model_id is not None:
            filters.append(GenerationTask.model_id == model_id)
        if media_type is not None:
            mode_expression = GenerationTask.request_payload["mode"].as_string()
            filters.append(
                mode_expression.in_(["text_to_image", "image_to_image"])
                if media_type == "image"
                else mode_expression.in_(
                    ["text_to_video", "image_to_video", "video_to_video"]
                )
            )
        total = int(
            session.scalar(select(func.count(GenerationTask.id)).where(*filters)) or 0
        )
        tasks = list(
            session.scalars(
                select(GenerationTask)
                .where(*filters)
                .order_by(GenerationTask.created_at.desc(), GenerationTask.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return total, PersonalTaskService.response_payloads(session, tasks)

    @staticmethod
    def get(
        session: Session, *, workspace_id: str, user_id: str, task_id: str
    ) -> dict[str, Any]:
        task = session.scalar(
            select(GenerationTask).where(
                GenerationTask.id == task_id,
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.company_id.is_(None),
                GenerationTask.user_id == user_id,
            )
        )
        if task is None:
            raise NotFoundError("个人空间下不存在该任务")
        return PersonalTaskService.response_payloads(session, [task])[0]

    @staticmethod
    def artworks_page(
        session: Session,
        *,
        workspace_id: str,
        user_id: str,
        page: int,
        page_size: int,
        model_id: str | None,
        media_type: str | None,
    ) -> tuple[int, list[dict[str, Any]]]:
        filters = [
            TaskArtifact.personal_workspace_id == workspace_id,
            TaskArtifact.company_id.is_(None),
            GenerationTask.personal_workspace_id == workspace_id,
            GenerationTask.user_id == user_id,
            GenerationTask.status == TaskStatus.SUCCEEDED,
            GenerationTask.actual_cost_points.is_not(None),
        ]
        if model_id is not None:
            filters.append(GenerationTask.model_id == model_id)
        if media_type is not None:
            filters.append(TaskArtifact.media_type == media_type)
        download_counts = (
            select(
                PersonalDownloadRecord.task_id.label("download_task_id"),
                PersonalDownloadRecord.asset_id.label("download_asset_id"),
                func.count(PersonalDownloadRecord.id).label(
                    "download_issue_count"
                ),
                func.max(PersonalDownloadRecord.created_at).label(
                    "last_download_issued_at"
                ),
            )
            .where(PersonalDownloadRecord.workspace_id == workspace_id)
            .group_by(
                PersonalDownloadRecord.task_id,
                PersonalDownloadRecord.asset_id,
            )
            .subquery("personal_artwork_download_counts")
        )
        statement = (
            select(
                TaskArtifact,
                GenerationTask,
                ModelDefinition,
                func.coalesce(download_counts.c.download_issue_count, 0),
                download_counts.c.last_download_issued_at,
            )
            .join(GenerationTask, GenerationTask.id == TaskArtifact.task_id)
            .join(ModelDefinition, ModelDefinition.id == GenerationTask.model_id)
            .outerjoin(
                download_counts,
                and_(
                    download_counts.c.download_task_id == TaskArtifact.task_id,
                    download_counts.c.download_asset_id == TaskArtifact.asset_id,
                ),
            )
            .where(*filters)
        )
        total = int(
            session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = list(
            session.execute(
                statement.order_by(TaskArtifact.created_at.desc(), TaskArtifact.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return total, [
            {
                "artifact_id": artifact.id,
                "task_id": task.id,
                "workspace_id": workspace_id,
                "asset_id": artifact.asset_id,
                "output_index": artifact.position,
                "media_type": artifact.media_type,
                "content_type": artifact.content_type,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
                "model_id": model.id,
                "model_display_name": model.display_name,
                "request_payload": task.request_payload,
                "actual_cost_points": task.actual_cost_points,
                "download_evidence_available": True,
                "download_status": (
                    "issued" if int(issue_count) > 0 else "not_downloaded"
                ),
                "download_issue_count": int(issue_count),
                # A signed URL issuance is not proof that bytes reached the
                # user. Personal workspaces do not yet have a trusted OBS or
                # controlled-transfer completion feed, so completion remains
                # explicitly false even after one or more URLs were issued.
                "download_completed_count": 0,
                "downloaded": False,
                "last_download_issued_at": last_issued_at,
                "last_download_completed_at": None,
                "created_at": artifact.created_at,
            }
            for artifact, task, model, issue_count, last_issued_at in rows
        ]
