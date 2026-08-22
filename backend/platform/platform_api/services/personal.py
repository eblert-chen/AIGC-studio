from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import (
    Company,
    CompanyMembership,
    GenerationTask,
    MembershipStatus,
    ModelCapability,
    ModelDefinition,
    PersonalDownloadRecord,
    PersonalRetailModelGrant,
    PersonalWalletAccount,
    PersonalWorkspace,
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
from .task_admission import TaskCapabilityAdmission
from .tasks import MAX_MONEY_CENTS, TaskService


PERSONAL_GENERATION_MODES = frozenset({"text_to_video", "text_to_image"})


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
        "grant_updated_at": grant.updated_at.isoformat(),
        "enabled": grant.enabled,
        "price_per_second_points": grant.price_per_second_points,
        "price_per_item_points": grant.price_per_item_points,
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
        user = session.get(User, user_id)
        if user is None:
            raise NotFoundError("用户不存在")
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
    def surfaces(session: Session, *, user_id: str) -> dict[str, Any]:
        workspace = PersonalWorkspaceService.ensure(session, user_id=user_id)
        user = session.get(User, user_id)
        assert user is not None
        companies = list(
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
        return {
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
            },
            "personal": {
                "kind": "personal",
                "workspace_id": workspace.id,
                "label": "个人空间",
                "capabilities": {
                    "generation": True,
                    "models": True,
                    "tasks": True,
                    "artworks": True,
                    "task_cancel": False,
                    "assets": False,
                    "artifact_access": True,
                    "publishing": False,
                },
            },
            "companies": [
                {
                    "kind": "company",
                    "company_id": company["id"],
                    "name": company["name"],
                    "status": company["status"].value,
                }
                for company in companies
            ],
            "platform_admin": bool(user.is_platform_admin),
        }


class PersonalModelService:
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
            and not config.get("input_media_types")
            and not config.get("required_resource_keys")
        }
        effective = {**effective, "modes": supported_modes}
        if require_usable and not supported_modes:
            raise ConflictError("零售模型没有可供个人空间使用的纯文本生成模式")
        return capability_map, effective

    @staticmethod
    def list_available(session: Session) -> list[dict[str, Any]]:
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
            result.append(
                {
                    "id": model.id,
                    "slug": model.slug,
                    "display_name": model.display_name,
                    "billing_mode": model.billing_mode,
                    "unit_price_points": unit_price,
                    "capability_version": model.capability_version,
                    "quote_revision": _retail_quote_revision(model=model, grant=grant),
                    "effective_capabilities": effective,
                }
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
    def _quote_and_snapshots(
        session: Session,
        *,
        model: ModelDefinition,
        grant: PersonalRetailModelGrant,
        request_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        mode = request_payload.get("mode", "text_to_video")
        if mode not in PERSONAL_GENERATION_MODES:
            raise ConflictError("个人空间首期仅支持纯文本生成图片或视频")
        assets = request_payload.get("assets", [])
        if assets not in (None, []):
            raise ConflictError("个人空间暂不支持上传或引用素材")
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
        if selected.get("input_media_types") or selected.get("required_resource_keys"):
            raise PermissionDeniedError("个人空间不具备该模式所需的素材或资源授权")

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
            "scope": "personal_retail",
            "mode": model.billing_mode,
            "unit_price_points": unit_price,
            "quantity": quantity,
            "quote_points": quote_points,
            "grant_id": grant.id,
            "quote_revision": quote_revision,
            "grant_updated_at": grant.updated_at.isoformat(),
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
            .with_for_update()
        )
        if workspace is None:
            raise PermissionDeniedError("个人空间不可用")
        model = session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .with_for_update()
        )
        if model is None or not model.active or model.published_at is None:
            raise NotFoundError("模型不存在或不可用")
        if require_relay_capability_revision and model.relay_capability_revision is None:
            raise ConflictError("模型尚未确认中转站能力版本")
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
        )
        if grant is None:
            raise NotFoundError("该模型尚未开放个人零售使用")
        current_revision = _retail_quote_revision(model=model, grant=grant)
        if expected_quote_revision is not None and expected_quote_revision != current_revision:
            raise ConflictError("Retail model price changed; refresh and retry")
        quote_points, pricing_snapshot, capability_snapshot = (
            PersonalTaskService._quote_and_snapshots(
                session,
                model=model,
                grant=grant,
                request_payload=request_payload,
            )
        )
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
                "workspace_id": task.personal_workspace_id,
                "user_id": task.user_id,
                "model_id": task.model_id,
                "status": task.status,
                "request_payload": task.request_payload,
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
                mode_expression == "text_to_image"
                if media_type == "image"
                else mode_expression == "text_to_video"
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
