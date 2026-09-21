from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    CompanyModelGrant,
    GenerationTask,
    ModelCommercialReleasePlan,
    ModelDefinition,
    PersonalRetailModelGrant,
    utcnow,
)
from ..relay_client import (
    RelayModelCatalog,
    RelayModelReleaseEvidence,
    RelayModelReleaseEvidenceItem,
    RelayModelResource,
)
from .errors import ConflictError, NotFoundError
from .google_video_catalog import google_video_draft_spec
from .models import ModelCatalogService
from .task_admission import TaskCapabilityAdmission


_CAPABILITY_AUDIT_ACTIONS = (
    "model.relay_capability.candidate_sync",
    "model.relay_capability.approve",
)

_PLATFORM_MODEL_SLUG_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$"
)
_PLATFORM_MODEL_SLUG_MAX_LENGTH = 80
_RELAY_PROVIDER_KEY = "relay"
_RELAY_CATALOG_CREATION_SOURCES = frozenset(
    {
        "relay_catalog_reconcile",
        "relay_catalog_periodic_reconcile",
        "commercial_release_reconcile",
    }
)
_UNTOUCHED_RELAY_DRAFT_AUDIT_ACTIONS = frozenset(
    {
        "model.create",
        "model.relay_capability.candidate_sync",
    }
)
_VIDEO_MODES = frozenset(
    {"text_to_video", "image_to_video", "video_to_video"}
)


