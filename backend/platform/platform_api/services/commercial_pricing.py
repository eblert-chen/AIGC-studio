from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyModelGrant,
    ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan,
    ModelDefinition,
    PersonalRetailModelGrant,
    utcnow,
)
from ..relay_client import RelayModelReleaseEvidence, RelayModelReleaseEvidenceItem
from .errors import ConflictError
from .model_release_guard import require_model_release_snapshot
from .provider_route_identity import canonical_sha256, commercial_route_release_snapshot
from .task_admission import TaskCapabilityAdmission


POINTS_PER_CNY = 10
TARGET_MARGIN_BPS = 3000  # Direct provider-cost gross margin, not net profit.
MICROS_PER_CURRENCY_UNIT = 1_000_000
PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1 = "explicit_image_input_v1"
_LEGACY_PERSONAL_TEXT_MODES = frozenset({"text_to_video", "text_to_image"})
_EXPLICIT_PERSONAL_MODES = _LEGACY_PERSONAL_TEXT_MODES | frozenset(
    {"image_to_image", "image_to_video"}
)
_IMAGE_INPUT_ROLES = frozenset(
    {"reference_image", "first_frame", "last_frame"}
)
_IMAGE_TEMPORAL_CONTROLS = frozenset({"first_frame", "last_frame"})

# Relay cost allocation owns these singular metric identities. Compatibility
# aliases are accepted at the API edge and immediately normalized; immutable
# commercial plans never preserve the older Platform-only vocabulary.
_PROVIDER_COST_COMPONENT_ALIASES = {
    "input_tokens": "input_token",
    "output_tokens": "output_token",
    "total_tokens": "total_token",
    "output_video_seconds": "output_second",
    "input_video_seconds": "input_second",
    "generated_images": "output_item",
    "reference_images": "input_image_above_free",
}
_TOKEN_COST_COMPONENTS = frozenset(
    {
        "input_token",
        "uncached_input_token",
        "cached_input_token",
        "output_token",
        "output_text_token",
        "output_video_token",
        "thought_token",
        "total_token",
    }
)


def _utc(value: datetime) -> datetime:
    # SQLite omits a stored UTC offset; API evidence parsing remains timezone-aware.
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None else value.astimezone(timezone.utc)
    )


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def minimum_price_points(*, provider_cost_micros: int, fx_cny_micros: int) -> tuple[int, int]:
    if any(
        type(value) is not int or value <= 0
        for value in (provider_cost_micros, fx_cny_micros)
    ):
        raise ConflictError("商业价格缺少有效的供应商成本或汇率依据")
    cost_cny_micros = _ceil_div(
        provider_cost_micros * fx_cny_micros, MICROS_PER_CURRENCY_UNIT
    )
    floor = _ceil_div(
        cost_cny_micros * POINTS_PER_CNY * 10_000,
        MICROS_PER_CURRENCY_UNIT * (10_000 - TARGET_MARGIN_BPS),
    )
    return cost_cny_micros, floor


