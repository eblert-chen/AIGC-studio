from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import (
    CompanyMembership,
    CompanyModelGrant,
    CompanyResourceGrant,
    GenerationTask,
    MembershipStatus,
    ModelCapability,
    ModelDefinition,
    ResourceDefinition,
    TaskArtifact,
    TaskStatus,
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
from .quote_revision import model_grant_quote_revision


MAX_MONEY_CENTS = 9_000_000_000_000_000
from .task_admission import TaskCapabilityAdmission
from .artifacts import TaskArtifactService


class TaskService:
    @staticmethod
    def request_fingerprint(*, model_id: str, request_payload: dict) -> str:
        try:
            canonical_request = json.dumps(
                {
                    "model_id": model_id,
                    "request_payload": request_payload,
                },
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ConflictError("任务请求包含无法持久化的参数") from exc
        return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_replay(
        task: GenerationTask,
        *,
        user_id: str,
        request_fingerprint: str,
    ) -> GenerationTask:
        if (
            task.user_id != user_id
            or task.request_fingerprint != request_fingerprint
        ):
            raise ConflictError("幂等键已被当前公司的一笔不同任务请求使用")
        return task

    @staticmethod
    def _positive_int(payload: dict, key: str, default: int | None = None) -> int:
        value = payload.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConflictError(f"{key} 必须是正整数")
        return value

    @staticmethod
    def _require_company_resources(
        session: Session,
        *,
        company_id: str,
        effective_capability: dict,
    ) -> list[dict]:
        required_keys = set(
            effective_capability.get("required_resource_keys", [])
        )
        if not required_keys:
            return []
        now = utcnow()
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
                    ResourceDefinition.key.in_(sorted(required_keys)),
                )
                .order_by(ResourceDefinition.key)
                .with_for_update()
        ).all()
        granted_keys = {resource.key for resource, _ in rows}
        missing_keys = sorted(required_keys - granted_keys)
        if missing_keys:
            raise PermissionDeniedError(
                "Company is missing required generation resource grants: "
                + ", ".join(missing_keys)
            )
        limited_rows = [
            (resource, resource_grant)
            for resource, resource_grant in rows
            if resource_grant.call_quota is not None
            or resource_grant.concurrency_limit is not None
        ]
        if limited_rows:
            # Generation resource quota is per exact company-resource grant
            # and its effective window. Every admitted generation consumes one
            # call even if it later fails or is cancelled; only DRAFT, QUEUED,
            # and PROCESSING consume concurrency. Immutable task snapshots
            # make the counter independent of later capability edits.
            active_statuses = {
                TaskStatus.DRAFT,
                TaskStatus.QUEUED,
                TaskStatus.PROCESSING,
            }
            for resource, resource_grant in limited_rows:
                used_calls = 0
                active_calls = 0
                task_statement = select(
                    GenerationTask.status,
                    GenerationTask.capability_snapshot,
                ).where(GenerationTask.company_id == company_id)
                if resource_grant.effective_at is not None:
                    task_statement = task_statement.where(
                        GenerationTask.created_at >= resource_grant.effective_at
                    )
                if resource_grant.expires_at is not None:
                    task_statement = task_statement.where(
                        GenerationTask.created_at < resource_grant.expires_at
                    )
                for status, capability_snapshot in session.execute(
                    task_statement
                ):
                    snapshots = (capability_snapshot or {}).get(
                        "resource_grants", []
                    )
                    if not isinstance(snapshots, list) or not any(
                        isinstance(snapshot, dict)
                        and snapshot.get("grant_id") == resource_grant.id
                        for snapshot in snapshots
                    ):
                        continue
                    used_calls += 1
                    if status in active_statuses:
                        active_calls += 1
                if (
                    resource_grant.call_quota is not None
                    and used_calls >= resource_grant.call_quota
                ):
                    raise PermissionDeniedError(
                        "Required resource call quota is exhausted: "
                        + resource.key
                    )
                if (
                    resource_grant.concurrency_limit is not None
                    and active_calls >= resource_grant.concurrency_limit
                ):
                    raise PermissionDeniedError(
                        "Required resource concurrency limit is reached: "
                        + resource.key
                    )
        return [
            {
                "key": resource.key,
                "resource_id": resource.id,
                "resource_updated_at": resource.updated_at.isoformat(),
                "grant_id": resource_grant.id,
                "grant_updated_at": resource_grant.updated_at.isoformat(),
                "config_override": resource_grant.config_override,
                "call_quota": resource_grant.call_quota,
                "concurrency_limit": resource_grant.concurrency_limit,
                "effective_at": (
                    resource_grant.effective_at.isoformat()
                    if resource_grant.effective_at
                    else None
                ),
                "expires_at": (
                    resource_grant.expires_at.isoformat()
                    if resource_grant.expires_at
                    else None
                ),
            }
            for resource, resource_grant in rows
        ]

    @classmethod
    def _quote_and_snapshots(
        cls,
        session: Session,
        *,
        grant: CompanyModelGrant,
        model: ModelDefinition,
        request_payload: dict,
    ) -> tuple[int, dict, dict]:
        has_second_price = grant.price_per_second_cents is not None
        has_item_price = grant.price_per_item_cents is not None
        if has_second_price == has_item_price:
            raise ConflictError("模型授权没有且仅有一种有效计费价格")
        configured_mode = "per_second" if has_second_price else "per_item"
        if configured_mode != model.billing_mode:
            raise ConflictError("模型授权价格与模型目录中的计费方式不一致")

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
        effective_capabilities = TaskCapabilityAdmission.effective_capabilities(
            capability_map=capability_map,
            config_override=grant.config_override,
            require_usable=True,
        )
        effective_capability = TaskCapabilityAdmission.validate(
            capability_map=capability_map,
            config_override=grant.config_override,
            request_payload=request_payload,
        )
        selected_mode = request_payload.get("mode", "text_to_video")
        effective_mode = effective_capability["modes"][selected_mode]
        legacy_effective_capability = {
            "modes": [selected_mode],
            "input_media_types": effective_mode["input_media_types"],
            "required_resource_keys": effective_mode[
                "required_resource_keys"
            ],
            "limits": effective_mode["limits"],
        }
        resource_grant_snapshot = TaskService._require_company_resources(
            session,
            company_id=grant.company_id,
            effective_capability=effective_mode,
        )

        if model.billing_mode == "per_second":
            quantity = cls._positive_int(request_payload, "duration_seconds")
            output_count = cls._positive_int(
                request_payload, "output_count", default=1
            )
            if output_count != 1:
                raise ConflictError("按秒计费的单次任务只能生成 1 条结果")
            mode = "per_second"
            unit_price = grant.price_per_second_cents
        else:
            quantity = cls._positive_int(request_payload, "output_count", default=1)
            mode = "per_item"
            unit_price = grant.price_per_item_cents

        if unit_price is None or unit_price <= 0:
            raise ConflictError("模型授权没有有效价格")
        quote_cents = unit_price * quantity
        if quote_cents > MAX_MONEY_CENTS:
            raise ConflictError("任务报价超出系统金额上限")
        pricing_snapshot = {
            "mode": mode,
            "unit_price_cents": unit_price,
            "quantity": quantity,
            "quote_cents": quote_cents,
            "grant_id": grant.id,
            "quote_revision": model_grant_quote_revision(
                model=model,
                grant=grant,
            ),
            "grant_updated_at": grant.updated_at.isoformat(),
            "call_quota": grant.call_quota,
            "concurrency_limit": grant.concurrency_limit,
            "effective_at": (
                grant.effective_at.isoformat() if grant.effective_at else None
            ),
            "expires_at": (
                grant.expires_at.isoformat() if grant.expires_at else None
            ),
        }
        capability_snapshot = {
            "model_id": model.id,
            "model_slug": model.slug,
            "capability_version": model.capability_version,
            "relay_capability_revision": model.relay_capability_revision,
            "capabilities": capability_map,
            "grant_config_override": grant.config_override,
            # Preserve the v1 task snapshot shape for existing readers. The
            # complete versioned contract, including supports_face and every
            # company-allowed mode, lives alongside it.
            "effective": legacy_effective_capability,
            "effective_capabilities": effective_capabilities,
            "resource_grants": resource_grant_snapshot,
        }
        return quote_cents, pricing_snapshot, capability_snapshot

    @staticmethod
    def create(
        session: Session,
        *,
        company_id: str,
        user_id: str,
        model_id: str,
        request_payload: dict,
        idempotency_key: str,
        expected_capability_version: int | None = None,
        expected_quote_revision: str | None = None,
        require_quote_revision: bool = False,
        require_relay_capability_revision: bool = False,
        relay_backend_id: str = NEW_API_RELAY_BACKEND_ID,
        relay_contract_revision: str = DEFAULT_RELAY_CONTRACT_REVISION,
    ) -> tuple[GenerationTask, bool]:
        if not isinstance(request_payload, dict):
            raise ConflictError("request_payload must be an object")
        request_fingerprint = TaskService.request_fingerprint(
            model_id=model_id,
            request_payload=request_payload,
        )
        existing = session.scalar(
            select(GenerationTask).where(
                GenerationTask.company_id == company_id,
                GenerationTask.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return (
                TaskService._validate_replay(
                    existing,
                    user_id=user_id,
                    request_fingerprint=request_fingerprint,
                ),
                False,
            )

        if re.fullmatch(RELAY_BACKEND_ID_PATTERN, relay_backend_id) is None:
            raise ConflictError("Relay backend identity is invalid")
        if (
            re.fullmatch(RELAY_CONTRACT_REVISION_PATTERN, relay_contract_revision)
            is None
        ):
            raise ConflictError("Relay contract revision is invalid")

        # Production callers must prove which server quote the user accepted.
        # Keep this check after the idempotency lookup so a legitimate replay
        # still returns the original immutable task even after prices change.
        if require_quote_revision and expected_quote_revision is None:
            raise ConflictError(
                "A current quote revision is required; refresh the model quote and retry"
            )

        membership = session.scalar(
            select(CompanyMembership).where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.user_id == user_id,
                CompanyMembership.status == MembershipStatus.ACTIVE,
            )
        )
        if membership is None:
            raise PermissionDeniedError("用户不属于当前公司")
        # Keep the pricing lock order aligned with model/grant administration:
        # model first, then the company grant. This prevents an admin billing
        # mode change racing a task snapshot into an inconsistent pair.
        model = session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .with_for_update()
        )
        if model is None or not model.active or model.published_at is None:
            raise NotFoundError("模型不存在或不可用")
        if (
            require_relay_capability_revision
            and model.relay_capability_revision is None
        ):
            raise ConflictError(
                "模型尚未确认中转站能力版本，请联系平台管理员"
            )
        if (
            expected_capability_version is not None
            and model.capability_version != expected_capability_version
        ):
            raise ConflictError(
                "Model capability version changed; refresh the model and retry"
            )
        admission_time = utcnow()
        grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id,
                CompanyModelGrant.model_id == model_id,
                CompanyModelGrant.enabled.is_(True),
                or_(
                    CompanyModelGrant.effective_at.is_(None),
                    CompanyModelGrant.effective_at <= admission_time,
                ),
                or_(
                    CompanyModelGrant.expires_at.is_(None),
                    CompanyModelGrant.expires_at > admission_time,
                ),
            ).with_for_update()
        )
        if grant is None:
            raise NotFoundError("当前公司未获授权使用该模型")
        current_quote_revision = model_grant_quote_revision(
            model=model,
            grant=grant,
        )
        if (
            expected_quote_revision is not None
            and expected_quote_revision != current_quote_revision
        ):
            raise ConflictError(
                "Model pricing or grant changed; refresh the model quote and retry"
            )
        quota_filters = [
            GenerationTask.company_id == company_id,
            GenerationTask.model_id == model_id,
        ]
        if grant.effective_at is not None:
            quota_filters.append(GenerationTask.created_at >= grant.effective_at)
        if grant.expires_at is not None:
            quota_filters.append(GenerationTask.created_at < grant.expires_at)
        if grant.call_quota is not None:
            used_calls = int(
                session.scalar(
                    select(func.count(GenerationTask.id)).where(*quota_filters)
                )
                or 0
            )
            if used_calls >= grant.call_quota:
                raise PermissionDeniedError("Company model call quota is exhausted")
        if grant.concurrency_limit is not None:
            active_calls = int(
                session.scalar(
                    select(func.count(GenerationTask.id)).where(
                        GenerationTask.company_id == company_id,
                        GenerationTask.model_id == model_id,
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
                raise PermissionDeniedError(
                    "Company model concurrency limit is reached"
                )
        quote_cents, pricing_snapshot, capability_snapshot = (
            TaskService._quote_and_snapshots(
                session,
                grant=grant,
                model=model,
                request_payload=request_payload,
            )
        )
        task_id = new_id()
        timestamp = utcnow()
        values = {
            "id": task_id,
            "company_id": company_id,
            "user_id": user_id,
            "model_id": model_id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "status": TaskStatus.DRAFT,
            "request_payload": request_payload,
            "quote_cents": quote_cents,
            "pricing_snapshot": pricing_snapshot,
            "capability_snapshot": capability_snapshot,
            "reserved_cents": 0,
            "actual_cost_cents": None,
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
                f"task idempotency is not implemented for {dialect_name}"
            )
        inserted_task_id = session.scalar(
            insert_statement.values(**values)
            .on_conflict_do_nothing(
                index_elements=["company_id", "idempotency_key"]
            )
            .returning(GenerationTask.id)
        )
        if inserted_task_id is not None:
            task = session.get(GenerationTask, inserted_task_id)
            if task is None:
                raise RuntimeError("inserted task could not be loaded")
            return task, True

        existing = session.scalar(
            select(GenerationTask).where(
                GenerationTask.company_id == company_id,
                GenerationTask.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise RuntimeError("idempotency conflict winner could not be loaded")
        return (
            TaskService._validate_replay(
                existing,
                user_id=user_id,
                request_fingerprint=request_fingerprint,
            ),
            False,
        )

    @staticmethod
    def list_company_tasks(
        session: Session,
        *,
        company_id: str,
        visible_user_id: str | None = None,
        status: TaskStatus | None = None,
        model_id: str | None = None,
        limit: int = 200,
    ) -> list[GenerationTask]:
        statement = select(GenerationTask).where(
            GenerationTask.company_id == company_id
        )
        if visible_user_id is not None:
            statement = statement.where(
                GenerationTask.user_id == visible_user_id
            )
        if status is not None:
            statement = statement.where(GenerationTask.status == status)
        if model_id is not None:
            statement = statement.where(GenerationTask.model_id == model_id)
        return list(
            session.scalars(
                statement
                .order_by(GenerationTask.created_at.desc())
                .limit(limit)
            ).all()
        )

    @staticmethod
    def get_company_task(
        session: Session,
        *,
        company_id: str,
        task_id: str,
        visible_user_id: str | None = None,
    ) -> GenerationTask:
        statement = select(GenerationTask).where(
            GenerationTask.id == task_id,
            GenerationTask.company_id == company_id,
        )
        if visible_user_id is not None:
            statement = statement.where(
                GenerationTask.user_id == visible_user_id
            )
        task = session.scalar(statement)
        if task is None:
            raise NotFoundError("当前公司下不存在该任务")
        return task

    @staticmethod
    def _base_response_payload(task: GenerationTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "company_id": task.company_id,
            "user_id": task.user_id,
            "model_id": task.model_id,
            "status": task.status,
            "request_payload": task.request_payload,
            "quote_cents": task.quote_cents,
            "pricing_snapshot": task.pricing_snapshot,
            "capability_snapshot": task.capability_snapshot,
            "reserved_cents": task.reserved_cents,
            "actual_cost_cents": task.actual_cost_cents,
            "relay_job_id": task.relay_job_id,
            "output_artifacts": task.output_artifacts,
            "failure_reason": task.failure_reason,
            "relay_error_snapshot": task.relay_error_snapshot,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    @classmethod
    def response_payloads(
        cls,
        session: Session,
        tasks: list[GenerationTask],
    ) -> list[dict[str, Any]]:
        """Serialize tasks with canonical TaskArtifact ids when they exist.

        The legacy JSON snapshot remains the fallback for historical and
        non-terminal tasks, so adding ``artifact_id`` stays response-compatible.
        """
        canonical_by_task: dict[str, list[dict[str, Any]]] = {}
        task_ids = [task.id for task in tasks]
        if task_ids:
            rows = session.scalars(
                select(TaskArtifact)
                .where(TaskArtifact.task_id.in_(task_ids))
                .order_by(TaskArtifact.task_id, TaskArtifact.position)
            )
            for row in rows:
                canonical_by_task.setdefault(row.task_id, []).append(
                    {
                        "artifact_id": row.id,
                        **TaskArtifactService._snapshot(row),
                    }
                )
        payloads = []
        for task in tasks:
            payload = cls._base_response_payload(task)
            if task.id in canonical_by_task:
                payload["output_artifacts"] = canonical_by_task[task.id]
            payloads.append(payload)
        return payloads

    @classmethod
    def response_payload(
        cls,
        session: Session,
        task: GenerationTask,
    ) -> dict[str, Any]:
        return cls.response_payloads(session, [task])[0]