class RelayCapabilityService:
    """Versioned approval boundary between Relay and the customer Platform.

    Relay owns the physical/verified ceiling. Platform owns the sellable
    restriction. Routing releases, key rotation and account-pool changes are
    intentionally absent: only Relay's deterministic capability revision can
    require a new Platform decision.
    """

    @staticmethod
    def auto_draft_provider_key(relay_model: RelayModelResource | None = None) -> str:
        """Return the provider-neutral key used by Relay-discovered drafts.

        A public model id is deliberately independent from physical providers,
        accounts and channels.  The only exception is an exact public identity
        present in the Platform's reviewed Google onboarding manifest.  That is
        an explicit product-catalog decision, not an inference from a route or
        provider-controlled model name. Capabilities still come exclusively
        from the live Relay catalog.
        """

        if relay_model is not None:
            reviewed = google_video_draft_spec(relay_model.id)
            if reviewed is not None:
                return reviewed.provider_key
        return _RELAY_PROVIDER_KEY

    @staticmethod
    def auto_draft_billing_mode(relay_model: RelayModelResource) -> str:
        """Choose a conservative editable billing default from capabilities.

        Image-only models are naturally per artifact.  Video-only models may
        use per-second billing only when every mode has exactly one output,
        which is the invariant enforced by ``ModelCatalogService``.  Mixed
        media or multi-output contracts fall back to per-item; pricing still
        requires an explicit administrator decision before publication.
        """

        modes = relay_model.capabilities.contract_dump().get("modes", {})
        mode_names = frozenset(modes)
        if (
            mode_names
            and mode_names.issubset(_VIDEO_MODES)
            and all(
                mode.get("limits", {}).get("output_counts") == [1]
                for mode in modes.values()
            )
        ):
            return "per_second"
        return "per_item"

    @staticmethod
    def require_customer_callable_model(
        relay_model: RelayModelResource,
    ) -> None:
        if (
            relay_model.lifecycle != "published_route"
            or not relay_model.customer_callable
            or not relay_model.published_route_revision
        ):
            raise ConflictError(
                "Relay 模型仍是已审核候选，尚未发布为客户可调用路由"
            )

    @staticmethod
    def _auto_draft_slug(relay_model: RelayModelResource) -> str:
        public_model_id = relay_model.id
        if (
            len(public_model_id) > _PLATFORM_MODEL_SLUG_MAX_LENGTH
            or _PLATFORM_MODEL_SLUG_PATTERN.fullmatch(public_model_id) is None
        ):
            raise ConflictError(
                "Relay 公共模型标识无法安全映射到 Platform 模型标识："
                f"{public_model_id!r}"
            )
        return public_model_id

    @classmethod
    def _adopt_reviewed_provider_ownership(
        cls,
        session: Session,
        *,
        model: ModelDefinition,
        provider_key: str,
        display_name: str,
        billing_mode: str,
    ) -> dict[str, Any] | None:
        """Adopt an old Relay-created draft into a reviewed provider catalog.

        Some candidates predate the reviewed provider manifest and therefore
        carry the provider-neutral ``relay`` key.  This one-way migration is
        deliberately narrower than a normal model edit: only an untouched,
        unpublished catalog draft with immutable Relay-creation provenance and
        no commercial or execution history may be relabelled.  Anything that
        has ever crossed a publication, grant, pricing-plan or task boundary
        remains a hard conflict.
        """

        if provider_key == _RELAY_PROVIDER_KEY or model.provider_key == provider_key:
            return None
        if model.provider_key != _RELAY_PROVIDER_KEY:
            raise ConflictError(
                "已审计 Google 模型标识与现有 Platform 供应商归属冲突"
            )

        audits = session.scalars(
            select(AuditLog)
            .where(
                AuditLog.target_type == "model_definition",
                AuditLog.target_id == model.id,
            )
            .order_by(AuditLog.created_at, AuditLog.id)
        ).all()
        create_audits = [item for item in audits if item.action == "model.create"]
        relay_created = (
            len(create_audits) == 1
            and create_audits[0].before_summary == {}
            and create_audits[0].after_summary.get("creation_source")
            in _RELAY_CATALOG_CREATION_SOURCES
            and create_audits[0].after_summary.get("provider_key")
            == _RELAY_PROVIDER_KEY
            and all(
                item.action in _UNTOUCHED_RELAY_DRAFT_AUDIT_ACTIONS
                for item in audits
            )
        )
        has_commercial_or_execution_history = any(
            session.scalar(statement) is not None
            for statement in (
                select(CompanyModelGrant.id)
                .where(CompanyModelGrant.model_id == model.id)
                .limit(1),
                select(PersonalRetailModelGrant.id)
                .where(PersonalRetailModelGrant.model_id == model.id)
                .limit(1),
                select(ModelCommercialReleasePlan.id)
                .where(ModelCommercialReleasePlan.model_id == model.id)
                .limit(1),
                select(GenerationTask.id)
                .where(GenerationTask.model_id == model.id)
                .limit(1),
            )
        )
        untouched = (
            relay_created
            and model.capability_version == 1
            and model.display_name == model.slug
            and model.billing_mode == billing_mode
            and model.active is False
            and model.published_at is None
            and model.relay_capability_revision is None
            and model.relay_capability_synced_at is None
            and model.relay_capability_approved_ceiling is None
            and model.relay_capability_approved_catalog_revision is None
            and not has_commercial_or_execution_history
        )
        if not untouched:
            raise ConflictError(
                "已审计 Google 模型标识与现有 Platform 供应商归属冲突"
            )

        before = {
            "display_name": model.display_name,
            "provider_key": model.provider_key,
            "capability_version": model.capability_version,
        }
        model.display_name = display_name
        model.provider_key = provider_key
        # provider/display edits participate in the model's optimistic
        # concurrency revision even though capability content is unchanged.
        model.capability_version += 1
        session.flush()
        return {
            "before": before,
            "after": {
                "display_name": model.display_name,
                "provider_key": model.provider_key,
                "capability_version": model.capability_version,
            },
        }

    @classmethod
    def reconcile_catalog_drafts(
        cls,
        session: Session,
        *,
        catalog: RelayModelCatalog,
    ) -> list[dict[str, Any]]:
        """Materialize and synchronize Relay models without publishing them.

        The operation is transaction-friendly and deterministic.  A nested
        savepoint contains the unique-slug insertion race so another worker
        winning the same draft does not poison the outer reconciliation.  Any
        non-race validation or revision collision still fails the complete
        request and lets the caller roll back every draft and audit event.
        """

        prepared: list[
            tuple[RelayModelResource, str, str, str, dict[str, Any], str]
        ] = []
        for relay_model in sorted(catalog.data, key=lambda item: item.id):
            # Reviewed candidates remain visible in the Relay catalog audit,
            # but only a tested, explicitly published route may cross into the
            # Platform-owned capability/commercialization workflow.
            if not relay_model.customer_callable:
                continue
            cls.require_customer_callable_model(relay_model)
            slug = cls._auto_draft_slug(relay_model)
            candidate = relay_model.capabilities.contract_dump()
            billing_mode = cls.auto_draft_billing_mode(relay_model)
            reviewed_google = google_video_draft_spec(relay_model.id)
            provider_key = cls.auto_draft_provider_key(relay_model)
            display_name = slug
            if reviewed_google is not None:
                if billing_mode != reviewed_google.billing_mode:
                    raise ConflictError(
                        "Google 模型的实时 Relay 能力与已审计草稿计价方式不兼容"
                    )
                display_name = reviewed_google.display_name
            # Validate the complete Platform draft before making any mutation.
            ModelCatalogService._normalized_capabilities(
                [("generation", candidate)], billing_mode=billing_mode
            )
            prepared.append(
                (
                    relay_model,
                    slug,
                    display_name,
                    provider_key,
                    candidate,
                    billing_mode,
                )
            )

        results: list[dict[str, Any]] = []
        for (
            relay_model,
            slug,
            display_name,
            provider_key,
            candidate,
            billing_mode,
        ) in prepared:
            model = session.scalar(
                select(ModelDefinition)
                .where(ModelDefinition.slug == slug)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            created = False
            if model is None:
                try:
                    with session.begin_nested():
                        model, created = ModelCatalogService.create_draft(
                            session,
                            slug=slug,
                            display_name=display_name,
                            provider_key=provider_key,
                            billing_mode=billing_mode,
                            capabilities=[("generation", candidate)],
                        )
                except (IntegrityError, ConflictError):
                    # A concurrent reconciliation may have inserted the same
                    # unique slug after our initial read.  Re-read only that
                    # exact row; unrelated failures remain fatal.
                    model = session.scalar(
                        select(ModelDefinition)
                        .where(ModelDefinition.slug == slug)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if model is None:
                        raise
                    created = False
            # Repeat the reviewed ownership check after the insertion race
            # boundary. A concurrent writer must not win the unique slug and
            # then receive a Google capability candidate under another owner.
            ownership_adoption = cls._adopt_reviewed_provider_ownership(
                session,
                model=model,
                provider_key=provider_key,
                display_name=display_name,
                billing_mode=billing_mode,
            )
            if (
                provider_key != _RELAY_PROVIDER_KEY
                and model.billing_mode != billing_mode
            ):
                raise ConflictError(
                    "已审计 Google 模型与现有 Platform 计价方式冲突"
                )

            before_state, locked_model, changed, compatibility = (
                cls.sync_candidate(
                    session,
                    model_id=model.id,
                    expected_capability_version=model.capability_version,
                    expected_catalog_revision=catalog.catalog_revision,
                    expected_candidate_revision=(
                        relay_model.capability_revision
                    ),
                    catalog=catalog,
                    relay_model=relay_model,
                )
            )
            results.append(
                {
                    "relay_model_id": relay_model.id,
                    "model": locked_model,
                    "created": created,
                    "ownership_adoption": ownership_adoption,
                    "candidate_changed": changed,
                    "compatibility": compatibility,
                    "before_state": before_state,
                    "after_state": cls.candidate_state(locked_model),
                }
            )
        return results

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

    @staticmethod
    def _is_safe_restriction(*, ceiling: dict, candidate: dict) -> bool:
        try:
            TaskCapabilityAdmission.validate_full_restriction(
                ceiling=ceiling,
                candidate=candidate,
            )
        except ConflictError:
            return False
        return True

    @classmethod
    def ensure_within_approved_ceiling(
        cls,
        *,
        approved_ceiling: dict | None,
        platform_capability: dict,
    ) -> None:
        if approved_ceiling is None:
            return
        if not cls._is_safe_restriction(
            ceiling=approved_ceiling,
            candidate=platform_capability,
        ):
            raise ConflictError(
                "平台模型能力超出已批准的 Relay 能力上限，请先同步并批准新版本"
            )

    @staticmethod
    def _json_changes(
        before: Any,
        after: Any,
        *,
        path: str = "$",
    ) -> list[dict[str, Any]]:
        if before == after:
            return []
        if isinstance(before, dict) and isinstance(after, dict):
            changes: list[dict[str, Any]] = []
            for key in sorted(set(before) | set(after)):
                child_path = f"{path}.{key}"
                if key not in before:
                    changes.append(
                        {
                            "path": child_path,
                            "kind": "added",
                            "before": None,
                            "after": after[key],
                        }
                    )
                elif key not in after:
                    changes.append(
                        {
                            "path": child_path,
                            "kind": "removed",
                            "before": before[key],
                            "after": None,
                        }
                    )
                else:
                    changes.extend(
                        RelayCapabilityService._json_changes(
                            before[key], after[key], path=child_path
                        )
                    )
            return changes
        return [
            {
                "path": path,
                "kind": "changed",
                "before": before,
                "after": after,
            }
        ]

    @classmethod
    def capability_diff(
        cls,
        *,
        approved_ceiling: dict | None,
        candidate: dict | None,
    ) -> dict[str, Any]:
        if candidate is None:
            return {
                "classification": "unavailable",
                "changes": cls._json_changes(approved_ceiling or {}, {}),
            }
        if approved_ceiling is None:
            return {
                "classification": "initial",
                "changes": cls._json_changes({}, candidate),
            }
        if approved_ceiling == candidate:
            return {"classification": "unchanged", "changes": []}
        candidate_is_restriction = cls._is_safe_restriction(
            ceiling=approved_ceiling,
            candidate=candidate,
        )
        approved_is_restriction = cls._is_safe_restriction(
            ceiling=candidate,
            candidate=approved_ceiling,
        )
        if candidate_is_restriction and not approved_is_restriction:
            classification = "restriction"
        elif approved_is_restriction and not candidate_is_restriction:
            classification = "expansion"
        else:
            classification = "mixed"
        return {
            "classification": classification,
            "changes": cls._json_changes(approved_ceiling, candidate),
        }

    @staticmethod
    def approval_status(model: ModelDefinition) -> str:
        candidate_revision = model.relay_capability_candidate_revision
        approved_revision = model.relay_capability_revision
        if candidate_revision is None:
            return "unavailable"
        if approved_revision is None:
            return "unapproved"
        if model.relay_capability_approved_ceiling is None:
            return "legacy_approved"
        if candidate_revision == approved_revision:
            return "approved"
        return "pending"

    @classmethod
    def candidate_state(cls, model: ModelDefinition) -> dict[str, Any]:
        diff = cls.capability_diff(
            approved_ceiling=model.relay_capability_approved_ceiling,
            candidate=model.relay_capability_candidate,
        )
        status = cls.approval_status(model)
        return {
            "candidate_revision": model.relay_capability_candidate_revision,
            "candidate_catalog_revision": (
                model.relay_capability_candidate_catalog_revision
            ),
            "candidate_capabilities": model.relay_capability_candidate,
            "candidate_synced_at": ModelCatalogService._as_utc(
                model.relay_capability_candidate_synced_at
            ),
            "approved_revision": model.relay_capability_revision,
            "approved_catalog_revision": (
                model.relay_capability_approved_catalog_revision
            ),
            "approved_ceiling": model.relay_capability_approved_ceiling,
            "approval_status": status,
            "requires_approval": status != "approved",
            "capability_diff": diff,
        }

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
        relay_capability = relay_model.capabilities.contract_dump()
        try:
            TaskCapabilityAdmission.validate_full_restriction(
                ceiling=relay_capability,
                candidate=platform_capability,
            )
        except ConflictError:
            return "unsafe_expansion", platform_capability
        if platform_capability == relay_capability:
            return "identical", platform_capability
        return "compatible_restriction", platform_capability

    @classmethod
    def approval_history(
        cls,
        session: Session,
        *,
        model_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = session.scalars(
            select(AuditLog)
            .where(
                AuditLog.target_type == "model_definition",
                AuditLog.target_id == model_id,
                AuditLog.action.in_(_CAPABILITY_AUDIT_ACTIONS),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
        ).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            decision = row.after_summary.get("relay_capability_decision", {})
            if not isinstance(decision, dict):
                decision = {}
            result.append(
                {
                    "id": row.id,
                    "event_type": (
                        "approval"
                        if row.action == "model.relay_capability.approve"
                        else "candidate_sync"
                    ),
                    "actor_user_id": row.actor_user_id,
                    "actor_kind": row.actor_kind,
                    "actor_key": row.actor_key,
                    "reason": str(decision.get("reason") or ""),
                    "before_revision": decision.get("before_revision"),
                    "after_revision": decision.get("after_revision"),
                    "catalog_revision": decision.get("catalog_revision"),
                    "capability_diff": decision.get("capability_diff")
                    or {"classification": "unchanged", "changes": []},
                    "request_id": row.request_id,
                    "created_at": ModelCatalogService._as_utc(row.created_at),
                }
            )
        return result

    @staticmethod
    def route_evidence_state(
        *,
        public_model_id: str,
        capability_revision: str,
        evidence: RelayModelReleaseEvidence | None,
        evidence_error: str | None = None,
    ) -> dict[str, Any]:
        common = {
            "routing_release_sha256": None,
            "model_release_id": None,
            "model_release_revision": None,
            "published_route_revision": None,
            "route_count": None,
            "enabled_route_count": None,
            "accepted_route_count": None,
            "fresh_test_count": None,
            "provider_cost_readiness_sha256": None,
            "provider_cost_ready": False,
            "provider_cost_rectangle_count": None,
            "provider_cost_ready_rectangle_count": None,
            "latest_successful_test_at": None,
            "routes": [],
            "evidence_generated_at": (
                evidence.generated_at if evidence is not None else None
            ),
            "test_freshness_max_age_seconds": (
                evidence.test_freshness_max_age_seconds
                if evidence is not None
                else None
            ),
        }
        if evidence is None:
            return {
                **common,
                "route_evidence_status": "unavailable",
                "route_evidence_blockers": [
                    evidence_error or "Relay 路由测试证据暂不可用"
                ],
            }
        item = next(
            (
                candidate
                for candidate in evidence.models
                if candidate.public_model_id == public_model_id
            ),
            None,
        )
        if item is None:
            return {
                **common,
                "route_evidence_status": "missing",
                "route_evidence_blockers": ["Relay 尚未生成该模型的路由测试证据"],
            }
        item_fields = {
            "routing_release_sha256": item.routing_release_sha256,
            "model_release_id": item.model_release_id,
            "model_release_revision": item.model_release_revision,
            "published_route_revision": item.published_route_revision,
            "route_count": item.route_count,
            "enabled_route_count": item.enabled_route_count,
            "accepted_route_count": item.accepted_route_count,
            "fresh_test_count": item.fresh_test_count,
            "provider_cost_readiness_sha256": (
                item.provider_cost_readiness_sha256
            ),
            "provider_cost_ready": item.provider_cost_ready,
            "provider_cost_rectangle_count": (
                item.provider_cost_rectangle_count
            ),
            "provider_cost_ready_rectangle_count": (
                item.provider_cost_ready_rectangle_count
            ),
            "latest_successful_test_at": item.latest_successful_test_at,
            "routes": [route.model_dump(mode="json") for route in item.routes],
        }
        if item.capability_revision != capability_revision:
            return {
                **common,
                **item_fields,
                "route_evidence_status": "revision_drift",
                "route_evidence_blockers": ["测试证据绑定的能力版本已漂移"],
            }
        if item.status != "ready":
            blockers: list[str] = []
            if item.enabled_route_count < 1:
                blockers.append("没有已启用的 Relay 路由")
            if item.accepted_route_count != item.route_count:
                blockers.append("仍有路由未完成受控验收")
            if item.fresh_test_count != item.route_count:
                blockers.append("仍有路由缺少新鲜成功测试")
            if not blockers:
                blockers.append("Relay 将当前路由发布标记为不可分发")
            return {
                **common,
                **item_fields,
                "route_evidence_status": "blocked",
                "route_evidence_blockers": blockers,
            }
        return {
            **common,
            **item_fields,
            "route_evidence_status": "ready",
            "route_evidence_blockers": [],
        }

    @classmethod
    def require_route_evidence_ready(
        cls,
        *,
        public_model_id: str,
        capability_revision: str | None,
        evidence: RelayModelReleaseEvidence | None,
        evidence_error: str | None = None,
    ) -> RelayModelReleaseEvidenceItem:
        if capability_revision is None:
            raise ConflictError("模型尚无已批准的 Relay 能力版本")
        state = cls.route_evidence_state(
            public_model_id=public_model_id,
            capability_revision=capability_revision,
            evidence=evidence,
            evidence_error=evidence_error,
        )
        if state["route_evidence_status"] != "ready":
            reason = "；".join(state["route_evidence_blockers"])
            raise ConflictError(f"模型尚未通过当前 Relay 路由发布测试：{reason}")
        assert evidence is not None
        return next(
            item for item in evidence.models if item.public_model_id == public_model_id
        )

    @staticmethod
    def provider_cost_evidence_state(
        item: RelayModelReleaseEvidenceItem,
    ) -> dict[str, Any]:
        """Classify exact provider-cost coverage for customer distribution.

        Route acceptance and provider-cost readiness are intentionally separate:
        an owner may review and approve an unpublished model without inventing a
        supplier rate. Publishing or enabling a customer grant requires every
        advertised route x mode x resolution rectangle to carry an immutable,
        currently materializable Relay contract-rate proof.
        """

        ready = bool(getattr(item, "provider_cost_ready", False))
        required_count = int(
            getattr(item, "provider_cost_rectangle_count", 0) or 0
        )
        ready_count = int(
            getattr(item, "provider_cost_ready_rectangle_count", 0) or 0
        )
        blocker_codes = sorted(
            {
                str(getattr(rectangle, "blocker_code", "") or "")
                for route in item.routes
                for rectangle in getattr(route, "provider_cost_rectangles", [])
                if getattr(rectangle, "blocker_code", None)
            }
        )
        if ready and required_count > 0 and ready_count == required_count:
            return {
                "provider_cost_status": "ready",
                "provider_cost_blocker_codes": [],
                "provider_cost_rectangle_count": required_count,
                "provider_cost_ready_rectangle_count": ready_count,
            }

        messages = {
            "provider_cost_runtime_unavailable": "Relay 供应商成本运行配置不可用",
            "provider_cost_sink_unconfigured": "Relay 尚未配置受控供应商成本入账通道",
            "provider_contract_rate_missing": "仍有路由、模式或清晰度缺少当前供应商合同费率",
            "provider_contract_rate_invalid": "供应商合同费率证据无效或已漂移",
            "provider_cost_rate_set_missing": "仍有路由、模式或清晰度缺少当前多组件供应商费率集",
            "provider_cost_rate_set_invalid": "供应商多组件费率集证据无效或已漂移",
            "provider_usage_cost_materialization_unqualified": (
                "该模型的多组件用量和供应商成本物化尚未完成资格验证"
            ),
        }
        blockers = [
            messages.get(code, "供应商成本覆盖证据不完整")
            for code in blocker_codes
        ]
        if not blockers:
            blockers = ["Relay 尚未提供精确到路由、模式和清晰度的成本覆盖证据"]
        return {
            "provider_cost_status": "blocked",
            "provider_cost_blocker_codes": blocker_codes,
            "provider_cost_blockers": blockers,
            "provider_cost_rectangle_count": required_count,
            "provider_cost_ready_rectangle_count": ready_count,
        }

    @classmethod
    def require_customer_distribution_ready(
        cls,
        *,
        public_model_id: str,
        capability_revision: str | None,
        evidence: RelayModelReleaseEvidence | None,
        evidence_error: str | None = None,
    ) -> RelayModelReleaseEvidenceItem:
        item = cls.require_route_evidence_ready(
            public_model_id=public_model_id,
            capability_revision=capability_revision,
            evidence=evidence,
            evidence_error=evidence_error,
        )
        cost_state = cls.provider_cost_evidence_state(item)
        if cost_state["provider_cost_status"] != "ready":
            reason = "；".join(cost_state["provider_cost_blockers"])
            raise ConflictError(f"模型供应商成本尚未覆盖，不能向客户分发：{reason}")
        return item

    @classmethod
    def audit_catalog(
        cls,
        session: Session,
        *,
        catalog: RelayModelCatalog,
        release_evidence: RelayModelReleaseEvidence | None = None,
        release_evidence_error: str | None = None,
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
            relay_capability = relay_model.capabilities.contract_dump()
            if platform_model is None:
                status = "unmapped"
                platform_capability = None
                approved_revision = None
                approved_ceiling = None
                approval_history: list[dict[str, Any]] = []
            else:
                status, platform_capability = cls.compatibility(
                    session,
                    model=platform_model,
                    relay_model=relay_model,
                )
                approved_revision = platform_model.relay_capability_revision
                approved_ceiling = (
                    platform_model.relay_capability_approved_ceiling
                )
                approval_history = cls.approval_history(
                    session, model_id=platform_model.id, limit=20
                )
            revision_collision = (
                approved_revision == relay_model.capability_revision
                and approved_ceiling is not None
                and approved_ceiling != relay_capability
            )
            if revision_collision:
                status = "revision_collision"
            capability_diff = cls.capability_diff(
                approved_ceiling=approved_ceiling,
                candidate=relay_capability,
            )
            requires_approval = (
                not relay_model.customer_callable
                or approved_revision != relay_model.capability_revision
                or approved_ceiling is None
            )
            items.append(
                {
                    "relay_model_id": relay_model.id,
                    "lifecycle": relay_model.lifecycle,
                    "managed_route": relay_model.managed_route,
                    "customer_callable": relay_model.customer_callable,
                    "published_route_revision": (
                        relay_model.published_route_revision or None
                    ),
                    # Backwards-compatible name for the live Relay candidate.
                    "capability_revision": relay_model.capability_revision,
                    "candidate_revision": relay_model.capability_revision,
                    "capabilities": relay_capability,
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
                    "approved_revision": approved_revision,
                    "platform_capabilities": platform_capability,
                    "requires_approval": requires_approval,
                    "approval_status": (
                        "revision_collision"
                        if revision_collision
                        else (
                            "approved" if not requires_approval else "pending"
                        )
                    ),
                    "capability_diff": capability_diff,
                    "approval_history": approval_history,
                    **{
                        **cls.route_evidence_state(
                            public_model_id=relay_model.id,
                            capability_revision=(
                                relay_model.capability_revision
                            ),
                            evidence=release_evidence,
                            evidence_error=release_evidence_error,
                        ),
                        # The catalog item is the lifecycle authority. A paired
                        # evidence snapshot is validated before this method is
                        # called and must carry the same model-local revision.
                        "published_route_revision": (
                            relay_model.published_route_revision or None
                        ),
                    },
                }
            )
        return {
            "catalog_revision": catalog.catalog_revision,
            "catalog_revision_scope": catalog.catalog_revision_scope,
            "published_route_revision": catalog.published_route_revision,
            "items": items,
            "platform_only_model_ids": [
                model.id
                for model in sorted(
                    platform_models.values(), key=lambda item: item.slug
                )
            ],
            "route_evidence_available": release_evidence is not None,
            "route_evidence_error": release_evidence_error,
        }

    @classmethod
    def sync_candidate(
        cls,
        session: Session,
        *,
        model_id: str,
        expected_capability_version: int,
        expected_catalog_revision: str | None,
        expected_candidate_revision: str | None,
        catalog: RelayModelCatalog,
        relay_model: RelayModelResource,
    ) -> tuple[dict[str, Any], ModelDefinition, bool, str]:
        model = ModelCatalogService.get_model_for_update(
            session, model_id=model_id
        )
        if model.capability_version != expected_capability_version:
            raise ConflictError("模型能力版本已变化，请刷新后重试")
        if model.slug != relay_model.id:
            raise ConflictError("平台模型标识与 Relay 模型标识不一致")
        cls.require_customer_callable_model(relay_model)
        if (
            expected_catalog_revision is not None
            and expected_catalog_revision != catalog.catalog_revision
        ):
            raise ConflictError("Relay 模型目录版本已变化，请刷新后重试")
        if (
            expected_candidate_revision is not None
            and expected_candidate_revision != relay_model.capability_revision
        ):
            raise ConflictError("Relay 模型能力版本已变化，请刷新后重试")
        candidate = relay_model.capabilities.contract_dump()
        if (
            model.relay_capability_revision == relay_model.capability_revision
            and model.relay_capability_approved_ceiling is not None
            and model.relay_capability_approved_ceiling != candidate
        ):
            raise ConflictError(
                "Relay 重用了能力版本但返回了不同能力，已拒绝同步"
            )
        if (
            model.relay_capability_candidate_revision
            == relay_model.capability_revision
            and model.relay_capability_candidate is not None
            and model.relay_capability_candidate != candidate
        ):
            raise ConflictError(
                "Relay 重用了候选能力版本但返回了不同能力，已拒绝同步"
            )
        before = cls.candidate_state(model)
        changed = (
            model.relay_capability_candidate_revision
            != relay_model.capability_revision
            or model.relay_capability_candidate_catalog_revision
            != catalog.catalog_revision
            or model.relay_capability_candidate != candidate
        )
        if changed:
            model.relay_capability_candidate_revision = (
                relay_model.capability_revision
            )
            model.relay_capability_candidate_catalog_revision = (
                catalog.catalog_revision
            )
            model.relay_capability_candidate = candidate
            model.relay_capability_candidate_synced_at = utcnow()
            session.flush()
        compatibility, _ = cls.compatibility(
            session, model=model, relay_model=relay_model
        )
        return before, model, changed, compatibility

    @classmethod
    def approve_candidate(
        cls,
        session: Session,
        *,
        model_id: str,
        expected_capability_version: int,
        expected_catalog_revision: str | None,
        expected_candidate_revision: str | None,
        live_catalog_revision: str,
        live_candidate_revision: str,
        live_candidate: dict[str, Any],
    ) -> tuple[dict, ModelDefinition, bool, str, dict[str, Any]]:
        model = ModelCatalogService.get_model_for_update(
            session, model_id=model_id
        )
        if model.capability_version != expected_capability_version:
            raise ConflictError("模型能力版本已变化，请刷新后重试")
        if model.relay_capability_candidate_revision is None:
            raise ConflictError("请先同步 Relay 模型能力候选版本")
        if (
            expected_catalog_revision is not None
            and expected_catalog_revision
            != model.relay_capability_candidate_catalog_revision
        ):
            raise ConflictError("Relay 模型目录版本已变化，请刷新后重试")
        if (
            expected_candidate_revision is not None
            and expected_candidate_revision
            != model.relay_capability_candidate_revision
        ):
            raise ConflictError("Relay 模型能力版本已变化，请刷新后重试")
        candidate = model.relay_capability_candidate
        if candidate is None:
            raise ConflictError("Relay 模型能力候选内容不可用")
        if model.relay_capability_candidate_revision != live_candidate_revision:
            raise ConflictError("Relay 模型能力版本已变化，请重新同步并审阅候选版本")
        if candidate != live_candidate:
            raise ConflictError(
                "Relay 能力版本对应的实时内容与已审阅候选不一致，"
                "疑似 revision collision，已拒绝批准"
            )
        if (
            model.relay_capability_revision == live_candidate_revision
            and model.relay_capability_approved_ceiling is not None
            and model.relay_capability_approved_ceiling != live_candidate
        ):
            raise ConflictError(
                "Relay 能力版本与既有批准内容冲突，疑似 revision collision，"
                "已拒绝批准"
            )
        platform_capability = cls._platform_capability(session, model)
        if platform_capability is None:
            raise ConflictError("平台模型尚未配置可用的生成能力")
        if not cls._is_safe_restriction(
            ceiling=candidate,
            candidate=platform_capability,
        ):
            raise ConflictError(
                "平台模型能力超出 Relay 当前能力，请先停用并收紧模型配置"
            )
        compatibility = (
            "identical"
            if platform_capability == candidate
            else "compatible_restriction"
        )
        approval_diff = cls.capability_diff(
            approved_ceiling=model.relay_capability_approved_ceiling,
            candidate=candidate,
        )
        before = ModelCatalogService.response(session, model=model)
        changed = (
            model.relay_capability_revision
            != model.relay_capability_candidate_revision
            or model.relay_capability_approved_ceiling != candidate
        )
        if changed:
            model.relay_capability_revision = (
                model.relay_capability_candidate_revision
            )
            model.relay_capability_approved_ceiling = candidate
            model.relay_capability_approved_catalog_revision = (
                model.relay_capability_candidate_catalog_revision
            )
            model.relay_capability_synced_at = utcnow()
            session.flush()
        return before, model, changed, compatibility, approval_diff

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