class CommercialPricingPolicy:
    """One price-write rule for all distribution paths.

    The model row must already be locked for writes. This helper acquires no
    company/model/execution locks, so callers retain company -> model -> execution.
    Immutable plans are read under the caller's model lock. Read-only previews
    repeat this rule, and execution must repeat it after acquiring that lock.
    """

    @staticmethod
    def _model_plans(session: Session, *, model_id: str):
        return session.execute(
            select(ModelCommercialReleasePlan, ModelCommercialReleaseExecution)
            .outerjoin(
                ModelCommercialReleaseExecution,
                ModelCommercialReleaseExecution.plan_id == ModelCommercialReleasePlan.id,
            )
            .where(ModelCommercialReleasePlan.model_id == model_id)
            .execution_options(populate_existing=True)
        ).all()

    @staticmethod
    def _current_plan(rows, *, model: ModelDefinition):
        applicable = [
            (plan, execution) for plan, execution in rows
            if plan.candidate_revision == model.relay_capability_revision
            and plan.candidate_revision == model.relay_capability_candidate_revision
            and plan.capability_version == model.capability_version
            and plan.billing_mode == model.billing_mode
        ]
        return max(applicable, key=lambda row: row[0].revision) if applicable else None

    @classmethod
    def require_publication(
        cls,
        session: Session,
        *,
        model: ModelDefinition,
        expected_release_snapshot: Mapping[str, Any] | None,
        publishing_plan_id: str | None,
    ) -> None:
        """Check commercial authority even before the first grant exists.

        Reuse the price-write rule so publication proves the exact current
        plan, route identity, fresh acceptance, provider cost and price floor.
        Only the commercial state machine supplies a publishing_plan_id;
        ordinary publication requires an already released execution.
        """
        current = cls._current_plan(
            cls._model_plans(session, model_id=model.id), model=model
        )
        plan = current[0] if current is not None else None
        cls.require_price(
            session,
            model=model,
            unit_price=plan.enterprise_price_points if plan is not None else 1,
            billing_unit="POINT",
            enabled=True,
            current_unit_price=None,
            config_override=plan.enterprise_config_override if plan is not None else {},
            scope="company",
            expected_release_snapshot=expected_release_snapshot,
            publishing_plan_id=publishing_plan_id,
        )

    @classmethod
    def distribution_blockers(
        cls, session: Session, *, model: ModelDefinition,
        release_evidence: RelayModelReleaseEvidence | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Combine commercial execution with current read-only Relay evidence.

        Customer messages deliberately do not return internal evidence,
        provider account identifiers, or execution error text.
        A historical release is not a heartbeat: without live evidence the
        directory must stay blocked even when the reconciliation worker is down.
        Task admission independently repeats its own live checks.
        """
        rows = cls._model_plans(session, model_id=model.id)
        if not rows and model.relay_capability_revision is None:
            return ()
        if not rows and __import__("os").environ.get("ENVIRONMENT") == "development":
            return ()
        current = cls._current_plan(rows, model=model)
        code = "commercial_release_plan_missing"
        message = "模型商业价格尚未审批，暂不能开始生成。"
        retryable = False
        if rows and current is None:
            code = "commercial_release_candidate_changed"
            message = "模型能力已变化，等待重新审批商业发布。"
        elif current is not None:
            plan, execution = current
            if execution is not None and execution.state == "released":
                return cls._live_distribution_blockers(
                    model=model, plan=plan, evidence=release_evidence
                )
            code = "commercial_release_reconciliation_pending"
            message = "模型商业发布正在核验，完成后才能开始生成。"
            retryable = True
            if execution is not None and execution.state == "blocked":
                reason = execution.last_blocker_code or "blocked"
                messages = {
                    "route_acceptance_pending": "模型连接验收尚未通过，暂不能开始生成。",
                    "provider_cost_unready": "模型成本依据暂不可用，等待平台复核。",
                    "provider_cost_plan_mismatch": "模型成本已变化，等待重新审批价格。",
                    "candidate_drift": "模型能力已变化，等待重新审批商业发布。",
                    "live_catalog_drift": "模型路由已变化，等待重新审批商业发布。",
                    "published_route_revision_drift": "模型路由版本已变化，等待重新审批商业发布。",
                    "provider_route_identity_drift": "模型连接配置已变化，等待重新验收和审批。",
                    "release_invariant_failed": "模型商业发布校验未通过，等待平台处理。",
                }
                code = f"commercial_release_{reason}" if reason in messages else "commercial_release_blocked"
                message = messages.get(reason, "模型商业发布暂不可用，等待平台处理。")
                retryable = reason in {"route_acceptance_pending", "provider_cost_unready"}
            elif execution is not None and execution.state == "superseded":
                code = "commercial_release_superseded"
                message = "模型商业审批已被替代，等待新的发布生效。"
                retryable = False
        return ({
            "code": code,
            "message": message,
            "resource_key": f"model:{model.id}",
            "resource_name": model.display_name,
            "retryable": retryable,
        },)

    @classmethod
    def _live_distribution_blockers(
        cls, *, model: ModelDefinition, plan: ModelCommercialReleasePlan,
        evidence: RelayModelReleaseEvidence | None,
    ) -> tuple[dict[str, Any], ...]:
        def blocked(code: str, message: str, *, retryable: bool = True):
            return ({
                "code": f"commercial_release_{code}",
                "message": message,
                "resource_key": f"model:{model.id}",
                "resource_name": model.display_name,
                "retryable": retryable,
            },)

        if evidence is None:
            return blocked("evidence_unavailable", "模型连接状态暂时无法核验，请稍后重试。")
        now = _utc(utcnow())
        generated_at = _utc(evidence.generated_at)
        # Match the authenticated Relay client's projection-age contract, and
        # recheck after transport so evidence cannot expire during this read.
        if generated_at > now + timedelta(seconds=30) or now - generated_at > timedelta(minutes=2):
            return blocked("evidence_expired", "模型连接验收依据已过期，等待重新核验。")
        item = next((row for row in evidence.models if row.public_model_id == model.slug), None)
        if item is None or item.capability_revision != plan.candidate_revision:
            return blocked("live_catalog_drift", "模型路由已变化，等待重新审批商业发布。", retryable=False)
        max_test_age = timedelta(seconds=evidence.test_freshness_max_age_seconds)
        if any(
            (route.fresh_until is not None and _utc(route.fresh_until) <= now)
            or (route.latest_successful_test_at is not None
                and now - _utc(route.latest_successful_test_at) > max_test_age)
            for route in item.routes
        ):
            return blocked("evidence_expired", "模型连接验收依据已过期，等待重新核验。")
        if item.status != "ready" or not item.routes or any(
            not route.enabled or not route.accepted or not route.fresh
            for route in item.routes
        ):
            return blocked("route_acceptance_pending", "模型连接验收尚未通过，暂不能开始生成。")
        identity, identity_sha = commercial_route_release_snapshot(item)
        if identity != plan.approved_route_identity or identity_sha != plan.approved_route_identity_sha256:
            return blocked("provider_route_identity_drift", "模型连接配置已变化，等待重新验收和审批。", retryable=False)
        if not item.provider_cost_ready:
            return blocked("provider_cost_unready", "模型成本依据暂不可用，等待平台复核。")
        if (
            not cls.route_cost_matches_plan(plan=plan, route_item=item)
            or any(_utc(value) > now for value in (plan.provider_cost_effective_at, plan.fx_effective_at))
            or any(rectangle.effective_from is None or _utc(rectangle.effective_from) > now
                   for route in item.routes for rectangle in route.provider_cost_rectangles)
        ):
            return blocked("provider_cost_plan_mismatch", "模型成本已变化，等待重新审批价格。", retryable=False)
        return ()

    @classmethod
    def project_personal_capabilities(
        cls,
        *,
        capability_map: dict[str, dict[str, Any]],
        config_override: dict[str, Any],
        provider_cost_formula: Mapping[str, Any],
        require_usable: bool = True,
    ) -> dict[str, Any]:
        """Project the immutable commercial policy into a personal ceiling.

        Plans created before the policy marker existed remain text-only. The
        explicit-image policy is opt-in, requires a complete personal override,
        and never admits video/audio/resource-backed modes. Approval and every
        later price/admission check call this same projection.
        """
        assumptions = provider_cost_formula.get("assumptions")
        policy = (
            assumptions.get("personal_media_policy")
            if isinstance(assumptions, Mapping)
            else None
        )
        if policy not in (None, PERSONAL_MEDIA_POLICY_EXPLICIT_IMAGE_V1):
            raise ConflictError("个人素材商业策略标记无效")
        if policy is not None and not config_override:
            raise ConflictError("个人图片输入策略必须绑定显式个人能力范围")

        effective = TaskCapabilityAdmission.effective_capabilities(
            capability_map=capability_map,
            config_override=config_override,
            strict_catalog=True,
            strict_override=bool(config_override),
            require_usable=True,
        )
        modes = effective.get("modes", {})
        if policy is None:
            # Compatibility contract for every historical plan: absence of the
            # immutable marker can never inherit newly available media modes.
            projected = {
                mode: config
                for mode, config in modes.items()
                if mode in _LEGACY_PERSONAL_TEXT_MODES
                and not config.get("input_media_types")
                and not config.get("required_resource_keys")
            }
        else:
            unknown_modes = set(modes) - _EXPLICIT_PERSONAL_MODES
            if unknown_modes:
                raise ConflictError(
                    "个人图片输入策略仅允许文本生成、图片生成和图片生成视频模式"
                )
            projected = dict(modes)
            text_mode_available = False
            for mode, config in projected.items():
                input_media_types = set(config.get("input_media_types", []))
                input_roles = set(config.get("input_roles", []))
                temporal_controls = set(config.get("temporal_controls", []))
                required_resource_keys = set(
                    config.get("required_resource_keys", [])
                )
                conditional_resources = config.get(
                    "conditional_required_resource_keys", {}
                )
                limits = config.get("limits", {})
                if (
                    required_resource_keys
                    or not isinstance(conditional_resources, Mapping)
                    or any(conditional_resources.values())
                    or not isinstance(limits, Mapping)
                    or int(limits.get("max_videos", 0)) != 0
                    or int(limits.get("max_audio", 0)) != 0
                    or input_media_types & {"video", "audio"}
                ):
                    raise ConflictError(
                        "个人图片输入策略禁止视频、音频和资源密钥能力"
                    )
                if mode in _LEGACY_PERSONAL_TEXT_MODES:
                    if (
                        input_media_types
                        or input_roles
                        or temporal_controls
                        or int(limits.get("max_images", 0)) != 0
                    ):
                        raise ConflictError(
                            "个人文本生成模式必须保持无素材输入"
                        )
                    text_mode_available = True
                elif (
                    mode not in {"image_to_image", "image_to_video"}
                    or input_media_types != {"image"}
                    or int(limits.get("max_images", 0)) <= 0
                    or (
                        mode == "image_to_image"
                        and (
                            not input_roles <= {"reference_image"}
                            or temporal_controls
                        )
                    )
                    or (
                        mode == "image_to_video"
                        and (
                            not input_roles <= _IMAGE_INPUT_ROLES
                            or not temporal_controls <= _IMAGE_TEMPORAL_CONTROLS
                        )
                    )
                ):
                    raise ConflictError(
                        "个人图片模式只能使用与模式匹配的明确图片输入能力"
                    )
            if not text_mode_available:
                raise ConflictError(
                    "个人授权必须保留至少一种无需素材的文本生成模式"
                )

        if require_usable and not projected:
            raise ConflictError(
                "个人授权必须保留至少一种无需素材或企业资源的文本生成模式"
            )
        return {**effective, "modes": projected}

    @staticmethod
    def task_needs_live_evidence(session: Session, *, model_id: str) -> bool:
        model = session.get(ModelDefinition, model_id)
        return model is not None and (
            model.relay_capability_revision is not None
            or session.scalar(select(ModelCommercialReleasePlan.id).where(
                ModelCommercialReleasePlan.model_id == model_id,
            ).limit(1)) is not None
        )

    @staticmethod
    def needs_live_evidence(
        session: Session, *, model_id: str, change: Mapping[str, Any], company_id: str | None = None,
    ) -> bool:
        """Select reads before row locks; the locked write rechecks everything."""
        if change.get("enabled"):
            return True
        fields = ("price_per_second_points", "price_per_item_points")
        if company_id is not None:
            fields += ("price_per_second_cents", "price_per_item_cents")
        if all(change.get(field) is None for field in fields):
            return False
        model = session.get(ModelDefinition, model_id)
        if model is None:
            return False  # The normal write/preview raises the scoped not-found.
        if model.relay_capability_revision is None and session.scalar(
            select(ModelCommercialReleasePlan.id).where(ModelCommercialReleasePlan.model_id == model_id).limit(1)
        ) is None:
            return False
        if company_id is not None:
            grant = session.scalar(select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == company_id, CompanyModelGrant.model_id == model_id,
            ))
        else:
            grant = session.scalar(select(PersonalRetailModelGrant).where(PersonalRetailModelGrant.model_id == model_id))
        return grant is None or any(change.get(field) != getattr(grant, field) for field in fields)

    @staticmethod
    def validate_provider_cost_formula(
        raw: dict[str, Any],
        *,
        billing_mode: str,
        candidate_revision: str,
        effective_capabilities: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version",
            "kind",
            "platform_billing_unit",
            "source_capability_revision",
            "assumptions",
            "components",
        }:
            raise ConflictError("供应商成本公式结构无效")
        kind = raw.get("kind")
        allowed_components = {
            "token_total": set(_TOKEN_COST_COMPONENTS),
            "resolution_output_second": {"output_second"},
            "token_total_with_video_input": set(_TOKEN_COST_COMPONENTS)
            | {"input_second"},
            "composite_media_seconds_images": {
                "output_second",
                "input_second",
                "input_image_above_free",
            },
            "output_item": {"output_item"},
        }
        if kind not in allowed_components:
            raise ConflictError("供应商成本公式类型无效")
        allowed_billing_modes = {
            "token_total": {"per_second", "per_item"},
            "token_total_with_video_input": {"per_second", "per_item"},
            "resolution_output_second": {"per_second"},
            "composite_media_seconds_images": {"per_second"},
            "output_item": {"per_item"},
        }
        if (
            raw.get("schema_version") != 1
            or raw.get("platform_billing_unit") != billing_mode
            or billing_mode not in allowed_billing_modes[kind]
            or raw.get("source_capability_revision") != candidate_revision
        ):
            raise ConflictError("供应商成本公式未绑定当前能力与计费单元")
        assumptions = raw.get("assumptions")
        if not isinstance(assumptions, dict) or not assumptions:
            raise ConflictError("供应商成本公式缺少保守上界假设")
        if assumptions.get("quantity_basis") != "relay_effective_capability_ceiling":
            raise ConflictError("供应商成本数量必须来自 Relay 能力硬上限")
        enforced_limits = assumptions.get("enforced_limits")
        if not isinstance(enforced_limits, dict) or not enforced_limits:
            raise ConflictError("供应商成本公式缺少服务端可执行的数量上限")
        mode_limits: list[dict[str, Any]] = []
        for capability in effective_capabilities:
            modes = capability.get("modes")
            if not isinstance(modes, dict) or not modes:
                raise ConflictError("客户授权缺少可验证的有效能力上限")
            current = [
                mode.get("limits")
                for mode in modes.values()
                if isinstance(mode, dict) and isinstance(mode.get("limits"), dict)
            ]
            if len(current) != len(modes):
                raise ConflictError("客户授权能力上限结构无效")
            mode_limits.extend(current)

        known_enforced_limits = {
            "max_total_tokens",
            "max_input_tokens",
            "max_output_tokens",
            "media_resolution",
            "max_input_video_seconds",
            "max_resolution",
            "minimum_output_seconds",
            "max_reference_images",
            "included_reference_images",
            "max_reference_audio_files",
            "included_reference_audio_files",
            "max_output_count",
        }
        if set(enforced_limits) - known_enforced_limits:
            raise ConflictError("供应商成本公式包含未执行的数量上限")
        if kind == "token_total" and not (
            any(
                key in enforced_limits
                for key in (
                    "max_total_tokens",
                    "max_input_tokens",
                    "max_output_tokens",
                )
            )
            and enforced_limits.get("media_resolution")
        ):
            raise ConflictError("Token 计费模型缺少服务端 Token/媒体分辨率上限")
        if kind == "token_total_with_video_input" and not (
            "max_total_tokens" in enforced_limits
            and "max_input_video_seconds" in enforced_limits
            and enforced_limits.get("media_resolution")
        ):
            raise ConflictError(
                "含视频输入的 Token 成本缺少 Token/媒体分辨率/视频时长上限"
            )
        if kind == "resolution_output_second" and not enforced_limits.get(
            "max_resolution"
        ):
            raise ConflictError("分辨率按秒成本缺少最高分辨率上界")
        if kind == "composite_media_seconds_images" and not (
            enforced_limits.get("minimum_output_seconds")
            and "max_input_video_seconds" in enforced_limits
            and "max_reference_images" in enforced_limits
        ):
            raise ConflictError("复合媒体成本缺少最短输出与输入媒体上限")
        if kind == "output_item" and not enforced_limits.get(
            "max_output_count"
        ):
            raise ConflictError("按件成本缺少服务端输出数量上限")

        # The commercial plan may only name limits which are present in the
        # server-computed Relay ceiling. This keeps an administrator estimate
        # from being mistaken for an executable admission bound.
        if kind in {"token_total", "token_total_with_video_input"}:
            required_token_keys = {
                key
                for key in (
                    "max_total_tokens",
                    "max_input_tokens",
                    "max_output_tokens",
                    "media_resolution",
                    "max_input_video_seconds",
                )
                if key in enforced_limits
            }
            # A cost assumption is not an admission limit. Every token/media
            # ceiling must be present on every effective customer capability.
            # Capability v1-v3 do not currently carry such fields, so Omni and
            # token-priced video remain correctly blocked until Relay actually
            # enforces and publishes them.
            if not required_token_keys or any(
                any(limit.get(key) != enforced_limits[key] for limit in mode_limits)
                for key in required_token_keys
            ):
                raise ConflictError(
                    "Relay 当前能力未显式钉住成本公式所需的 Token/媒体分辨率上限"
                )
        resolution_rank = {
            "360p": 360,
            "480p": 480,
            "720p": 720,
            "768p": 768,
            "1080p": 1080,
            "2k": 2000,
            "4k": 4000,
        }
        if kind in {
            "resolution_output_second",
            "composite_media_seconds_images",
        }:
            resolutions = {
                str(value).lower()
                for limit in mode_limits
                for value in limit.get("resolutions", [])
            }
            if not resolutions or any(value not in resolution_rank for value in resolutions):
                raise ConflictError("Relay 分辨率上限无法安全排序")
            actual_max_resolution = max(
                resolutions, key=lambda value: resolution_rank[value]
            )
            if (
                str(enforced_limits.get("max_resolution", "")).lower()
                != actual_max_resolution
            ):
                raise ConflictError("成本公式未使用 Relay 支持的最高分辨率")
        if kind == "resolution_output_second":
            max_videos = max(int(limit.get("max_videos", 0)) for limit in mode_limits)
            max_images = max(int(limit.get("max_images", 0)) for limit in mode_limits)
            max_audio = max(int(limit.get("max_audio", 0)) for limit in mode_limits)
            if max_videos:
                raise ConflictError("仅按输出秒计价的商业能力不得保留可能产生输入秒成本的视频模式")
            if max_images:
                if (
                    enforced_limits.get("max_reference_images") != max_images
                    or enforced_limits.get("included_reference_images", -1)
                    < max_images
                ):
                    raise ConflictError("输入图片未被官方免费额度完整覆盖，不能仅按输出秒定价")
            if max_audio:
                if (
                    enforced_limits.get("max_reference_audio_files") != max_audio
                    or enforced_limits.get("included_reference_audio_files", -1)
                    < max_audio
                ):
                    raise ConflictError("输入音频未被官方免费额度完整覆盖，不能仅按输出秒定价")
        if kind == "composite_media_seconds_images":
            durations = {
                int(value)
                for limit in mode_limits
                for value in limit.get("duration_seconds", [])
            }
            max_images = max(int(limit.get("max_images", 0)) for limit in mode_limits)
            if (
                not durations
                or enforced_limits.get("minimum_output_seconds") != min(durations)
                or enforced_limits.get("max_reference_images") != max_images
            ):
                raise ConflictError("复合成本公式未绑定 Relay 的最短输出与图片上限")
            if any(
                limit.get("max_input_video_seconds")
                != enforced_limits.get("max_input_video_seconds")
                for limit in mode_limits
            ):
                raise ConflictError("Relay 当前能力未显式钉住输入视频秒数上限")
        if kind == "output_item":
            max_output_count = max(
                max(int(value) for value in limit.get("output_counts", []))
                for limit in mode_limits
            )
            if enforced_limits.get("max_output_count") != max_output_count:
                raise ConflictError("按件成本未绑定客户授权的最大输出数量")

        components = raw.get("components")
        if not isinstance(components, list) or not 1 <= len(components) <= 16:
            raise ConflictError("供应商成本公式组件数量无效")
        normalized_components: list[dict[str, int | str]] = []
        seen: set[str] = set()
        for component in components:
            if not isinstance(component, dict) or set(component) != {
                "component",
                "rate_micros",
                "quantity_numerator",
                "quantity_denominator",
            }:
                raise ConflictError("供应商成本公式组件结构无效")
            raw_name = component.get("component")
            name = _PROVIDER_COST_COMPONENT_ALIASES.get(raw_name, raw_name)
            if name not in allowed_components[kind] or name in seen:
                raise ConflictError("供应商成本公式组件重复或不适用于当前类型")
            seen.add(name)
            values = (
                component.get("rate_micros"),
                component.get("quantity_numerator"),
                component.get("quantity_denominator"),
            )
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                or value > 10**15
                for value in values
            ):
                raise ConflictError("供应商成本公式组件数值无效")
            normalized_components.append(
                {
                    "component": name,
                    "rate_micros": values[0],
                    "quantity_numerator": values[1],
                    "quantity_denominator": values[2],
                }
            )
        if kind in {"token_total", "token_total_with_video_input"} and not seen & {
            *_TOKEN_COST_COMPONENTS,
        }:
            raise ConflictError("Token 成本公式缺少 Token 费率组件")
        if kind in {
            "resolution_output_second",
            "composite_media_seconds_images",
        } and "output_second" not in seen:
            raise ConflictError("按秒视频成本公式缺少输出秒费率")
        if kind == "composite_media_seconds_images" and len(seen) < 2:
            raise ConflictError("复合媒体成本必须包含至少一种附加成本")
        if kind == "output_item" and seen != {"output_item"}:
            raise ConflictError("按件成本公式必须精确使用输出件数费率")
        components_by_name = {
            str(item["component"]): item for item in normalized_components
        }
        for exact_unit_component in ("output_second", "output_item"):
            component = components_by_name.get(exact_unit_component)
            if component is not None and (
                component["quantity_numerator"] != 1
                or component["quantity_denominator"] != 1
            ):
                raise ConflictError("输出秒或输出件成本必须按一个 Platform 计费单元归一")
        if kind == "composite_media_seconds_images":
            input_seconds = components_by_name.get("input_second")
            if input_seconds is not None and (
                input_seconds["quantity_numerator"]
                != enforced_limits["max_input_video_seconds"]
                or input_seconds["quantity_denominator"]
                != enforced_limits["minimum_output_seconds"]
            ):
                raise ConflictError("输入视频成本未按最大输入秒数/最短输出秒数保守摊销")
            extra_images = components_by_name.get("input_image_above_free")
            if extra_images is not None:
                included = enforced_limits.get("included_reference_images")
                if not isinstance(included, int) or isinstance(included, bool) or included < 0:
                    raise ConflictError("图片附加成本缺少官方免费图片数量")
                expected_extra = max(
                    enforced_limits["max_reference_images"] - included,
                    0,
                )
                if (
                    expected_extra <= 0
                    or extra_images["quantity_numerator"] != expected_extra
                    or extra_images["quantity_denominator"] != 1
                ):
                    raise ConflictError("图片附加成本未绑定超出免费额度的最大数量")
        return {
            "schema_version": 1,
            "kind": kind,
            "platform_billing_unit": billing_mode,
            "source_capability_revision": candidate_revision,
            "assumptions": assumptions,
            "components": sorted(
                normalized_components, key=lambda item: str(item["component"])
            ),
        }

    @staticmethod
    def route_cost_matches_plan(
        *,
        plan: ModelCommercialReleasePlan,
        route_item: RelayModelReleaseEvidenceItem,
    ) -> bool:
        expected_provider_metric = (
            "output_second" if plan.billing_mode == "per_second" else "output_item"
        )
        rate_component = next(
            (
                component
                for component in plan.provider_cost_formula.get("components", [])
                if component.get("component") == expected_provider_metric
            ),
            None,
        )
        approved_unit_rate_micros = (
            rate_component.get("rate_micros")
            if isinstance(rate_component, dict) else None
        )
        rectangles = [
            rectangle
            for route in route_item.routes
            for rectangle in route.provider_cost_rectangles
        ]
        return bool(rectangles) and all(
            rectangle.ready
            and rectangle.source_document_sha256
            == plan.provider_cost_evidence_sha256
            and (
                # The source document alone says nothing about a rectangle's
                # actual rate, FX or quantities. Relay computes this ceiling
                # only for an exact executable provider contract and hashes
                # its rate set and capability into the current release. Use
                # the CNY ceiling, including ledger rounding, independently
                # during approval, release, grant writes and task admission.
                (
                    rectangle.rate_set_id is not None
                    and rectangle.billing_unit == plan.billing_mode
                    and rectangle.cost_revision_sha256 is not None
                    and rectangle.customer_unit_cost_ceiling_revision_sha256 is not None
                    and type(rectangle.customer_unit_cost_ceiling_cny_micros) is int
                    and type(plan.provider_cost_cny_micros) is int
                    and plan.provider_cost_cny_micros
                    >= rectangle.customer_unit_cost_ceiling_cny_micros
                )
                or (
                    rectangle.contract_rate_id is not None
                    and type(approved_unit_rate_micros) is int
                    and rectangle.billing_unit == expected_provider_metric
                    and rectangle.currency == plan.provider_cost_currency
                    # One cent is 10,000 micros of the same currency. The
                    # commercial formula may conservatively use the highest
                    # covered resolution, but may not understate a legacy
                    # single-component Relay rectangle.
                    and rectangle.unit_amount_cents * 10_000
                    <= approved_unit_rate_micros
                )
            )
            for rectangle in rectangles
        )

    @classmethod
    def require_price(
        cls,
        session: Session,
        *,
        model: ModelDefinition,
        unit_price: int,
        billing_unit: str,
        enabled: bool,
        current_unit_price: int | None,
        config_override: dict[str, Any],
        scope: str,
        expected_release_snapshot: Mapping[str, Any] | None,
        publishing_plan_id: str | None = None,
    ) -> ModelCommercialReleasePlan | None:
        if type(unit_price) is not int or unit_price <= 0:
            raise ConflictError("模型价格必须是正整数")
        # Always permit shutting down an existing grant at its unchanged price.
        # A simultaneous price write does not inherit this stop-only authority.
        if publishing_plan_id is None and not enabled and current_unit_price == unit_price:
            return None

        rows = cls._model_plans(session, model_id=model.id)
        governed = bool(rows) or model.relay_capability_revision is not None or expected_release_snapshot is not None
        if not governed:
            # Existing local/manual models have never entered Relay commercial
            # approval. Their development/legacy modeling contract is unchanged.
            return None
        if not rows:
            raise ConflictError("已批准的 Relay 模型缺少商业价格计划，请先审批供应商成本与积分底价")

        current = cls._current_plan(rows, model=model)
        if current is None:
            raise ConflictError("商业价格计划与当前模型能力已变化，请重新审批成本与价格")
        plan, execution = current
        if execution is None or (
            (publishing_plan_id is None and execution.state != "released")
            or (publishing_plan_id is not None and (
                plan.id != publishing_plan_id or execution.state not in {"approved", "blocked"}
            ))
        ):
            raise ConflictError("当前商业价格计划尚未完成发布或已被替代")
        if billing_unit != "POINT":
            raise ConflictError("商业发布模型只能按已审批积分价格分发，请先迁移企业积分计费")

        # 开发环境快捷路径：跳过 Relay 成本与路由证据检查
        if __import__("os").environ.get("ENVIRONMENT") == "development":
            return plan

        # Disabled drafts cannot be used to store a price which bypasses the
        # active plan, and missing live evidence cannot authorize a price write.
        if expected_release_snapshot is None:
            raise ConflictError("模型计价缺少当前 Relay 成本与路由证据，请刷新后重试")
        require_model_release_snapshot(model=model, expected_snapshot=expected_release_snapshot)
        raw_evidence = expected_release_snapshot.get("route_release_evidence")
        if not isinstance(raw_evidence, Mapping):
            raise ConflictError("模型计价缺少完整供应商成本证据，请刷新后重试")
        try:
            item = RelayModelReleaseEvidenceItem.model_validate_json(json.dumps(dict(raw_evidence)))
        except (ValidationError, TypeError, ValueError) as exc:
            raise ConflictError("模型计价供应商成本证据无效，请刷新后重试") from exc
        snapshot_routes = deepcopy(dict(raw_evidence).get("routes"))
        snapshot_routes.sort(key=lambda row: (str(row.get("route_id", "")), int(row.get("channel_id", 0))))
        if canonical_sha256({"schema_version": 1, "routes": snapshot_routes}) != (
            expected_release_snapshot["route_evidence_identity"].get("route_inventory_sha256")
        ):
            raise ConflictError("模型计价供应商成本证据与路由快照不一致")
        identity, identity_sha = commercial_route_release_snapshot(item)
        now = _utc(utcnow())
        if (
            item.status != "ready"
            or not item.provider_cost_ready
            or not item.routes
            or identity != plan.approved_route_identity
            or identity_sha != plan.approved_route_identity_sha256
            or any(not route.enabled or not route.accepted or not route.fresh
                   or (route.fresh_until is not None and _utc(route.fresh_until) <= now)
                   for route in item.routes)
            or not cls.route_cost_matches_plan(plan=plan, route_item=item)
        ):
            raise ConflictError("供应商成本或路由证据缺失、过期或已变化，请重新验收和审批")
        if any(_utc(value) > now for value in (plan.provider_cost_effective_at, plan.fx_effective_at)):
            raise ConflictError("供应商成本或汇率依据尚未生效")
        if any(rectangle.effective_from is None or _utc(rectangle.effective_from) > now
               for route in item.routes for rectangle in route.provider_cost_rectangles):
            raise ConflictError("供应商成本费率尚未生效")

        if scope == "personal":
            # Compare the actual retail projection against the immutable plan
            # projection below. Applying the policy filter to both sides would
            # silently accept an over-broad grant and then expose its media
            # modes through PersonalModelService.
            from .personal import PersonalModelService

            _, capabilities = PersonalModelService._effective(
                session,
                model=model,
                grant=PersonalRetailModelGrant(
                    model_id=model.id,
                    config_override=config_override,
                ),
                require_usable=True,
            )
            if capabilities.get("schema_version") == 1:
                capabilities = {
                    **capabilities,
                    "modes": {
                        mode: {
                            key: value
                            for key, value in config.items()
                            if key != "conditional_required_resource_keys"
                            or value != {}
                        }
                        for mode, config in capabilities["modes"].items()
                    },
                }
        else:
            capabilities = TaskCapabilityAdmission.effective_capabilities(
                capability_map={"generation": model.relay_capability_approved_ceiling},
                config_override=config_override,
                strict_catalog=True,
                strict_override=bool(config_override),
                require_usable=True,
            )
        approved_capabilities = []
        for approved_scope, approved_override in (
            ("personal", plan.personal_config_override),
            ("company", plan.enterprise_config_override),
        ):
            if approved_scope == "personal":
                ceiling = cls.project_personal_capabilities(
                    capability_map={
                        "generation": model.relay_capability_approved_ceiling
                    },
                    config_override=approved_override,
                    provider_cost_formula=plan.provider_cost_formula,
                    require_usable=True,
                )
            else:
                ceiling = TaskCapabilityAdmission.effective_capabilities(
                    capability_map={
                        "generation": model.relay_capability_approved_ceiling
                    },
                    config_override=approved_override,
                    strict_catalog=True,
                    strict_override=bool(approved_override),
                    require_usable=True,
                )
            approved_capabilities.append(ceiling)
            if approved_scope == scope:
                try:
                    TaskCapabilityAdmission.validate_full_restriction(
                        ceiling=ceiling, candidate=capabilities,
                    )
                except ConflictError as exc:
                    if scope == "personal":
                        raise ConflictError(
                            "个人授权包含商业计划尚未核价的模式或能力，请明确收紧为已核价文本范围；"
                            "不能沿用历史文本价格开放图片或视频输入"
                        ) from exc
                    raise
        formula = cls.validate_provider_cost_formula(
            plan.provider_cost_formula,
            billing_mode=model.billing_mode,
            candidate_revision=plan.candidate_revision,
            # Retaining the approved worst-case ceiling permits a narrower
            # customer configuration without pretending that the price floor
            # itself has fallen. Expanding past that ceiling still fails.
            effective_capabilities=(*approved_capabilities, capabilities),
        )
        cost_micros = sum(_ceil_div(
            component["rate_micros"] * component["quantity_numerator"],
            component["quantity_denominator"],
        ) for component in formula["components"])
        cny_micros, floor = minimum_price_points(
            provider_cost_micros=cost_micros,
            fx_cny_micros=plan.fx_cny_micros_per_currency_unit,
        )
        if (
            plan.points_per_cny != POINTS_PER_CNY
            or plan.target_margin_bps != TARGET_MARGIN_BPS
            or plan.provider_cost_micros != cost_micros
            or plan.provider_cost_cny_micros != cny_micros
            or plan.minimum_price_points != floor
        ):
            raise ConflictError("商业计划的成本、汇率与最低积分价格不一致")
        if unit_price < floor:
            raise ConflictError(f"积分价格不得低于当前商业计划最低价 {floor} 积分（供应商直接成本毛利 30%）")
        return plan
