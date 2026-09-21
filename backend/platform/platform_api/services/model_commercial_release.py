from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import enum
import hashlib
import hmac
import json
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    Company,
    CompanyMembership,
    CompanyModelGrant,
    CompanyPointLot,
    CompanyStatus,
    MembershipStatus,
    ModelCommercialReleaseBatch,
    ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan,
    ModelDefinition,
    PersonalPointLot,
    PersonalRetailModelGrant,
    PersonalWorkspace,
    PointLotSourceKind,
    User,
    UserAccountType,
    UserStatus,
    new_id,
    utcnow,
)
from ..relay_client import (
    RelayModelCatalog,
    RelayModelReleaseEvidence,
    RelayModelReleaseEvidenceItem,
    validate_model_catalog_release_evidence_pair,
)
from .audit import AuditService
from .company_points_billing import (
    POINT_VALUE_CENTS,
    CompanyPointBillingService,
)
from .errors import ConflictError, NotFoundError
from .commercial_pricing import (
    CommercialPricingPolicy, MICROS_PER_CURRENCY_UNIT, POINTS_PER_CNY,
    TARGET_MARGIN_BPS, minimum_price_points,
)
from .model_release_guard import build_model_release_readiness
from .models import ModelCatalogService, ModelGrantService
from .personal import PersonalRetailGrantService
from .personal_billing import PersonalWalletService
from .relay_capabilities import RelayCapabilityService
from .task_admission import TaskCapabilityAdmission
from .provider_route_identity import (
    canonical_sha256,
    commercial_route_publication_evidence,
    commercial_route_release_snapshot,
)


ENTERPRISE_DISTRIBUTION_SCOPE = "all_active_point_companies_at_release"
_MAX_PRICE_POINTS = 10**12
_SYSTEM_ACTOR = "relay-catalog-sync"
LOCAL_VIDEO_LAB_MARKER_ACTION = "local_video_lab.initialize"
LOCAL_VIDEO_LAB_ACTIVATION_ACTION = "local_video_lab.commercial_activation"

# Batch entry points take a *reader* rather than a resolved catalog so a
# malformed or unknown request can be rejected without a Relay round trip.
# Reading Relay eagerly would report an outage (503) where the caller actually
# made a client error (404/409), hiding a fixable mistake behind infrastructure.
RelaySnapshotReader = Callable[
    [], tuple[RelayModelCatalog, RelayModelReleaseEvidence]
]



def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: (
            value.astimezone(timezone.utc).isoformat()
            if isinstance(value, datetime)
            else str(value)
        ),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _audit_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _audit_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_audit_safe(item) for item in value]
    return value


@dataclass(frozen=True)
class CommercialReleaseReconcileResult:
    catalog_revision: str
    planned_count: int
    released_count: int
    blocked_count: int
    unchanged_count: int
    items: tuple[dict[str, Any], ...]


