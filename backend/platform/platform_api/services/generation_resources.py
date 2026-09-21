from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    CompanyResourceGrant,
    CompanyModelGrant,
    GenerationTask,
    ResourceDefinition,
    TaskStatus,
    utcnow,
)
from .errors import PermissionDeniedError


@dataclass(frozen=True)
class ResourceEvaluation:
    required_keys: tuple[str, ...]
    blockers: tuple[dict[str, Any], ...]
    snapshots: tuple[dict[str, Any], ...]

    @property
    def ready(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class ModelGrantEvaluation:
    blockers: tuple[dict[str, Any], ...]

    @property
    def ready(self) -> bool:
        return not self.blockers


class GenerationModelGrantAdmission:
    """Shared company-model quota evaluation for discovery and task admission."""

    @classmethod
    def evaluate(
        cls,
        session: Session,
        *,
        company_id: str,
        model_id: str,
        model_name: str,
        grant: CompanyModelGrant,
    ) -> ModelGrantEvaluation:
        blockers: list[dict[str, Any]] = []
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
                blockers.append(
                    {
                        "code": "model_call_quota_exhausted",
                        "message": f"{model_name}的调用额度已用完。",
                        "resource_key": f"model:{model_id}",
                        "resource_name": model_name,
                        "retryable": False,
                    }
                )
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
                blockers.append(
                    {
                        "code": "model_concurrency_saturated",
                        "message": f"{model_name}当前任务已达并发上限，请稍后重试。",
                        "resource_key": f"model:{model_id}",
                        "resource_name": model_name,
                        "retryable": True,
                    }
                )
        return ModelGrantEvaluation(tuple(blockers))

    @classmethod
    def require(
        cls,
        session: Session,
        *,
        company_id: str,
        model_id: str,
        model_name: str,
        grant: CompanyModelGrant,
    ) -> None:
        evaluation = cls.evaluate(
            session,
            company_id=company_id,
            model_id=model_id,
            model_name=model_name,
            grant=grant,
        )
        if not evaluation.blockers:
            return
        if evaluation.blockers[0]["code"] == "model_call_quota_exhausted":
            raise PermissionDeniedError("Company model call quota is exhausted")
        raise PermissionDeniedError("Company model concurrency limit is reached")


class GenerationResourceAdmission:
    """One resource-policy evaluator shared by discovery and admission.

    Discovery is advisory and does not lock grants. Task admission calls the
    same evaluator with ``lock=True`` and remains the final authority.
    """

    _ACTIVE_TASK_STATUSES = frozenset(
        {TaskStatus.DRAFT, TaskStatus.QUEUED, TaskStatus.PROCESSING}
    )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _blocker(
        *,
        code: str,
        resource_key: str,
        resource_name: str,
        message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "resource_key": resource_key,
            "resource_name": resource_name,
            "retryable": retryable,
        }

    @classmethod
    def _usage(
        cls,
        session: Session,
        *,
        company_id: str,
        grant: CompanyResourceGrant,
    ) -> tuple[int, int]:
        statement = select(
            GenerationTask.status,
            GenerationTask.capability_snapshot,
        ).where(GenerationTask.company_id == company_id)
        if grant.effective_at is not None:
            statement = statement.where(
                GenerationTask.created_at >= grant.effective_at
            )
        if grant.expires_at is not None:
            statement = statement.where(
                GenerationTask.created_at < grant.expires_at
            )
        used_calls = 0
        active_calls = 0
        for status, capability_snapshot in session.execute(statement):
            snapshots = (capability_snapshot or {}).get("resource_grants", [])
            if not isinstance(snapshots, list) or not any(
                isinstance(snapshot, dict)
                and snapshot.get("grant_id") == grant.id
                for snapshot in snapshots
            ):
                continue
            used_calls += 1
            if status in cls._ACTIVE_TASK_STATUSES:
                active_calls += 1
        return used_calls, active_calls

    @classmethod
    def evaluate_company(
        cls,
        session: Session,
        *,
        company_id: str,
        required_keys: list[str] | set[str] | tuple[str, ...],
        lock: bool,
        checked_at: datetime | None = None,
    ) -> ResourceEvaluation:
        normalized_keys = tuple(sorted(set(required_keys)))
        if not normalized_keys:
            return ResourceEvaluation((), (), ())
        now = cls._as_utc(checked_at or utcnow())
        assert now is not None
        definitions = {
            resource.key: resource
            for resource in session.scalars(
                select(ResourceDefinition).where(
                    ResourceDefinition.key.in_(normalized_keys)
                )
            ).all()
        }
        definition_ids = [resource.id for resource in definitions.values()]
        grant_statement = select(CompanyResourceGrant).where(
            CompanyResourceGrant.company_id == company_id,
            CompanyResourceGrant.resource_id.in_(definition_ids),
        )
        if lock:
            grant_statement = grant_statement.with_for_update()
        grants = {
            grant.resource_id: grant
            for grant in session.scalars(grant_statement).all()
        }

        blockers: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        for key in normalized_keys:
            resource = definitions.get(key)
            if resource is None:
                blockers.append(
                    cls._blocker(
                        code="resource_not_defined",
                        resource_key=key,
                        resource_name="所需能力",
                        message="平台尚未配置该生成能力，请联系管理员。",
                        retryable=False,
                    )
                )
                continue
            name = resource.display_name
            if not resource.active:
                blockers.append(
                    cls._blocker(
                        code="resource_inactive",
                        resource_key=key,
                        resource_name=name,
                        message=f"{name}已停用，请联系管理员。",
                        retryable=False,
                    )
                )
                continue
            grant = grants.get(resource.id)
            if grant is None or not grant.enabled:
                blockers.append(
                    cls._blocker(
                        code="resource_not_granted",
                        resource_key=key,
                        resource_name=name,
                        message=f"当前工作区尚未开通{name}。",
                        retryable=False,
                    )
                )
                continue
            effective_at = cls._as_utc(grant.effective_at)
            expires_at = cls._as_utc(grant.expires_at)
            if effective_at is not None and effective_at > now:
                blockers.append(
                    cls._blocker(
                        code="resource_not_yet_effective",
                        resource_key=key,
                        resource_name=name,
                        message=f"{name}尚未生效。",
                        retryable=True,
                    )
                )
                continue
            if expires_at is not None and expires_at <= now:
                blockers.append(
                    cls._blocker(
                        code="resource_grant_expired",
                        resource_key=key,
                        resource_name=name,
                        message=f"{name}授权已到期。",
                        retryable=False,
                    )
                )
                continue

            if grant.call_quota is not None or grant.concurrency_limit is not None:
                used_calls, active_calls = cls._usage(
                    session,
                    company_id=company_id,
                    grant=grant,
                )
                if grant.call_quota is not None and used_calls >= grant.call_quota:
                    blockers.append(
                        cls._blocker(
                            code="resource_call_quota_exhausted",
                            resource_key=key,
                            resource_name=name,
                            message=f"{name}调用额度已用完。",
                            retryable=False,
                        )
                    )
                    continue
                if (
                    grant.concurrency_limit is not None
                    and active_calls >= grant.concurrency_limit
                ):
                    blockers.append(
                        cls._blocker(
                            code="resource_concurrency_saturated",
                            resource_key=key,
                            resource_name=name,
                            message=f"{name}当前任务已达并发上限，请稍后重试。",
                            retryable=True,
                        )
                    )
                    continue

            snapshots.append(
                {
                    "key": resource.key,
                    "resource_id": resource.id,
                    "resource_updated_at": resource.updated_at.isoformat(),
                    "grant_id": grant.id,
                    "grant_updated_at": grant.updated_at.isoformat(),
                    "config_override": grant.config_override,
                    "call_quota": grant.call_quota,
                    "concurrency_limit": grant.concurrency_limit,
                    "effective_at": (
                        grant.effective_at.isoformat()
                        if grant.effective_at
                        else None
                    ),
                    "expires_at": (
                        grant.expires_at.isoformat() if grant.expires_at else None
                    ),
                }
            )
        return ResourceEvaluation(
            required_keys=normalized_keys,
            blockers=tuple(blockers),
            snapshots=tuple(snapshots),
        )

    @classmethod
    def require_company(
        cls,
        session: Session,
        *,
        company_id: str,
        required_keys: list[str] | set[str] | tuple[str, ...],
    ) -> list[dict[str, Any]]:
        evaluation = cls.evaluate_company(
            session,
            company_id=company_id,
            required_keys=required_keys,
            lock=True,
        )
        if evaluation.blockers:
            blocker = evaluation.blockers[0]
            code = blocker["code"]
            key = blocker["resource_key"]
            if code == "resource_call_quota_exhausted":
                raise PermissionDeniedError(
                    "Required resource call quota is exhausted: " + key
                )
            if code == "resource_concurrency_saturated":
                raise PermissionDeniedError(
                    "Required resource concurrency limit is reached: " + key
                )
            raise PermissionDeniedError(
                "Company is missing required generation resource grants: "
                + ", ".join(
                    item["resource_key"] for item in evaluation.blockers
                )
            )
        return list(evaluation.snapshots)

    @classmethod
    def mode_readiness(
        cls,
        session: Session,
        *,
        company_id: str,
        effective_capabilities: dict[str, Any],
        checked_at: datetime,
        base_blockers: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, dict[str, Any]]:
        cache: dict[tuple[str, ...], ResourceEvaluation] = {}

        def evaluate(keys: set[str]) -> ResourceEvaluation:
            cache_key = tuple(sorted(keys))
            if cache_key not in cache:
                cache[cache_key] = cls.evaluate_company(
                    session,
                    company_id=company_id,
                    required_keys=cache_key,
                    lock=False,
                    checked_at=checked_at,
                )
            return cache[cache_key]

        result: dict[str, dict[str, Any]] = {}
        for mode, capability in effective_capabilities.get("modes", {}).items():
            base_keys = set(capability.get("required_resource_keys", []))
            default_evaluation = evaluate(base_keys)
            default_blockers = [
                *base_blockers,
                *default_evaluation.blockers,
            ]
            supports_face = capability.get("supports_face") is True
            face_keys = set(base_keys)
            face_keys.update(
                capability.get("conditional_required_resource_keys", {}).get(
                    "face_enabled", []
                )
            )
            face_evaluation = evaluate(face_keys) if supports_face else None
            face_blockers = (
                [*base_blockers, *face_evaluation.blockers]
                if face_evaluation is not None
                else []
            )
            result[mode] = {
                "default": {
                    "ready": not default_blockers,
                    "status": (
                        "ready" if not default_blockers else "blocked"
                    ),
                    "blockers": default_blockers,
                },
                "options": {
                    "face_enabled": {
                        "supported": supports_face,
                        "ready": bool(
                            face_evaluation is not None and not face_blockers
                        ),
                        "status": (
                            "unsupported"
                            if not supports_face
                            else (
                                "ready" if not face_blockers else "blocked"
                            )
                        ),
                        "blockers": (
                            face_blockers
                            if face_evaluation is not None
                            else []
                        ),
                    }
                },
            }
        return result