class ModelCommercialReleaseService:
    """Consume owner-approved prices only after exact live Relay acceptance.

    The service deliberately separates two authorities:

    * a Platform owner approves an immutable cost/FX/price/distribution plan;
    * Relay proves that the exact capability and routing release can run.

    Neither authority can substitute for the other.  Reconciliation is merely
    the idempotent transaction which joins both pieces of evidence.
    """

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ConflictError("成本与汇率证据时间必须包含 UTC 偏移")
        return value.astimezone(timezone.utc)

    @staticmethod
    def lock_distribution_companies(session: Session) -> tuple[Company, ...]:
        """Acquire company locks before any model or commercial-plan locks.

        The HTTP reconcile path must call this before catalog materialization,
        which can itself update model rows in the same transaction.
        """
        return tuple(session.scalars(
            select(Company)
            .where(Company.status == CompanyStatus.ACTIVE, Company.billing_version == 2)
            .order_by(Company.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all())

    @classmethod
    def _response(
        cls,
        *,
        plan: ModelCommercialReleasePlan,
        execution: ModelCommercialReleaseExecution,
        model: ModelDefinition,
    ) -> dict[str, Any]:
        return {
            "id": plan.id,
            "revision": plan.revision,
            "supersedes_plan_id": plan.supersedes_plan_id,
            "batch_id": plan.batch_id,
            "model_id": plan.model_id,
            "model_slug": model.slug,
            "candidate_revision": plan.candidate_revision,
            "candidate_catalog_revision": plan.candidate_catalog_revision,
            "capability_version": plan.capability_version,
            "billing_mode": plan.billing_mode,
            "provider_cost_currency": plan.provider_cost_currency,
            "provider_cost_formula": plan.provider_cost_formula,
            "provider_cost_micros": plan.provider_cost_micros,
            "provider_cost_cny_micros": plan.provider_cost_cny_micros,
            "provider_cost_evidence_kind": plan.provider_cost_evidence_kind,
            "provider_cost_evidence_reference": (
                plan.provider_cost_evidence_reference
            ),
            "provider_cost_evidence_sha256": plan.provider_cost_evidence_sha256,
            "provider_cost_effective_at": plan.provider_cost_effective_at,
            "fx_cny_micros_per_currency_unit": (
                plan.fx_cny_micros_per_currency_unit
            ),
            "fx_source": plan.fx_source,
            "fx_version": plan.fx_version,
            "fx_evidence_sha256": plan.fx_evidence_sha256,
            "fx_effective_at": plan.fx_effective_at,
            "points_per_cny": plan.points_per_cny,
            "target_margin_bps": plan.target_margin_bps,
            "minimum_price_points": plan.minimum_price_points,
            "personal_price_points": plan.personal_price_points,
            "enterprise_price_points": plan.enterprise_price_points,
            "enterprise_distribution_scope": plan.enterprise_distribution_scope,
            "personal_config_override": plan.personal_config_override,
            "enterprise_config_override": plan.enterprise_config_override,
            "approval_reason": plan.approval_reason,
            "approved_by_user_id": plan.approved_by_user_id,
            "approved_at": plan.approved_at,
            "idempotency_key": plan.idempotency_key,
            "content_sha256": plan.content_sha256,
            "approved_route_identity": plan.approved_route_identity,
            "approved_route_identity_sha256": (
                plan.approved_route_identity_sha256
            ),
            "state": execution.state,
            "attempt_count": execution.attempt_count,
            "last_blocker_code": execution.last_blocker_code,
            "last_blocker_message": execution.last_blocker_message,
            "route_release_evidence": execution.route_release_evidence,
            "released_route_identity_sha256": (
                execution.released_route_identity_sha256
            ),
            "publication_receipt": execution.publication_receipt,
            "publication_receipt_sha256": execution.publication_receipt_sha256,
            "personal_grant_id": execution.personal_grant_id,
            "company_grant_count": execution.company_grant_count,
            "company_ids": execution.company_ids,
            "released_at": execution.released_at,
            "created_at": plan.created_at,
            "updated_at": execution.updated_at,
        }

    @classmethod
    def list_plans(cls, session: Session) -> list[dict[str, Any]]:
        rows = session.execute(
            select(
                ModelCommercialReleasePlan,
                ModelCommercialReleaseExecution,
                ModelDefinition,
            )
            .join(
                ModelCommercialReleaseExecution,
                ModelCommercialReleaseExecution.plan_id
                == ModelCommercialReleasePlan.id,
            )
            .join(ModelDefinition, ModelDefinition.id == ModelCommercialReleasePlan.model_id)
            .order_by(
                ModelCommercialReleasePlan.created_at,
                ModelCommercialReleasePlan.id,
            )
        ).all()
        return [
            cls._response(plan=plan, execution=execution, model=model)
            for plan, execution, model in rows
        ]

    @staticmethod
    def _local_video_lab_marker(
        session: Session,
        *,
        lab_id: str | None = None,
        manifest_sha256: str | None = None,
        lock: bool = False,
    ) -> AuditLog | None:
        statement = select(AuditLog).where(
            AuditLog.action == LOCAL_VIDEO_LAB_MARKER_ACTION,
            AuditLog.target_type == "local_video_lab",
        )
        if lab_id is not None:
            statement = statement.where(AuditLog.target_id == lab_id)
        if lock:
            statement = statement.with_for_update()
        rows = tuple(session.scalars(statement).all())
        if not rows:
            return None
        if len(rows) != 1:
            raise ConflictError("本地视频联调数据库标记不唯一")
        marker = rows[0]
        binding = marker.after_summary
        if (
            not isinstance(binding, dict)
            or binding.get("kind") != "ai-video-local-video-lab"
            or binding.get("lab_id") != marker.target_id
            or binding.get("test_data_only") is not True
            or binding.get("cash_basis_cents") != 0
            or (
                manifest_sha256 is not None
                and binding.get("manifest_sha256") != manifest_sha256
            )
        ):
            raise ConflictError("本地视频联调数据库标记无效")
        return marker

    @classmethod
    def has_unreleased_plans(cls, session: Session) -> bool:
        # A local-video-lab database is activated only through the exact-set
        # transaction below.  In particular, the periodic worker must never
        # consume one newly-approved plan between the lab's approval requests.
        if cls._local_video_lab_marker(session) is not None:
            return False
        return session.scalar(
            select(ModelCommercialReleaseExecution.id)
            .where(
                ModelCommercialReleaseExecution.state.in_(
                    ("approved", "blocked", "released")
                )
            )
            .limit(1)
        ) is not None

    @staticmethod
    def local_lab_activation_intent(
        *,
        lab_id: str,
        manifest_sha256: str,
        provider_mode: str,
        company_id: str,
        company_user_id: str,
        personal_user_id: str,
        personal_workspace_id: str,
        test_points: int,
        unit_price_points_per_second: int,
        call_quota: int,
        concurrency_limit: int,
        plans: tuple[Mapping[str, str], ...],
    ) -> dict[str, Any]:
        scalar_values = (
            lab_id,
            manifest_sha256,
            company_id,
            company_user_id,
            personal_user_id,
            personal_workspace_id,
        )
        if any(not isinstance(value, str) or not value for value in scalar_values):
            raise ConflictError("本地视频联调激活作用域无效")
        if provider_mode not in {"mock", "live"}:
            raise ConflictError("本地视频联调供应商模式无效")
        if not manifest_sha256.startswith("sha256:") or len(manifest_sha256) != 71:
            raise ConflictError("本地视频联调 manifest 摘要无效")
        bounded = (
            test_points,
            unit_price_points_per_second,
            call_quota,
            concurrency_limit,
        )
        if any(type(value) is not int or value <= 0 for value in bounded):
            raise ConflictError("本地视频联调预算无效")
        normalized_plans: list[dict[str, str]] = []
        for raw in plans:
            if set(raw) != {
                "plan_id",
                "model_id",
                "model_slug",
                "plan_content_sha256",
            }:
                raise ConflictError("本地视频联调商业计划意图字段无效")
            item = {key: str(raw[key]) for key in raw}
            if (
                any(not value for value in item.values())
                or len(item["plan_content_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in item["plan_content_sha256"]
                )
            ):
                raise ConflictError("本地视频联调商业计划意图无效")
            normalized_plans.append(item)
        if not normalized_plans:
            raise ConflictError("本地视频联调商业计划集合不能为空")
        for field in ("plan_id", "model_id", "model_slug"):
            values = [item[field] for item in normalized_plans]
            if len(values) != len(set(values)):
                raise ConflictError("本地视频联调商业计划集合必须精确且无重复")
        normalized_plans.sort(key=lambda item: (item["model_slug"], item["plan_id"]))
        return {
            "schema_version": 1,
            "kind": "local_video_lab_exact_commercial_activation",
            "lab_id": lab_id,
            "manifest_sha256": manifest_sha256,
            "provider_mode": provider_mode,
            "company_scope": {
                "user_id": company_user_id,
                "company_id": company_id,
                "personal_workspace_id": None,
            },
            "personal_scope": {
                "user_id": personal_user_id,
                "company_id": None,
                "personal_workspace_id": personal_workspace_id,
            },
            "budget": {
                "test_points": test_points,
                "unit_price_points_per_second": unit_price_points_per_second,
                "call_quota": call_quota,
                "concurrency_limit": concurrency_limit,
            },
            "plans": normalized_plans,
        }

    @classmethod
    def _local_lab_activation_receipt(
        cls,
        session: Session,
        *,
        intent: Mapping[str, Any],
        lock_marker: bool,
    ) -> dict[str, Any] | None:
        lab_id = str(intent["lab_id"])
        marker = cls._local_video_lab_marker(
            session,
            lab_id=lab_id,
            manifest_sha256=str(intent["manifest_sha256"]),
            lock=lock_marker,
        )
        if marker is None:
            raise ConflictError("当前数据库不是已绑定的本地视频联调隔离库")
        rows = tuple(session.scalars(
            select(AuditLog).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION,
                AuditLog.target_type == "local_video_lab_activation",
                AuditLog.target_id == lab_id,
            )
        ).all())
        if not rows:
            return None
        if len(rows) != 1:
            raise ConflictError("本地视频联调激活回执不唯一")
        row = rows[0]
        stored = row.after_summary
        expected_intent_sha256 = canonical_sha256(intent)
        receipt = stored.get("receipt") if isinstance(stored, dict) else None
        if (
            not isinstance(stored, dict)
            or stored.get("intent") != intent
            or not isinstance(stored.get("intent_sha256"), str)
            or not hmac.compare_digest(
                stored["intent_sha256"], expected_intent_sha256
            )
        ):
            raise ConflictError("本地视频联调激活幂等键已绑定不同意图")
        if (
            not isinstance(receipt, dict)
            or not isinstance(stored.get("receipt_sha256"), str)
            or not hmac.compare_digest(
                stored["receipt_sha256"], canonical_sha256(receipt)
            )
        ):
            raise ConflictError("本地视频联调激活回执完整性校验失败")
        return {
            "activation_audit_id": row.id,
            "intent_sha256": stored["intent_sha256"],
            "receipt_sha256": stored["receipt_sha256"],
            "receipt": receipt,
        }

    @classmethod
    def read_local_lab_activation_receipt(
        cls,
        session: Session,
        *,
        intent: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return cls._local_lab_activation_receipt(
            session,
            intent=intent,
            lock_marker=False,
        )

    @classmethod
    def read_local_lab_activation_receipt_for_lab(
        cls,
        session: Session,
        *,
        lab_id: str,
        manifest_sha256: str,
    ) -> dict[str, Any] | None:
        marker = cls._local_video_lab_marker(
            session,
            lab_id=lab_id,
            manifest_sha256=manifest_sha256,
        )
        if marker is None:
            raise ConflictError("当前数据库不是已绑定的本地视频联调隔离库")
        rows = tuple(session.scalars(
            select(AuditLog).where(
                AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION,
                AuditLog.target_type == "local_video_lab_activation",
                AuditLog.target_id == lab_id,
            )
        ).all())
        if not rows:
            return None
        if len(rows) != 1:
            raise ConflictError("本地视频联调激活回执不唯一")
        row = rows[0]
        stored = row.after_summary
        intent = stored.get("intent") if isinstance(stored, dict) else None
        receipt = stored.get("receipt") if isinstance(stored, dict) else None
        if (
            not isinstance(intent, dict)
            or not isinstance(receipt, dict)
            or intent.get("lab_id") != lab_id
            or intent.get("manifest_sha256") != manifest_sha256
            or receipt.get("lab_id") != lab_id
            or receipt.get("manifest_sha256") != manifest_sha256
            or not isinstance(stored.get("intent_sha256"), str)
            or not isinstance(stored.get("receipt_sha256"), str)
            or not hmac.compare_digest(
                stored["intent_sha256"], canonical_sha256(intent)
            )
            or not hmac.compare_digest(
                stored["receipt_sha256"], canonical_sha256(receipt)
            )
        ):
            raise ConflictError("本地视频联调激活回执完整性校验失败")
        return {
            "activation_audit_id": row.id,
            "intent_sha256": stored["intent_sha256"],
            "receipt_sha256": stored["receipt_sha256"],
            "receipt": receipt,
        }

    _validate_provider_cost_formula = staticmethod(CommercialPricingPolicy.validate_provider_cost_formula)

    @classmethod
    def approve_plan(
        cls,
        session: Session,
        *,
        model_id: str,
        expected_capability_version: int,
        expected_candidate_revision: str,
        expected_catalog_revision: str,
        expected_routing_release_sha256: str,
        provider_cost_currency: str,
        provider_cost_formula: dict[str, Any],
        provider_cost_evidence_kind: str,
        provider_cost_evidence_reference: str,
        provider_cost_evidence_sha256: str,
        provider_cost_effective_at: datetime,
        fx_cny_micros_per_currency_unit: int,
        fx_source: str,
        fx_version: str,
        fx_evidence_sha256: str,
        fx_effective_at: datetime,
        personal_price_points: int | None,
        enterprise_price_points: int | None,
        personal_config_override: dict[str, Any],
        enterprise_config_override: dict[str, Any],
        approval_reason: str,
        idempotency_key: str,
        approved_by_user_id: str,
        request_id: str,
        release_evidence: RelayModelReleaseEvidence,
        supersedes_plan_id: str | None = None,
        batch_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        # Capture caller intent separately from the live Relay snapshot so an
        # exact retry can return its immutable receipt after routing changes.
        request_intent = {
            "model_id": model_id,
            "expected_capability_version": expected_capability_version,
            "expected_candidate_revision": expected_candidate_revision,
            "expected_catalog_revision": expected_catalog_revision,
            "expected_routing_release_sha256": expected_routing_release_sha256,
            "provider_cost_currency": provider_cost_currency,
            "provider_cost_formula": provider_cost_formula,
            "provider_cost_evidence_kind": provider_cost_evidence_kind,
            "provider_cost_evidence_reference": provider_cost_evidence_reference,
            "provider_cost_evidence_sha256": provider_cost_evidence_sha256,
            "provider_cost_effective_at": provider_cost_effective_at,
            "fx_cny_micros_per_currency_unit": fx_cny_micros_per_currency_unit,
            "fx_source": fx_source, "fx_version": fx_version,
            "fx_evidence_sha256": fx_evidence_sha256,
            "fx_effective_at": fx_effective_at,
            "personal_price_points": personal_price_points,
            "enterprise_price_points": enterprise_price_points,
            "personal_config_override": personal_config_override,
            "enterprise_config_override": enterprise_config_override,
            "approval_reason": approval_reason,
            "approved_by_user_id": approved_by_user_id,
            "supersedes_plan_id": supersedes_plan_id,
        }
        request_fingerprint = _canonical_sha256(request_intent)
        # A local lab's marker is the plan-set serialization lock.  Take it
        # before the normal company/model locks so no approval can appear
        # between exact-set enumeration and atomic activation.
        lab_marker = cls._local_video_lab_marker(session, lock=True)
        cls.lock_distribution_companies(session)
        model = session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if model is None:
            raise NotFoundError("模型不存在")
        existing_by_key = session.scalar(
            select(ModelCommercialReleasePlan)
            .where(ModelCommercialReleasePlan.idempotency_key == idempotency_key)
        )
        if lab_marker is not None and existing_by_key is None:
            activated = session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action == LOCAL_VIDEO_LAB_ACTIVATION_ACTION,
                    AuditLog.target_type == "local_video_lab_activation",
                    AuditLog.target_id == lab_marker.target_id,
                )
                .limit(1)
            )
            if activated is not None:
                raise ConflictError(
                    "本地视频联调已完成 exact activation，不能追加商业计划"
                )
        if existing_by_key is not None and existing_by_key.request_fingerprint is not None:
            fingerprint_matches = hmac.compare_digest(
                existing_by_key.request_fingerprint, request_fingerprint
            )
            if not fingerprint_matches:
                # Pre-route-fence plans already pinned their route identity.
                # Permit only an exact historical retry whose newly supplied
                # fence identifies that same immutable route; never adopt the
                # current live route or rewrite an old approval on retry.
                previous_intent = dict(request_intent)
                previous_intent.pop("expected_routing_release_sha256")
                stored_routing_release = (existing_by_key.approved_route_identity or {}).get(
                    "routing_release_sha256"
                )
                fingerprint_matches = (
                    isinstance(stored_routing_release, str)
                    and hmac.compare_digest(stored_routing_release, expected_routing_release_sha256)
                    and hmac.compare_digest(
                        existing_by_key.request_fingerprint,
                        _canonical_sha256(previous_intent),
                    )
                )
            if not fingerprint_matches:
                raise ConflictError("幂等键已被不同的商业发布计划使用")
            execution = session.scalar(
                select(ModelCommercialReleaseExecution)
                .where(ModelCommercialReleaseExecution.plan_id == existing_by_key.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if execution is None:
                raise RuntimeError("commercial release execution is missing")
            return cls._response(plan=existing_by_key, execution=execution, model=model), False
        if model.capability_version != expected_capability_version:
            raise ConflictError("模型能力版本已变化，请刷新后重新审批价格")
        if (
            model.relay_capability_candidate_revision
            != expected_candidate_revision
            or model.relay_capability_candidate_catalog_revision
            != expected_catalog_revision
            or model.relay_capability_candidate is None
        ):
            raise ConflictError("Relay 候选版本已变化，请刷新后重新审批价格")
        # A later route invalidation can block an execution while preserving
        # its immutable receipt. That is still commercial history, never
        # current publication authority. Legacy recovery is only for rows
        # which have not crossed that boundary at all.
        has_released_history = session.scalar(
            select(ModelCommercialReleasePlan.id)
            .join(ModelCommercialReleaseExecution,
                  ModelCommercialReleaseExecution.plan_id == ModelCommercialReleasePlan.id)
            .where(
                ModelCommercialReleasePlan.model_id == model.id,
                or_(
                    ModelCommercialReleaseExecution.state == "released",
                    ModelCommercialReleaseExecution.publication_receipt.is_not(None),
                ),
            )
            .limit(1)
        ) is not None
        recovering_legacy_publication = (
            model.published_at is not None and not has_released_history
        )
        if model.active and not has_released_history:
            raise ConflictError(
                "已发布模型缺少历史商业发布计划，请先停用模型，再审批当前成本和路由以恢复商业发布"
            )
        route_item = next(
            (
                item
                for item in release_evidence.models
                if item.public_model_id == model.slug
            ),
            None,
        )
        if (
            route_item is None
            or route_item.capability_revision != expected_candidate_revision
            or route_item.published_route_revision is None
            or not route_item.routes
            or any(
                route.provider_name is None
                or route.provider_account_id is None
                or route.provider_key_index is None
                or route.provider_key_fingerprint_prefix is None
                or route.provider_credential_set_version is None
                or route.route_binding_sha256 is None
                for route in route_item.routes
            )
        ):
            raise ConflictError("Relay 候选缺少可固定的供应商账号路由证据")
        if not hmac.compare_digest(
            route_item.routing_release_sha256, expected_routing_release_sha256
        ):
            raise ConflictError("Relay 路由发布版本已变化，请刷新并重新审阅账号和路由后审批")
        approved_route_identity, approved_route_identity_sha256 = (
            commercial_route_release_snapshot(route_item)
        )
        if provider_cost_currency not in {"CNY", "USD"}:
            raise ConflictError("供应商成本币种无效")
        if provider_cost_evidence_kind not in {
            "provider_price_list",
            "contract_rate",
            "provider_invoice",
        }:
            raise ConflictError("供应商成本必须绑定官方价格表、合同费率或供应商账单")
        if fx_cny_micros_per_currency_unit <= 0:
            raise ConflictError("供应商成本汇率必须为正数")
        if (
            provider_cost_currency == "CNY"
            and fx_cny_micros_per_currency_unit != MICROS_PER_CURRENCY_UNIT
        ):
            raise ConflictError("人民币成本必须绑定 1:1 汇率快照")
        cost_reference = provider_cost_evidence_reference.strip()
        normalized_fx_source = fx_source.strip()
        normalized_fx_version = fx_version.strip()
        normalized_reason = approval_reason.strip()
        if not 3 <= len(cost_reference) <= 500:
            raise ConflictError("供应商成本证据引用无效")
        if provider_cost_evidence_kind == "provider_price_list":
            parsed_reference = urlsplit(cost_reference)
            if (
                parsed_reference.scheme != "https"
                or not parsed_reference.hostname
                or parsed_reference.username is not None
                or parsed_reference.password is not None
                or parsed_reference.fragment
            ):
                raise ConflictError("官方价格表证据必须使用无凭据、无片段的 HTTPS 地址")
        if not 2 <= len(normalized_fx_source) <= 120:
            raise ConflictError("汇率来源无效")
        if not 2 <= len(normalized_fx_version) <= 160:
            raise ConflictError("汇率版本无效")
        if not 3 <= len(normalized_reason) <= 500:
            raise ConflictError("审批原因必须包含 3 到 500 个字符")

        capability_map = {"generation": model.relay_capability_candidate}
        personal_effective = CommercialPricingPolicy.project_personal_capabilities(
            capability_map=capability_map,
            config_override=personal_config_override,
            provider_cost_formula=provider_cost_formula,
            require_usable=True,
        )
        enterprise_effective = TaskCapabilityAdmission.effective_capabilities(
            capability_map=capability_map,
            config_override=enterprise_config_override,
            strict_catalog=True,
            strict_override=bool(enterprise_config_override),
            require_usable=True,
        )
        normalized_formula = cls._validate_provider_cost_formula(
            provider_cost_formula,
            billing_mode=model.billing_mode,
            candidate_revision=expected_candidate_revision,
            effective_capabilities=(personal_effective, enterprise_effective),
        )
        provider_cost_micros = sum(
            _ceil_div(
                component["rate_micros"]
                * component["quantity_numerator"],
                component["quantity_denominator"],
            )
            for component in normalized_formula["components"]
        )
        if provider_cost_micros <= 0:
            raise ConflictError("供应商成本公式未产生正数成本")
        cost_effective_at = cls._as_utc(provider_cost_effective_at)
        normalized_fx_effective_at = cls._as_utc(fx_effective_at)
        provider_cost_cny_micros, minimum_points = minimum_price_points(
            provider_cost_micros=provider_cost_micros,
            fx_cny_micros=fx_cny_micros_per_currency_unit,
        )
        resolved_personal_price = personal_price_points or minimum_points
        resolved_enterprise_price = (
            enterprise_price_points or minimum_points
        )
        if (
            resolved_personal_price < minimum_points
            or resolved_enterprise_price < minimum_points
            or resolved_personal_price > _MAX_PRICE_POINTS
            or resolved_enterprise_price > _MAX_PRICE_POINTS
        ):
            raise ConflictError("积分价格不得低于 30% 目标毛利对应的最低价格")

        prior_plans = list(session.scalars(
            select(ModelCommercialReleasePlan)
            .where(ModelCommercialReleasePlan.model_id == model.id,
                   ModelCommercialReleasePlan.candidate_revision == expected_candidate_revision)
            .order_by(ModelCommercialReleasePlan.revision)
        ).all())
        predecessor = prior_plans[-1] if prior_plans else None
        predecessor_execution = None
        if existing_by_key is not None:
            # Pre-0055 plans do not have a request fingerprint. Preserve their
            # original content-hash replay check below; do not require them to
            # supersede themselves just to replay their original key.
            plan_revision = existing_by_key.revision
        elif predecessor is None:
            if supersedes_plan_id is not None:
                raise ConflictError("被替代的商业审批计划不是当前候选的最新计划")
            plan_revision = 1
        else:
            if supersedes_plan_id != predecessor.id:
                raise ConflictError("该候选已有商业审批，请显式指定最新计划进行重新审批")
            predecessor_execution = session.scalar(
                select(ModelCommercialReleaseExecution)
                .where(ModelCommercialReleaseExecution.plan_id == predecessor.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if predecessor_execution is None or predecessor_execution.state not in {"approved", "blocked", "released"}:
                raise ConflictError("只有当前未被替代的商业审批可以创建后继计划")
            plan_revision = predecessor.revision + 1

        content = {
            "schema_version": 3,
            "revision": plan_revision,
            "supersedes_plan_id": supersedes_plan_id,
            "model_id": model.id,
            "candidate_revision": expected_candidate_revision,
            "candidate_catalog_revision": expected_catalog_revision,
            "capability_version": expected_capability_version,
            "billing_mode": model.billing_mode,
            "provider_cost_currency": provider_cost_currency,
            "provider_cost_formula": normalized_formula,
            "provider_cost_micros": provider_cost_micros,
            "provider_cost_cny_micros": provider_cost_cny_micros,
            "provider_cost_evidence_kind": provider_cost_evidence_kind,
            "provider_cost_evidence_reference": cost_reference,
            "provider_cost_evidence_sha256": provider_cost_evidence_sha256,
            "provider_cost_effective_at": cost_effective_at,
            "fx_cny_micros_per_currency_unit": fx_cny_micros_per_currency_unit,
            "fx_source": normalized_fx_source,
            "fx_version": normalized_fx_version,
            "fx_evidence_sha256": fx_evidence_sha256,
            "fx_effective_at": normalized_fx_effective_at,
            "points_per_cny": POINTS_PER_CNY,
            "target_margin_bps": TARGET_MARGIN_BPS,
            "minimum_price_points": minimum_points,
            "personal_price_points": resolved_personal_price,
            "enterprise_price_points": resolved_enterprise_price,
            "enterprise_distribution_scope": ENTERPRISE_DISTRIBUTION_SCOPE,
            "personal_config_override": personal_config_override,
            "enterprise_config_override": enterprise_config_override,
            "approval_reason": normalized_reason,
            "approved_by_user_id": approved_by_user_id,
            "approved_route_identity": approved_route_identity,
            "approved_route_identity_sha256": approved_route_identity_sha256,
        }
        content_sha256 = _canonical_sha256(content)
        existing_by_key = session.scalar(
            select(ModelCommercialReleasePlan).where(
                ModelCommercialReleasePlan.idempotency_key == idempotency_key
            )
        )
        if existing_by_key is not None:
            legacy_content = dict(content)
            legacy_content.pop("revision")
            legacy_content.pop("supersedes_plan_id")
            legacy_content["schema_version"] = 2
            legacy_hashes = {content_sha256, _canonical_sha256(legacy_content)}
            legacy_content["schema_version"] = 1
            legacy_content.pop("approved_route_identity")
            legacy_content.pop("approved_route_identity_sha256")
            legacy_hashes.add(_canonical_sha256(legacy_content))
            if existing_by_key.content_sha256 not in legacy_hashes:
                raise ConflictError("幂等键已被不同的商业发布计划使用")
            execution = session.scalar(
                select(ModelCommercialReleaseExecution).where(
                    ModelCommercialReleaseExecution.plan_id == existing_by_key.id
                )
            )
            if execution is None:
                raise RuntimeError("commercial release execution is missing")
            existing_model = session.get(ModelDefinition, existing_by_key.model_id)
            assert existing_model is not None
            return cls._response(
                plan=existing_by_key,
                execution=execution,
                model=existing_model,
            ), False
        plan = ModelCommercialReleasePlan(
            id=new_id(),
            revision=plan_revision,
            supersedes_plan_id=supersedes_plan_id,
            # Immutable binding: this table rejects UPDATE, so a plan can only
            # join a batch here, at INSERT time.
            batch_id=batch_id,
            request_fingerprint=request_fingerprint,
            model_id=model.id,
            candidate_revision=expected_candidate_revision,
            candidate_catalog_revision=expected_catalog_revision,
            capability_version=expected_capability_version,
            billing_mode=model.billing_mode,
            provider_cost_currency=provider_cost_currency,
            provider_cost_formula=normalized_formula,
            provider_cost_micros=provider_cost_micros,
            provider_cost_cny_micros=provider_cost_cny_micros,
            provider_cost_evidence_kind=provider_cost_evidence_kind,
            provider_cost_evidence_reference=cost_reference,
            provider_cost_evidence_sha256=provider_cost_evidence_sha256,
            provider_cost_effective_at=cost_effective_at,
            fx_cny_micros_per_currency_unit=fx_cny_micros_per_currency_unit,
            fx_source=normalized_fx_source,
            fx_version=normalized_fx_version,
            fx_evidence_sha256=fx_evidence_sha256,
            fx_effective_at=normalized_fx_effective_at,
            points_per_cny=POINTS_PER_CNY,
            target_margin_bps=TARGET_MARGIN_BPS,
            minimum_price_points=minimum_points,
            personal_price_points=resolved_personal_price,
            enterprise_price_points=resolved_enterprise_price,
            enterprise_distribution_scope=ENTERPRISE_DISTRIBUTION_SCOPE,
            personal_config_override=personal_config_override,
            enterprise_config_override=enterprise_config_override,
            approval_reason=normalized_reason,
            approved_by_user_id=approved_by_user_id,
            approved_at=utcnow(),
            idempotency_key=idempotency_key,
            content_sha256=content_sha256,
            approved_route_identity=approved_route_identity,
            approved_route_identity_sha256=approved_route_identity_sha256,
        )
        if any(rectangle.rate_set_id is not None
               for route in route_item.routes
               for rectangle in route.provider_cost_rectangles) and not (
            CommercialPricingPolicy.route_cost_matches_plan(plan=plan, route_item=route_item)
        ):
            raise ConflictError(
                "商业成本公式低于 Relay 已验证的客户计费单元成本上限，或缺少可执行上限证据；请刷新并复核费率、汇率和能力数量后重新审批"
            )
        execution = ModelCommercialReleaseExecution(
            id=new_id(),
            plan_id=plan.id,
            state="approved",
            attempt_count=0,
            company_ids=[],
        )
        session.add(plan)
        try:
            # These mappers intentionally have no ORM relationship. Persist
            # the immutable parent before its execution FK, also on PostgreSQL.
            session.flush()
            session.add(execution)
            if predecessor_execution is not None and predecessor_execution.state != "released":
                predecessor_execution.state = "superseded"
                predecessor_execution.last_blocker_code = "approval_superseded"
                predecessor_execution.last_blocker_message = "已由新的明确审批替代"
            # A released predecessor remains an immutable historical release.
            # Its successor becomes the current price authority only after its
            # own execution has atomically recorded a new publication receipt.
            session.flush()
        except IntegrityError as exc:
            raise ConflictError("商业发布计划已被并发创建，请刷新后重试") from exc
        AuditService.append(
            session,
            actor_user_id=approved_by_user_id,
            action="model.commercial_release_plan.approve",
            target_type="model_commercial_release_plan",
            target_id=plan.id,
            before_summary={"supersedes_plan_id": supersedes_plan_id},
            after_summary={
                "model_id": model.id,
                "revision": plan_revision,
                "supersedes_plan_id": supersedes_plan_id,
                "candidate_revision": expected_candidate_revision,
                "catalog_revision": expected_catalog_revision,
                "expected_routing_release_sha256": expected_routing_release_sha256,
                "billing_mode": model.billing_mode,
                "cost_evidence_sha256": provider_cost_evidence_sha256,
                "fx_evidence_sha256": fx_evidence_sha256,
                "points_per_cny": POINTS_PER_CNY,
                "target_margin_bps": TARGET_MARGIN_BPS,
                "minimum_price_points": minimum_points,
                "personal_price_points": resolved_personal_price,
                "enterprise_price_points": resolved_enterprise_price,
                "content_sha256": content_sha256,
                "approved_route_identity_sha256": (
                    approved_route_identity_sha256
                ),
                "automatic_release": False,
                "recovering_legacy_publication": recovering_legacy_publication,
            },
            request_id=request_id,
        )
        if recovering_legacy_publication:
            AuditService.append(
                session,
                actor_user_id=approved_by_user_id,
                action="model.commercial_release_recovery.approve",
                target_type="model_definition",
                target_id=model.id,
                before_summary={
                    "active": model.active,
                    "published_at": _audit_safe(model.published_at),
                    "released_commercial_plan_exists": False,
                },
                after_summary={
                    "commercial_plan_id": plan.id,
                    "plan_content_sha256": plan.content_sha256,
                    "approved_route_identity_sha256": approved_route_identity_sha256,
                    "approval_reason": normalized_reason,
                    "requires_explicit_reactivation": True,
                },
                request_id=request_id,
            )
        return cls._response(plan=plan, execution=execution, model=model), True

    @classmethod
    def _block(
        cls,
        execution: ModelCommercialReleaseExecution,
        *,
        code: str,
        message: str,
    ) -> None:
        execution.state = "blocked"
        execution.last_blocker_code = code
        execution.last_blocker_message = message
        execution.released_at = None

    @classmethod
    def _route_item(
        cls,
        *,
        model: ModelDefinition,
        plan: ModelCommercialReleasePlan,
        evidence: RelayModelReleaseEvidence,
    ) -> RelayModelReleaseEvidenceItem | None:
        item = next(
            (row for row in evidence.models if row.public_model_id == model.slug),
            None,
        )
        if item is None or item.capability_revision != plan.candidate_revision:
            return None
        return item

    _route_cost_matches_plan = staticmethod(CommercialPricingPolicy.route_cost_matches_plan)

    @classmethod
    def _release_one(
        cls,
        session: Session,
        *,
        plan: ModelCommercialReleasePlan,
        execution: ModelCommercialReleaseExecution,
        catalog: RelayModelCatalog,
        route_item: RelayModelReleaseEvidenceItem,
        request_id: str,
        trigger: str,
        companies: tuple[Company, ...],
        company_policy: Mapping[str, int] | None = None,
        personal_policy: Mapping[str, int] | None = None,
    ) -> None:
        route_identity, route_identity_sha256 = commercial_route_release_snapshot(
            route_item
        )
        if (
            plan.approved_route_identity is None
            or plan.approved_route_identity_sha256 is None
            or route_identity != plan.approved_route_identity
            or not hmac.compare_digest(
                route_identity_sha256, plan.approved_route_identity_sha256
            )
        ):
            raise ConflictError("Relay 供应商账号、密钥版本或路由发布已漂移")
        # Companies were locked before models and executions by reconcile.
        model = session.get(ModelDefinition, plan.model_id)
        if model is None:
            raise NotFoundError("模型不存在")
        price_revision = model.published_at is not None
        relay_model = RelayCapabilityService.relay_model(
            catalog, model_slug=model.slug
        )
        RelayCapabilityService.require_customer_callable_model(relay_model)
        if (
            relay_model.capability_revision != plan.candidate_revision
            or relay_model.capabilities.contract_dump()
            != model.relay_capability_candidate
            or relay_model.published_route_revision
            != route_item.published_route_revision
        ):
            raise ConflictError("商业计划绑定的 Relay 候选已漂移")
        before_approval, locked_model, approval_changed, compatibility, diff = (
            RelayCapabilityService.approve_candidate(
                session,
                model_id=model.id,
                expected_capability_version=plan.capability_version,
                expected_catalog_revision=(
                    model.relay_capability_candidate_catalog_revision
                ),
                expected_candidate_revision=plan.candidate_revision,
                live_catalog_revision=catalog.catalog_revision,
                live_candidate_revision=relay_model.capability_revision,
                live_candidate=relay_model.capabilities.contract_dump(),
            )
        )
        route_evidence = route_item.model_dump(mode="json")
        readiness = build_model_release_readiness(
            model=locked_model,
            evidence=route_evidence,
        )
        if approval_changed:
            AuditService.append(
                session,
                actor_user_id=None,
                actor_kind="system",
                actor_key=_SYSTEM_ACTOR,
                action="model.relay_capability.approve",
                target_type="model_definition",
                target_id=locked_model.id,
                before_summary=_audit_safe(before_approval),
                after_summary=_audit_safe({
                    **ModelCatalogService.response(session, model=locked_model),
                    "commercial_plan_id": plan.id,
                    "commercial_plan_content_sha256": plan.content_sha256,
                    "compatibility": compatibility,
                    "capability_diff": diff,
                    "route_release_evidence": route_evidence,
                    "preapproved_by_user_id": plan.approved_by_user_id,
                    "trigger": trigger,
                }),
                request_id=request_id,
            )
        # A cost/price successor does not reactivate a deliberately stopped
        # model and never rewrites its original publication timestamp.
        publish_changed = False
        if not price_revision:
            before_publish, locked_model, publish_changed = ModelCatalogService.publish(
                session,
                model_id=locked_model.id,
                require_relay_capability_revision=True,
                expected_release_snapshot=readiness.expected_snapshot,
                publishing_plan_id=plan.id,
            )
        if publish_changed:
            AuditService.append(
                session,
                actor_user_id=None,
                actor_kind="system",
                actor_key=_SYSTEM_ACTOR,
                action="model.publish",
                target_type="model_definition",
                target_id=locked_model.id,
                before_summary=_audit_safe(before_publish),
                after_summary=_audit_safe({
                    **ModelCatalogService.response(session, model=locked_model),
                    "commercial_plan_id": plan.id,
                    "route_release_evidence": route_evidence,
                    "trigger": trigger,
                }),
                request_id=request_id,
            )

        current_personal = session.scalar(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == locked_model.id
            )
        )
        current_quote = (
            PersonalRetailGrantService.response(
                session, model=locked_model, grant=current_personal
            )["quote_revision"]
            if current_personal is not None
            else None
        )
        personal_price = max(
            plan.personal_price_points,
            (
                current_personal.price_per_second_points
                or current_personal.price_per_item_points or 0
            )
            if price_revision and current_personal is not None else 0,
        )
        personal_enabled = (
            current_personal.enabled
            if price_revision and current_personal is not None else True
        )
        personal_config = (
            current_personal.config_override
            if price_revision and current_personal is not None
            else plan.personal_config_override
        )
        personal_before, personal_after, personal_changed = (
            PersonalRetailGrantService.upsert(
                session,
                model_id=locked_model.id,
                expected_capability_version=locked_model.capability_version,
                expected_quote_revision=current_quote,
                enabled=personal_enabled,
                price_per_second_points=(
                    personal_price
                    if plan.billing_mode == "per_second"
                    else None
                ),
                price_per_item_points=(
                    personal_price
                    if plan.billing_mode == "per_item"
                    else None
                ),
                config_override=personal_config,
                require_relay_approval=True,
                expected_release_snapshot=readiness.expected_snapshot,
                publishing_plan_id=plan.id,
                **(
                    {
                        "call_quota": personal_policy["call_quota"],
                        "concurrency_limit": personal_policy[
                            "concurrency_limit"
                        ],
                    }
                    if personal_policy is not None
                    else {}
                ),
            )
        )
        if personal_changed:
            AuditService.append(
                session,
                actor_user_id=None,
                actor_kind="system",
                actor_key=_SYSTEM_ACTOR,
                action="personal.model_grant.update",
                target_type="personal_retail_model_grant",
                target_id=personal_after["grant_id"],
                before_summary=_audit_safe(personal_before),
                after_summary=_audit_safe({
                    **personal_after,
                    "commercial_plan_id": plan.id,
                    "preapproved_by_user_id": plan.approved_by_user_id,
                }),
                request_id=request_id,
            )

        released_company_ids: list[str] = []
        company_receipts: list[dict[str, Any]] = []
        for company in companies:
            previous = session.scalar(select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company.id,
                CompanyModelGrant.model_id == locked_model.id,
            ))
            keep_policy = price_revision and previous is not None
            company_price = max(
                plan.enterprise_price_points,
                (previous.price_per_second_points or previous.price_per_item_points or 0)
                if keep_policy else 0,
            )
            grant = ModelGrantService.upsert_grant(
                session,
                company_id=company.id,
                model_id=locked_model.id,
                enabled=previous.enabled if keep_policy else True,
                price_per_second_cents=None,
                price_per_item_cents=None,
                price_per_second_points=(
                    company_price
                    if plan.billing_mode == "per_second"
                    else None
                ),
                price_per_item_points=(
                    company_price
                    if plan.billing_mode == "per_item"
                    else None
                ),
                config_override=previous.config_override if keep_policy else plan.enterprise_config_override,
                call_quota=(
                    company_policy["call_quota"]
                    if company_policy is not None
                    else previous.call_quota if keep_policy else None
                ),
                concurrency_limit=(
                    company_policy["concurrency_limit"]
                    if company_policy is not None
                    else previous.concurrency_limit if keep_policy else None
                ),
                effective_at=ModelCatalogService._as_utc(previous.effective_at) if keep_policy else None,
                expires_at=ModelCatalogService._as_utc(previous.expires_at) if keep_policy else None,
                actor_user_id=plan.approved_by_user_id,
                expected_release_snapshot=readiness.expected_snapshot,
                publishing_plan_id=plan.id,
            )
            released_company_ids.append(company.id)
            company_receipts.append({
                "company_id": company.id,
                "grant_id": grant.id,
                "enabled": grant.enabled,
                "unit_price_points": company_price,
                "price_version_id": grant.point_price_active_version_id,
                "config_override": grant.config_override,
                "call_quota": grant.call_quota,
                "concurrency_limit": grant.concurrency_limit,
                "effective_at": _audit_safe(ModelCatalogService._as_utc(grant.effective_at)),
                "expires_at": _audit_safe(ModelCatalogService._as_utc(grant.expires_at)),
            })
            # A compact per-company audit remains queryable without exposing
            # any Relay route or credential material to the tenant surface.
            AuditService.append(
                session,
                actor_user_id=None,
                actor_kind="system",
                actor_key=_SYSTEM_ACTOR,
                action="company.model_grant.upsert",
                target_type="company_model_grant",
                target_id=grant.id,
                before_summary={},
                after_summary={
                    "company_id": company.id,
                    "model_id": locked_model.id,
                    "billing_unit": "POINT",
                    "unit_price_points": company_price,
                    "commercial_plan_id": plan.id,
                    "preapproved_by_user_id": plan.approved_by_user_id,
                },
                request_id=request_id,
            )

        released_at = utcnow()
        relay_route_release = commercial_route_publication_evidence(
            route_item,
            route_release_evidence=route_evidence,
        )
        if not hmac.compare_digest(
            relay_route_release["route_identity_sha256"],
            route_identity_sha256,
        ):
            raise ConflictError("Relay 路由发布回执与已批准路由身份不一致")
        publication_receipt = {
            "schema_version": 4,
            "plan_id": plan.id,
            "plan_content_sha256": plan.content_sha256,
            "model_id": locked_model.id,
            "model_slug": locked_model.slug,
            "capability_revision": locked_model.relay_capability_revision,
            "route_identity_sha256": route_identity_sha256,
            "relay_route_release": relay_route_release,
            "personal_grant_id": personal_after["grant_id"],
            "personal_price_points": personal_price,
            "personal_enabled": personal_enabled,
            "personal_quote_revision": personal_after["quote_revision"],
            "personal_config_override": personal_after["config_override"],
            "personal_call_quota": personal_after["call_quota"],
            "personal_concurrency_limit": personal_after[
                "concurrency_limit"
            ],
            "enterprise_price_points": plan.enterprise_price_points,
            "company_grants": company_receipts,
            "distribution_policy": "preserve_restrictions_and_higher_prices" if price_revision else "initial_commercial_release",
            "company_ids": released_company_ids,
            "company_grant_count": len(released_company_ids),
            "released_at": released_at.astimezone(timezone.utc).isoformat(),
        }
        execution.state = "released"
        execution.last_blocker_code = None
        execution.last_blocker_message = None
        execution.route_release_evidence = route_evidence
        execution.released_route_identity_sha256 = route_identity_sha256
        execution.publication_receipt = publication_receipt
        execution.publication_receipt_sha256 = canonical_sha256(
            publication_receipt
        )
        execution.personal_grant_id = personal_after["grant_id"]
        execution.company_grant_count = len(released_company_ids)
        execution.company_ids = released_company_ids
        execution.released_at = released_at
        AuditService.append(
            session,
            actor_user_id=None,
            actor_kind="system",
            actor_key=_SYSTEM_ACTOR,
            action="model.commercial_release.execute",
            target_type="model_commercial_release_plan",
            target_id=plan.id,
            before_summary={"state": "approved_or_blocked"},
            after_summary={
                "state": "released",
                "model_id": locked_model.id,
                "personal_grant_id": execution.personal_grant_id,
                "company_grant_count": execution.company_grant_count,
                "company_ids": released_company_ids,
                "route_release_evidence": route_evidence,
                "route_identity_sha256": route_identity_sha256,
                "relay_route_release": relay_route_release,
                "publication_receipt_sha256": (
                    execution.publication_receipt_sha256
                ),
                "provider_cost_evidence_sha256": (
                    plan.provider_cost_evidence_sha256
                ),
                "fx_evidence_sha256": plan.fx_evidence_sha256,
                "trigger": trigger,
            },
            request_id=request_id,
        )
        session.flush()

    @classmethod
    def activate_local_lab_exact(
        cls,
        session: Session,
        *,
        intent: Mapping[str, Any],
        catalog: RelayModelCatalog,
        release_evidence: RelayModelReleaseEvidence,
        approved_by_user_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Atomically activate one exact, isolated local-lab commercial set.

        The ordinary reconciler intentionally makes per-plan progress.  That
        behavior is unsafe for a paid lab acceptance set, where a failed apply
        must expose neither a partial model grant nor a separately committed
        test balance.  This entry point is gated by the durable lab marker and
        lets the request transaction own every authorization and credit.
        """

        existing = cls._local_lab_activation_receipt(
            session,
            intent=intent,
            lock_marker=True,
        )
        if existing is not None:
            return existing

        validate_model_catalog_release_evidence_pair(
            catalog=catalog,
            evidence=release_evidence,
        )

        lab_id = str(intent["lab_id"])
        budget = intent["budget"]
        company_scope = intent["company_scope"]
        personal_scope = intent["personal_scope"]
        scope_keys = {"user_id", "company_id", "personal_workspace_id"}
        if (
            not isinstance(company_scope, Mapping)
            or not isinstance(personal_scope, Mapping)
            or set(company_scope) != scope_keys
            or set(personal_scope) != scope_keys
            or not isinstance(company_scope.get("user_id"), str)
            or not company_scope["user_id"]
            or not isinstance(company_scope.get("company_id"), str)
            or not company_scope["company_id"]
            or company_scope.get("personal_workspace_id") is not None
            or not isinstance(personal_scope.get("user_id"), str)
            or not personal_scope["user_id"]
            or personal_scope.get("company_id") is not None
            or not isinstance(personal_scope.get("personal_workspace_id"), str)
            or not personal_scope["personal_workspace_id"]
            or company_scope["user_id"] == personal_scope["user_id"]
        ):
            raise ConflictError("本地视频联调个人与企业作用域必须严格互斥")
        company_id = company_scope["company_id"]
        company_user_id = company_scope["user_id"]
        personal_user_id = personal_scope["user_id"]
        personal_workspace_id = personal_scope["personal_workspace_id"]
        requested_plans = tuple(intent["plans"])
        requested_by_id = {item["plan_id"]: item for item in requested_plans}

        plan_key_prefix = f"lab-commercial-plan:{lab_id}:"
        plans = tuple(session.scalars(
            select(ModelCommercialReleasePlan)
            .order_by(ModelCommercialReleasePlan.model_id)
        ).all())
        if (
            any(not plan.idempotency_key.startswith(plan_key_prefix) for plan in plans)
            or {plan.id for plan in plans} != set(requested_by_id)
            or len(plans) != len(requested_plans)
        ):
            raise ConflictError("本地视频联调必须一次激活服务端完整商业计划集合")

        # Preserve the production lock order: every distribution company,
        # both exact principals, every model in deterministic order, then every
        # execution.  The marker lock taken above serializes plan creation.
        companies = tuple(session.scalars(
            select(Company)
            .order_by(Company.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all())
        if (
            len(companies) != 1
            or companies[0].id != company_id
            or companies[0].status != CompanyStatus.ACTIVE
            or companies[0].billing_version != 2
        ):
            raise ConflictError("本地视频联调隔离库包含非预期企业分发作用域")

        scope_user_ids = tuple(sorted((company_user_id, personal_user_id)))
        scope_users = tuple(session.scalars(
            select(User)
            .where(User.id.in_(scope_user_ids))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all())
        users_by_id = {user.id: user for user in scope_users}
        company_user = users_by_id.get(company_user_id)
        personal_user = users_by_id.get(personal_user_id)
        if (
            len(scope_users) != 2
            or company_user is None
            or company_user.status != UserStatus.ACTIVE
            or company_user.account_type != UserAccountType.COMPANY
            or company_user.is_platform_admin
            or personal_user is None
            or personal_user.status != UserStatus.ACTIVE
            or personal_user.account_type != UserAccountType.PERSONAL
            or personal_user.is_platform_admin
        ):
            raise ConflictError("本地视频联调企业或个人用户作用域已漂移")
        membership = session.scalar(
            select(CompanyMembership)
            .where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.user_id == company_user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if membership is None or membership.status != MembershipStatus.ACTIVE:
            raise ConflictError("本地视频联调企业成员作用域已漂移")
        workspace = session.scalar(
            select(PersonalWorkspace).where(
                PersonalWorkspace.id == personal_workspace_id,
                PersonalWorkspace.user_id == personal_user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if workspace is None or not workspace.active:
            raise ConflictError("本地视频联调个人作用域已漂移")

        model_ids = tuple(sorted({plan.model_id for plan in plans}))
        all_models = tuple(session.scalars(
            select(ModelDefinition)
            .order_by(ModelDefinition.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all())
        all_models_by_id = {model.id: model for model in all_models}
        if any(model_id not in all_models_by_id for model_id in model_ids):
            raise ConflictError("本地视频联调商业计划未一一绑定模型")
        models_by_id = {model_id: all_models_by_id[model_id] for model_id in model_ids}
        all_executions = tuple(session.scalars(
            select(ModelCommercialReleaseExecution)
            .order_by(ModelCommercialReleaseExecution.plan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all())
        if (
            len(all_executions) != len(plans)
            or {execution.plan_id for execution in all_executions}
            != set(requested_by_id)
        ):
            raise ConflictError("本地视频联调商业计划执行记录不完整")
        executions_by_plan = {
            execution.plan_id: execution for execution in all_executions
        }

        company_credit_key = f"lab-test-budget:{lab_id}"
        personal_credit_key = f"lab-personal-test-budget:{lab_id}"
        historical_partial = any(
            execution.state == "released"
            or execution.publication_receipt is not None
            or execution.released_at is not None
            for execution in all_executions
        )
        historical_partial = historical_partial or any(
            model.active
            or model.published_at is not None
            or model.relay_capability_revision is not None
            for model in all_models
        )
        historical_partial = historical_partial or session.scalar(
            select(PersonalRetailModelGrant.id)
            .limit(1)
        ) is not None
        historical_partial = historical_partial or session.scalar(
            select(CompanyModelGrant.id)
            .limit(1)
        ) is not None
        historical_partial = historical_partial or session.scalar(
            select(CompanyPointLot.id)
            .limit(1)
        ) is not None
        historical_partial = historical_partial or session.scalar(
            select(PersonalPointLot.id)
            .limit(1)
        ) is not None
        if historical_partial:
            raise ConflictError(
                "本地视频联调存在无原子激活回执的历史部分授权；请重建隔离 lab"
            )

        # Complete every read-only invariant check before the first release.
        release_rows: list[
            tuple[
                ModelCommercialReleasePlan,
                ModelCommercialReleaseExecution,
                ModelDefinition,
                RelayModelReleaseEvidenceItem,
            ]
        ] = []
        for plan in plans:
            requested = requested_by_id[plan.id]
            model = models_by_id[plan.model_id]
            execution = executions_by_plan[plan.id]
            if (
                requested["model_id"] != model.id
                or requested["model_slug"] != model.slug
                or requested["plan_content_sha256"] != plan.content_sha256
                or plan.idempotency_key
                != f"lab-commercial-plan:{lab_id}:{model.slug}"
                or plan.approved_by_user_id != approved_by_user_id
            ):
                raise ConflictError("本地视频联调商业计划意图已漂移")
            if (
                plan.billing_mode != "per_second"
                or plan.personal_price_points
                != budget["unit_price_points_per_second"]
                or plan.enterprise_price_points
                != budget["unit_price_points_per_second"]
                or plan.enterprise_distribution_scope
                != ENTERPRISE_DISTRIBUTION_SCOPE
            ):
                raise ConflictError("本地视频联调商业价格或分发策略已漂移")
            if execution.state not in {"approved", "blocked"}:
                raise ConflictError("本地视频联调商业计划不处于可激活状态")
            if (
                model.capability_version != plan.capability_version
                or model.relay_capability_candidate_revision
                != plan.candidate_revision
            ):
                raise ConflictError("本地视频联调 Relay 候选已漂移")
            relay_model = next(
                (item for item in catalog.data if item.id == model.slug), None
            )
            if (
                relay_model is None
                or relay_model.capability_revision != plan.candidate_revision
                or relay_model.capabilities.contract_dump()
                != model.relay_capability_candidate
                or relay_model.lifecycle != "published_route"
                or not relay_model.customer_callable
            ):
                raise ConflictError("Relay 实时目录与本地视频联调计划不一致")
            route_item = cls._route_item(
                model=model,
                plan=plan,
                evidence=release_evidence,
            )
            if route_item is None or route_item.status != "ready":
                raise ConflictError("本地视频联调路由尚未完成真实且新鲜的验收")
            if (
                relay_model.published_route_revision
                != route_item.published_route_revision
                or not isinstance(plan.approved_route_identity, Mapping)
                or plan.approved_route_identity.get(
                    "published_route_revision"
                )
                != route_item.published_route_revision
            ):
                raise ConflictError("本地视频联调逐模型路由发布版本已漂移")
            route_identity, route_identity_sha256 = (
                commercial_route_release_snapshot(route_item)
            )
            if (
                plan.approved_route_identity is None
                or plan.approved_route_identity != route_identity
                or plan.approved_route_identity_sha256 is None
                or not hmac.compare_digest(
                    plan.approved_route_identity_sha256,
                    route_identity_sha256,
                )
            ):
                raise ConflictError("本地视频联调供应商账号或路由身份已漂移")
            provider_cost_state = (
                RelayCapabilityService.provider_cost_evidence_state(route_item)
            )
            if provider_cost_state["provider_cost_status"] != "ready":
                raise ConflictError("本地视频联调供应商成本证据未就绪")
            if not cls._route_cost_matches_plan(
                plan=plan,
                route_item=route_item,
            ):
                raise ConflictError("本地视频联调供应商成本证据与批准计划不一致")
            release_rows.append((plan, execution, model, route_item))

        company_policy = {
            "call_quota": int(budget["call_quota"]),
            "concurrency_limit": int(budget["concurrency_limit"]),
        }
        personal_policy = dict(company_policy)
        for plan, execution, _model, route_item in release_rows:
            execution.attempt_count += 1
            cls._release_one(
                session,
                plan=plan,
                execution=execution,
                catalog=catalog,
                route_item=route_item,
                request_id=request_id,
                trigger="local_video_lab_exact_activation",
                companies=companies,
                company_policy=company_policy,
                personal_policy=personal_policy,
            )

        test_points = int(budget["test_points"])
        _company_wallet, company_entry, company_created = (
            CompanyPointBillingService.credit(
                session,
                company_id=company_id,
                amount_points=test_points,
                source_kind=PointLotSourceKind.PROMOTIONAL,
                cash_basis_cents=0,
                receivable_basis_cents=0,
                subsidy_cents=test_points * POINT_VALUE_CENTS,
                idempotency_key=company_credit_key,
                note=(
                    "local_video_lab_test_only:"
                    f"{intent['provider_mode']}:{lab_id}; non-cash, not a purchase"
                ),
            )
        )
        if not company_created:
            raise ConflictError(
                "本地视频联调企业测试积分不是由当前原子激活事务创建"
            )
        _personal_wallet, personal_entry, personal_created = (
            PersonalWalletService.credit(
                session,
                workspace_id=personal_workspace_id,
                amount_points=test_points,
                idempotency_key=personal_credit_key,
                note=(
                    "local_video_lab_personal_test_only:"
                    f"{intent['provider_mode']}:{lab_id}; non-cash, not a purchase"
                ),
            )
        )
        if not personal_created:
            raise ConflictError(
                "本地视频联调个人测试积分不是由当前原子激活事务创建"
            )

        activated_at = utcnow().astimezone(timezone.utc).isoformat()
        plan_receipts = []
        for plan, execution, model, _route_item in release_rows:
            company_grant = session.scalar(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.company_id == company_id,
                    CompanyModelGrant.model_id == model.id,
                )
            )
            personal_grant = session.scalar(
                select(PersonalRetailModelGrant).where(
                    PersonalRetailModelGrant.model_id == model.id,
                )
            )
            if (
                company_grant is None
                or personal_grant is None
                or execution.publication_receipt is None
            ):
                raise ConflictError("本地视频联调最终授权回执不完整")
            plan_receipts.append({
                "plan_id": plan.id,
                "plan_content_sha256": plan.content_sha256,
                "model_id": model.id,
                "model_slug": model.slug,
                "capability_revision": model.relay_capability_revision,
                "route_identity_sha256": (
                    execution.released_route_identity_sha256
                ),
                "provider_cost_evidence_sha256": (
                    plan.provider_cost_evidence_sha256
                ),
                "publication_receipt_sha256": (
                    execution.publication_receipt_sha256
                ),
                "personal_grant_id": execution.personal_grant_id,
                "company_grant_id": company_grant.id,
                "company_call_quota": company_grant.call_quota,
                "company_concurrency_limit": company_grant.concurrency_limit,
                "personal_call_quota": personal_grant.call_quota,
                "personal_concurrency_limit": personal_grant.concurrency_limit,
            })
        plan_receipts.sort(key=lambda item: (item["model_slug"], item["plan_id"]))
        receipt = {
            "schema_version": 1,
            "kind": "local_video_lab_exact_commercial_activation_receipt",
            "lab_id": lab_id,
            "manifest_sha256": intent["manifest_sha256"],
            "test_data_only": True,
            "cash_basis_cents": 0,
            "company_scope": company_scope,
            "personal_scope": personal_scope,
            "budget": budget,
            "catalog_revision": catalog.catalog_revision,
            "plans": plan_receipts,
            "company_point_ledger_entry_id": company_entry.id,
            "personal_point_ledger_entry_id": personal_entry.id,
            "activated_at": activated_at,
        }
        intent_sha256 = canonical_sha256(intent)
        receipt_sha256 = canonical_sha256(receipt)
        audit = AuditService.append(
            session,
            actor_kind="system",
            actor_key="local-video-lab",
            action=LOCAL_VIDEO_LAB_ACTIVATION_ACTION,
            target_type="local_video_lab_activation",
            target_id=lab_id,
            before_summary={},
            after_summary={
                "intent": dict(intent),
                "intent_sha256": intent_sha256,
                "receipt": receipt,
                "receipt_sha256": receipt_sha256,
            },
            request_id=request_id,
        )
        session.flush()
        return {
            "activation_audit_id": audit.id,
            "intent_sha256": intent_sha256,
            "receipt_sha256": receipt_sha256,
            "receipt": receipt,
        }

    @classmethod
    def reconcile(
        cls,
        session: Session,
        *,
        catalog: RelayModelCatalog,
        release_evidence: RelayModelReleaseEvidence,
        request_id: str,
        trigger: str,
        locked_companies: tuple[Company, ...] | None = None,
    ) -> CommercialReleaseReconcileResult:
        validate_model_catalog_release_evidence_pair(
            catalog=catalog,
            evidence=release_evidence,
        )
        if cls._local_video_lab_marker(session) is not None:
            raise ConflictError(
                "本地视频联调商业计划只能通过 exact activation 原子激活"
            )
        companies = (
            cls.lock_distribution_companies(session)
            if locked_companies is None else locked_companies
        )
        # Lock models in one deterministic order before any execution lock.
        # In particular never apply FOR UPDATE to the plan/model JOIN below.
        # Batch-owned plans are excluded: they release only through
        # activate_release_batch(), and letting this path consume one would
        # break the batch's all-or-none guarantee.
        list(session.scalars(
            select(ModelDefinition)
            .where(
                ModelDefinition.id.in_(
                    select(ModelCommercialReleasePlan.model_id).where(
                        ModelCommercialReleasePlan.batch_id.is_(None)
                    )
                )
            )
            .order_by(ModelDefinition.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all())
        rows = session.execute(
            select(
                ModelCommercialReleasePlan,
                ModelCommercialReleaseExecution,
                ModelDefinition,
            )
            .join(
                ModelCommercialReleaseExecution,
                ModelCommercialReleaseExecution.plan_id
                == ModelCommercialReleasePlan.id,
            )
            .join(ModelDefinition, ModelDefinition.id == ModelCommercialReleasePlan.model_id)
            .where(ModelCommercialReleasePlan.batch_id.is_(None))
            .order_by(ModelCommercialReleasePlan.created_at, ModelCommercialReleasePlan.id)
            .with_for_update(of=ModelCommercialReleaseExecution)
            .execution_options(populate_existing=True)
        ).all()
        released_count = 0
        blocked_count = 0
        unchanged_count = 0
        for plan, execution, model in rows:
            if execution.state == "superseded":
                unchanged_count += 1
                continue
            if execution.state == "blocked" and execution.last_blocker_code in {
                "candidate_drift",
                "live_catalog_drift",
                "published_route_revision_drift",
                "provider_route_identity_drift",
            }:
                # Semantic model/route drift requires an explicit successor
                # approval. A later Relay snapshot must not auto-reactivate an
                # invalidated commercial decision.
                unchanged_count += 1
                continue
            was_released = execution.state == "released"
            if not was_released:
                execution.attempt_count += 1
            if (
                model.capability_version != plan.capability_version
                or model.relay_capability_candidate_revision
                != plan.candidate_revision
            ):
                cls._block(
                    execution,
                    code="candidate_drift",
                    message="Relay 候选或 Platform 能力版本已变化，需要新的商业审批计划",
                )
                blocked_count += 1
                continue
            relay_model = next(
                (item for item in catalog.data if item.id == model.slug), None
            )
            if (
                relay_model is None
                or relay_model.capability_revision != plan.candidate_revision
                or relay_model.capabilities.contract_dump()
                != model.relay_capability_candidate
                or relay_model.lifecycle != "published_route"
                or not relay_model.customer_callable
            ):
                cls._block(
                    execution,
                    code="live_catalog_drift",
                    message="Relay 实时目录与已批准商业计划不一致",
                )
                blocked_count += 1
                continue
            route_item = cls._route_item(
                model=model,
                plan=plan,
                evidence=release_evidence,
            )
            if route_item is None or route_item.status != "ready":
                cls._block(
                    execution,
                    code="route_acceptance_pending",
                    message="当前 Relay 路由尚未完成真实、完整且新鲜的验收",
                )
                blocked_count += 1
                continue
            if (
                relay_model.published_route_revision
                != route_item.published_route_revision
                or not isinstance(plan.approved_route_identity, Mapping)
                or plan.approved_route_identity.get(
                    "published_route_revision"
                )
                != route_item.published_route_revision
            ):
                cls._block(
                    execution,
                    code="published_route_revision_drift",
                    message="Relay 逐模型路由发布版本已变化，需要重新审批",
                )
                blocked_count += 1
                continue
            _, route_identity_sha256 = commercial_route_release_snapshot(
                route_item
            )
            if (
                plan.approved_route_identity_sha256 is None
                or not hmac.compare_digest(
                    route_identity_sha256,
                    plan.approved_route_identity_sha256,
                )
            ):
                cls._block(
                    execution,
                    code="provider_route_identity_drift",
                    message="供应商账号、密钥版本或 Relay 路由发布已变化，需要重新审批",
                )
                blocked_count += 1
                continue
            provider_cost_state = (
                RelayCapabilityService.provider_cost_evidence_state(route_item)
            )
            if provider_cost_state["provider_cost_status"] != "ready":
                cls._block(
                    execution,
                    code="provider_cost_unready",
                    message="；".join(
                        provider_cost_state["provider_cost_blockers"]
                    )[:500],
                )
                blocked_count += 1
                continue
            if not cls._route_cost_matches_plan(
                plan=plan,
                route_item=route_item,
            ):
                cls._block(
                    execution,
                    code="provider_cost_plan_mismatch",
                    message=(
                        "Relay 成本路由使用的价格证据或计费单元与管理员批准的"
                        "不可变商业计划不一致"
                    ),
                )
                blocked_count += 1
                continue
            if was_released:
                # A released plan is continuously revalidated, but an
                # unchanged exact model-local route identity is a no-op.
                unchanged_count += 1
                continue
            try:
                with session.begin_nested():
                    cls._release_one(
                        session,
                        plan=plan,
                        execution=execution,
                        catalog=catalog,
                        route_item=route_item,
                        request_id=request_id,
                        trigger=trigger,
                        companies=companies,
                    )
            except (ConflictError, NotFoundError) as exc:
                execution = session.get(
                    ModelCommercialReleaseExecution, execution.id
                )
                assert execution is not None
                cls._block(
                    execution,
                    code="release_invariant_failed",
                    message=str(exc)[:500],
                )
                blocked_count += 1
            else:
                released_count += 1
        session.flush()
        items = tuple(cls.list_plans(session))
        return CommercialReleaseReconcileResult(
            catalog_revision=catalog.catalog_revision,
            planned_count=len(rows),
            released_count=released_count,
            blocked_count=blocked_count,
            unchanged_count=unchanged_count,
            items=items,
        )

    # ------------------------------------------------------------------
    # Commercial release batches
    #
    # A batch groups several immutable per-model plans so one transaction can
    # release them together.  It adds no new approval authority: every plan is
    # still created by approve_plan() and every release still runs through
    # _release_one().  What the batch adds is the all-or-none commit boundary.
    # ------------------------------------------------------------------

    @staticmethod
    def _route_evidence_item(
        *,
        model: ModelDefinition,
        evidence: RelayModelReleaseEvidence,
    ) -> RelayModelReleaseEvidenceItem | None:
        """Route evidence for a model that has no plan yet (preflight only)."""

        item = next(
            (row for row in evidence.models if row.public_model_id == model.slug),
            None,
        )
        if (
            item is None
            or model.relay_capability_candidate_revision is None
            or item.capability_revision != model.relay_capability_candidate_revision
        ):
            return None
        return item

    @classmethod
    def _open_batch_plan_id(cls, session: Session, *, model_id: str) -> str | None:
        """A plan already bound to a batch that has not released yet."""

        return session.scalar(
            select(ModelCommercialReleasePlan.id)
            .join(
                ModelCommercialReleaseBatch,
                ModelCommercialReleaseBatch.id
                == ModelCommercialReleasePlan.batch_id,
            )
            .where(
                ModelCommercialReleasePlan.model_id == model_id,
                ModelCommercialReleaseBatch.state != "released",
            )
            .limit(1)
        )

    @classmethod
    def _preflight_model(
        cls,
        session: Session,
        *,
        model_id: str,
        catalog: RelayModelCatalog,
        release_evidence: RelayModelReleaseEvidence,
    ) -> dict[str, Any]:
        model = session.get(ModelDefinition, model_id)
        if model is None:
            return {
                "model_id": model_id,
                "model_slug": None,
                "display_name": None,
                "provider_key": None,
                "capability": None,
                "route_evidence": None,
                "provider_cost": None,
                "already_published": False,
                "blockers": ["model_not_found"],
            }

        blockers: list[str] = []
        if model.relay_capability_candidate_revision is None:
            blockers.append("candidate_missing")

        relay_model = next(
            (item for item in catalog.data if item.id == model.slug), None
        )
        if (
            relay_model is None
            or relay_model.lifecycle != "published_route"
            or not relay_model.customer_callable
        ):
            blockers.append("model_not_customer_callable")
        elif (
            relay_model.capability_revision
            != model.relay_capability_candidate_revision
            or relay_model.capabilities.contract_dump()
            != model.relay_capability_candidate
        ):
            blockers.append("live_catalog_drift")

        route_item = cls._route_evidence_item(model=model, evidence=release_evidence)
        route_state: dict[str, Any] | None = None
        if route_item is None:
            blockers.append("route_not_ready")
        else:
            route_state = {
                "status": str(getattr(route_item, "status", "") or "unknown"),
                "published_route_revision": getattr(
                    route_item, "published_route_revision", None
                ),
                "routing_release_sha256": getattr(
                    route_item, "routing_release_sha256", None
                ),
                "route_count": len(route_item.routes),
            }
            if route_state["status"] != "ready":
                blockers.append("route_not_ready")

        cost_state: dict[str, Any] | None = None
        if route_item is not None:
            raw = RelayCapabilityService.provider_cost_evidence_state(route_item)
            cost_state = {
                "status": raw["provider_cost_status"],
                "ready_rectangle_count": raw["provider_cost_ready_rectangle_count"],
                "rectangle_count": raw["provider_cost_rectangle_count"],
                "blocker_codes": list(raw["provider_cost_blocker_codes"]),
            }
            if raw["provider_cost_status"] != "ready":
                blockers.append("provider_cost_incomplete")

        if cls._open_batch_plan_id(session, model_id=model.id) is not None:
            blockers.append("already_in_open_batch")

        return {
            "model_id": model.id,
            "model_slug": model.slug,
            "display_name": model.display_name,
            "provider_key": model.provider_key,
            "capability": {
                "capability_version": model.capability_version,
                "candidate_revision": model.relay_capability_candidate_revision,
                "candidate_catalog_revision": (
                    model.relay_capability_candidate_catalog_revision
                ),
                "approved_revision": model.relay_capability_revision,
            },
            "route_evidence": route_state,
            "provider_cost": cost_state,
            "already_published": model.published_at is not None,
            "blockers": blockers,
        }

    @classmethod
    def preflight_batch(
        cls,
        session: Session,
        *,
        model_ids: Sequence[str],
        relay_snapshot: "RelaySnapshotReader",
    ) -> dict[str, Any]:
        """Report, per model, exactly what still blocks a batch release.

        Strictly read-only.  This exists so an operator can see the whole set
        at once instead of discovering blockers one model at a time.  It
        decides nothing: every check is repeated inside the activation
        transaction against the then-current Relay snapshot.
        """

        if not model_ids:
            raise ConflictError("批量模型清单不能为空")
        if len(set(model_ids)) != len(model_ids):
            raise ConflictError("批量模型清单包含重复项")
        catalog, release_evidence = relay_snapshot()
        validate_model_catalog_release_evidence_pair(
            catalog=catalog,
            evidence=release_evidence,
        )

        items = [
            cls._preflight_model(
                session,
                model_id=model_id,
                catalog=catalog,
                release_evidence=release_evidence,
            )
            for model_id in model_ids
        ]
        blocked = [item for item in items if item["blockers"]]
        return {
            "catalog_revision": catalog.catalog_revision,
            "model_count": len(items),
            "releasable_count": len(items) - len(blocked),
            "blocked_count": len(blocked),
            "items": items,
        }

    @classmethod
    def list_batches(cls, session: Session) -> list[dict[str, Any]]:
        rows = session.execute(
            select(ModelCommercialReleaseBatch)
            .order_by(
                ModelCommercialReleaseBatch.created_at,
                ModelCommercialReleaseBatch.id,
            )
        ).scalars().all()
        counts: dict[str, int] = {}
        for batch_id, count in session.execute(
            select(
                ModelCommercialReleasePlan.batch_id,
                func.count(ModelCommercialReleasePlan.id),
            )
            .where(ModelCommercialReleasePlan.batch_id.is_not(None))
            .group_by(ModelCommercialReleasePlan.batch_id)
        ).all():
            counts[str(batch_id)] = int(count)
        return [
            cls._batch_response(batch, plan_count=counts.get(batch.id, 0))
            for batch in rows
        ]

    @staticmethod
    def _batch_response(
        batch: ModelCommercialReleaseBatch,
        *,
        plan_count: int,
    ) -> dict[str, Any]:
        return {
            "batch_id": batch.id,
            "idempotency_key": batch.idempotency_key,
            "state": batch.state,
            "model_count": batch.model_count,
            "plan_count": plan_count,
            "catalog_revision": batch.catalog_revision,
            "approved_by_user_id": batch.approved_by_user_id,
            "approved_at": batch.approved_at,
            "activated_by_user_id": batch.activated_by_user_id,
            "released_at": batch.released_at,
            "attempt_count": batch.attempt_count,
            "last_failure_code": batch.last_failure_code,
            "last_failure_summary": batch.last_failure_summary,
            "result_payload": batch.result_payload,
            "created_at": batch.created_at,
            "updated_at": batch.updated_at,
        }

    @classmethod
    def create_batch(
        cls,
        session: Session,
        *,
        idempotency_key: str,
        items: Sequence[Mapping[str, Any]],
        relay_snapshot: "RelaySnapshotReader",
        approved_by_user_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Create one batch row plus one immutable plan per model.

        The batch row is inserted first because ``batch_id`` is bound at plan
        INSERT time and the plan table rejects UPDATE.
        """

        # Cheap request-shape checks first: a duplicate or malformed batch must
        # not require a Relay read to be rejected.
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            raise ConflictError("批量发布必须提供幂等键")
        if not items:
            raise ConflictError("批量发布必须至少包含一个模型")
        model_ids = [str(item["model_id"]) for item in items]
        if len(set(model_ids)) != len(model_ids):
            raise ConflictError("批量发布模型清单包含重复项")

        existing = session.scalar(
            select(ModelCommercialReleaseBatch).where(
                ModelCommercialReleaseBatch.idempotency_key == normalized_key
            )
        )
        if existing is not None:
            return cls._batch_response(
                existing,
                plan_count=len(cls._batch_plans(session, batch_id=existing.id)),
            )

        # One model may belong to at most one open batch.  Checked before the
        # Relay read so a conflicting request fails without touching Relay.
        for model_id in model_ids:
            open_plan_id = cls._open_batch_plan_id(session, model_id=model_id)
            if open_plan_id is not None:
                raise ConflictError(
                    "模型已属于另一个尚未释放的商业发布批量，请先激活或放弃该批量"
                )

        catalog, release_evidence = relay_snapshot()
        validate_model_catalog_release_evidence_pair(
            catalog=catalog,
            evidence=release_evidence,
        )
        # Serialize against a concurrent create for the same model set.
        cls.lock_distribution_companies(session)

        request_sha256 = _canonical_sha256(
            {
                "idempotency_key": normalized_key,
                "catalog_revision": catalog.catalog_revision,
                "model_ids": sorted(model_ids),
            }
        )
        batch = ModelCommercialReleaseBatch(
            id=new_id(),
            idempotency_key=normalized_key,
            state="approved",
            model_count=len(model_ids),
            request_sha256=request_sha256,
            catalog_revision=catalog.catalog_revision,
            approved_by_user_id=approved_by_user_id,
            approved_at=utcnow(),
            attempt_count=0,
            last_failure_code="",
        )
        session.add(batch)
        try:
            # The plan rows carry the batch FK, so the parent must exist first.
            session.flush()
        except IntegrityError as exc:
            raise ConflictError("商业发布批量已被并发创建，请刷新后重试") from exc

        plans: list[ModelCommercialReleasePlan] = []
        for item in items:
            model = session.get(ModelDefinition, str(item["model_id"]))
            if model is None:
                raise NotFoundError("模型不存在")
            _, _created = cls.approve_plan(
                session,
                model_id=model.id,
                expected_capability_version=int(item["expected_capability_version"]),
                expected_candidate_revision=str(item["expected_candidate_revision"]),
                expected_catalog_revision=str(item["expected_catalog_revision"]),
                expected_routing_release_sha256=str(
                    item["expected_routing_release_sha256"]
                ),
                provider_cost_currency=str(item["provider_cost_currency"]),
                provider_cost_formula=dict(item["provider_cost_formula"]),
                provider_cost_evidence_kind=str(item["provider_cost_evidence_kind"]),
                provider_cost_evidence_reference=str(
                    item["provider_cost_evidence_reference"]
                ),
                provider_cost_evidence_sha256=str(
                    item["provider_cost_evidence_sha256"]
                ),
                provider_cost_effective_at=item["provider_cost_effective_at"],
                fx_cny_micros_per_currency_unit=int(
                    item["fx_cny_micros_per_currency_unit"]
                ),
                fx_source=str(item["fx_source"]),
                fx_version=str(item["fx_version"]),
                fx_evidence_sha256=str(item["fx_evidence_sha256"]),
                fx_effective_at=item["fx_effective_at"],
                personal_price_points=item.get("personal_price_points"),
                enterprise_price_points=item.get("enterprise_price_points"),
                personal_config_override=dict(item.get("personal_config_override") or {}),
                enterprise_config_override=dict(
                    item.get("enterprise_config_override") or {}
                ),
                approval_reason=str(item.get("approval_reason") or "批量发布"),
                # One plan per model inside the batch, so the plan idempotency
                # key must be derived from both.
                idempotency_key=f"{normalized_key}:{model.slug}",
                approved_by_user_id=approved_by_user_id,
                request_id=request_id,
                release_evidence=release_evidence,
                batch_id=batch.id,
            )
        plans = cls._batch_plans(session, batch_id=batch.id)
        if len(plans) != batch.model_count:
            raise ConflictError("批量商业计划集合与批量记录不一致")

        AuditService.append(
            session,
            actor_user_id=approved_by_user_id,
            action="model.commercial_release.batch.approve",
            target_type="model_commercial_release_batch",
            target_id=batch.id,
            before_summary=None,
            after_summary=_audit_safe(
                {
                    "batch_id": batch.id,
                    "idempotency_key": normalized_key,
                    "model_count": batch.model_count,
                    "model_ids": [plan.model_id for plan in plans],
                    "plan_ids": [plan.id for plan in plans],
                    "catalog_revision": batch.catalog_revision,
                    "request_sha256": batch.request_sha256,
                    "trigger": "platform_admin_request",
                }
            ),
            request_id=request_id,
        )
        session.flush()
        return cls._batch_response(batch, plan_count=len(plans))

    @staticmethod
    def _batch_plans(
        session: Session,
        *,
        batch_id: str,
    ) -> list[ModelCommercialReleasePlan]:
        return list(
            session.scalars(
                select(ModelCommercialReleasePlan)
                .where(ModelCommercialReleasePlan.batch_id == batch_id)
                .order_by(ModelCommercialReleasePlan.model_id)
            ).all()
        )

    @classmethod
    def abandon_batch(
        cls,
        session: Session,
        *,
        batch_id: str,
        abandoned_by_user_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        batch = session.scalar(
            select(ModelCommercialReleaseBatch)
            .where(ModelCommercialReleaseBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise NotFoundError("商业发布批量不存在")
        if batch.state == "released":
            raise ConflictError("已释放的商业发布批量不能放弃")
        if batch.state == "abandoned":
            return cls._batch_response(
                batch, plan_count=len(cls._batch_plans(session, batch_id=batch.id))
            )
        before = cls._batch_response(
            batch, plan_count=len(cls._batch_plans(session, batch_id=batch.id))
        )
        batch.state = "abandoned"
        AuditService.append(
            session,
            actor_user_id=abandoned_by_user_id,
            action="model.commercial_release.batch.abandon",
            target_type="model_commercial_release_batch",
            target_id=batch.id,
            before_summary=_audit_safe(before),
            after_summary=_audit_safe(
                {
                    "batch_id": batch.id,
                    "state": "abandoned",
                    "trigger": "platform_admin_request",
                }
            ),
            request_id=request_id,
        )
        session.flush()
        return cls._batch_response(
            batch, plan_count=len(cls._batch_plans(session, batch_id=batch.id))
        )

    @classmethod
    def activate_release_batch(
        cls,
        session: Session,
        *,
        batch_id: str,
        relay_snapshot: "RelaySnapshotReader",
        activated_by_user_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Release every plan in a batch inside one all-or-none transaction.

        Structurally the same as ``activate_local_lab_exact`` except that the
        serialization point is the batch row rather than a lab marker, and no
        test credit is issued.  Every read-only invariant is checked for every
        model *before* the first release runs, so a blocked batch normally
        fails before it can write anything; anything that does fail later still
        rolls the whole transaction back.
        """

        # Resolve the batch and its state before touching Relay: an unknown or
        # already-abandoned batch must fail without a catalog read.
        batch = session.scalar(
            select(ModelCommercialReleaseBatch)
            .where(ModelCommercialReleaseBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise NotFoundError("商业发布批量不存在")
        if batch.state == "released":
            # Idempotent replay: return the committed receipt unchanged.
            return dict(batch.result_payload or {})
        if batch.state != "approved":
            raise ConflictError("商业发布批量不处于可激活状态")

        catalog, release_evidence = relay_snapshot()
        validate_model_catalog_release_evidence_pair(
            catalog=catalog,
            evidence=release_evidence,
        )
        if cls._local_video_lab_marker(session) is not None:
            raise ConflictError(
                "本地视频联调库必须通过 exact activation 原子激活"
            )

        # Same lock order as the per-plan reconcile path: companies, then
        # models, then executions.
        companies = cls.lock_distribution_companies(session)
        rows = session.execute(
            select(
                ModelCommercialReleasePlan,
                ModelCommercialReleaseExecution,
                ModelDefinition,
            )
            .join(
                ModelCommercialReleaseExecution,
                ModelCommercialReleaseExecution.plan_id
                == ModelCommercialReleasePlan.id,
            )
            .join(
                ModelDefinition,
                ModelDefinition.id == ModelCommercialReleasePlan.model_id,
            )
            .where(ModelCommercialReleasePlan.batch_id == batch.id)
            .order_by(ModelCommercialReleasePlan.model_id)
            .with_for_update(of=ModelCommercialReleaseExecution)
            .execution_options(populate_existing=True)
        ).all()
        if len(rows) != batch.model_count:
            raise ConflictError("批量商业计划集合与批量记录不一致")

        # Every read-only invariant first: no write happens until the whole set
        # is known to be releasable.
        release_rows: list[
            tuple[
                ModelCommercialReleasePlan,
                ModelCommercialReleaseExecution,
                ModelDefinition,
                RelayModelReleaseEvidenceItem,
            ]
        ] = []
        for plan, execution, model in rows:
            if execution.state not in {"approved", "blocked"}:
                raise ConflictError("批量商业计划不处于可激活状态")
            if (
                model.capability_version != plan.capability_version
                or model.relay_capability_candidate_revision
                != plan.candidate_revision
            ):
                raise ConflictError("Relay 候选或 Platform 能力版本已变化，需要新的商业审批计划")
            relay_model = next(
                (item for item in catalog.data if item.id == model.slug), None
            )
            if (
                relay_model is None
                or relay_model.capability_revision != plan.candidate_revision
                or relay_model.capabilities.contract_dump()
                != model.relay_capability_candidate
                or relay_model.lifecycle != "published_route"
                or not relay_model.customer_callable
            ):
                raise ConflictError("Relay 实时目录与已批准商业计划不一致")
            route_item = cls._route_item(
                model=model,
                plan=plan,
                evidence=release_evidence,
            )
            if route_item is None or route_item.status != "ready":
                raise ConflictError("路由尚未完成真实且新鲜的验收")
            route_identity, route_identity_sha256 = (
                commercial_route_release_snapshot(route_item)
            )
            if (
                plan.approved_route_identity is None
                or plan.approved_route_identity_sha256 is None
                or route_identity != plan.approved_route_identity
                or not hmac.compare_digest(
                    route_identity_sha256, plan.approved_route_identity_sha256
                )
            ):
                raise ConflictError("供应商账号或路由身份已漂移")
            if (
                RelayCapabilityService.provider_cost_evidence_state(route_item)[
                    "provider_cost_status"
                ]
                != "ready"
            ):
                raise ConflictError("供应商成本证据未就绪")
            if not cls._route_cost_matches_plan(plan=plan, route_item=route_item):
                raise ConflictError("供应商成本证据与批准计划不一致")
            release_rows.append((plan, execution, model, route_item))

        for plan, execution, _model, route_item in release_rows:
            execution.attempt_count += 1
            cls._release_one(
                session,
                plan=plan,
                execution=execution,
                catalog=catalog,
                route_item=route_item,
                request_id=request_id,
                trigger="platform_admin_release_batch",
                companies=companies,
            )
        session.flush()

        receipts: list[dict[str, Any]] = []
        for plan, execution, model, _route_item in release_rows:
            personal_grant = session.scalar(
                select(PersonalRetailModelGrant).where(
                    PersonalRetailModelGrant.model_id == model.id
                )
            )
            company_grant_ids = list(
                session.scalars(
                    select(CompanyModelGrant.id).where(
                        CompanyModelGrant.model_id == model.id
                    )
                ).all()
            )
            receipts.append(
                {
                    "model_id": model.id,
                    "model_slug": model.slug,
                    "plan_id": plan.id,
                    "execution_state": execution.state,
                    "personal_grant_id": (
                        personal_grant.id if personal_grant is not None else None
                    ),
                    "company_grant_ids": company_grant_ids,
                }
            )

        batch.state = "released"
        batch.activated_by_user_id = activated_by_user_id
        batch.released_at = utcnow()
        batch.attempt_count += 1
        batch.last_failure_code = ""
        batch.last_failure_summary = None
        result = {
            "batch_id": batch.id,
            "state": "released",
            "catalog_revision": catalog.catalog_revision,
            "released_model_ids": [plan.model_id for plan, _, _, _ in release_rows],
            "receipts": receipts,
        }
        batch.result_payload = result
        AuditService.append(
            session,
            actor_user_id=activated_by_user_id,
            action="model.commercial_release.batch.activate",
            target_type="model_commercial_release_batch",
            target_id=batch.id,
            before_summary=None,
            after_summary=_audit_safe(
                {
                    "batch_id": batch.id,
                    "idempotency_key": batch.idempotency_key,
                    "model_count": batch.model_count,
                    "model_ids": result["released_model_ids"],
                    "plan_ids": [plan.id for plan, _, _, _ in release_rows],
                    "catalog_revision": catalog.catalog_revision,
                    "trigger": "platform_admin_request",
                }
            ),
            request_id=request_id,
        )
        session.flush()
        return result

    @classmethod
    def record_batch_activation_failure(
        cls,
        session: Session,
        *,
        batch_id: str,
        failure_code: str,
        failure_summary: Mapping[str, Any] | None,
        request_id: str,
    ) -> None:
        """Persist why an activation failed, outside the rolled-back transaction.

        The activation transaction must leave no partial trace, so this runs in
        its own session afterwards.  Without it an operator would see a batch
        that silently stayed ``approved``.
        """

        batch = session.scalar(
            select(ModelCommercialReleaseBatch)
            .where(ModelCommercialReleaseBatch.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None or batch.state == "released":
            return
        batch.attempt_count += 1
        batch.last_failure_code = str(failure_code)[:64]
        batch.last_failure_summary = (
            _audit_safe(dict(failure_summary)) if failure_summary else None
        )
        AuditService.append(
            session,
            actor_user_id=None,
            actor_kind="system",
            actor_key=_SYSTEM_ACTOR,
            action="model.commercial_release.batch.activation_failed",
            target_type="model_commercial_release_batch",
            target_id=batch.id,
            before_summary=None,
            after_summary=_audit_safe(
                {
                    "batch_id": batch.id,
                    "failure_code": batch.last_failure_code,
                    "failure_summary": batch.last_failure_summary,
                }
            ),
            request_id=request_id,
        )
        session.flush()
