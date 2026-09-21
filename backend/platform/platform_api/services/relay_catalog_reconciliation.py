from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    CompanyModelGrant,
    ModelDefinition,
    PersonalRetailModelGrant,
)
from ..relay_client import RelayModelCatalog
from .audit import AuditService
from .models import ModelCatalogService
from .relay_capabilities import RelayCapabilityService


@dataclass(frozen=True)
class ReconciliationAuditActor:
    kind: str
    key: str

    @classmethod
    def user(cls, user_id: str) -> "ReconciliationAuditActor":
        return cls(kind="user", key=user_id)

    @classmethod
    def system(cls, actor_key: str) -> "ReconciliationAuditActor":
        return cls(kind="system", key=actor_key)

    def audit_kwargs(self) -> dict[str, str | None]:
        if self.kind == "user":
            return {
                "actor_user_id": self.key,
                "actor_kind": "user",
                "actor_key": None,
            }
        if self.kind == "system":
            return {
                "actor_user_id": None,
                "actor_kind": "system",
                "actor_key": self.key,
            }
        raise ValueError("Reconciliation audit actor is invalid")


@dataclass(frozen=True)
class RelayCatalogReconciliationResult:
    created_model_ids: tuple[str, ...]
    synced_model_ids: tuple[str, ...]
    invalidated_model_ids: tuple[str, ...]
    unchanged_count: int
    reconciliation_audit_id: str | None

    @property
    def created_count(self) -> int:
        return len(self.created_model_ids)

    @property
    def synced_count(self) -> int:
        return len(self.synced_model_ids)

    @property
    def invalidated_count(self) -> int:
        return len(self.invalidated_model_ids)


class RelayCatalogReconciliationService:
    """Materialize Relay catalog candidates without releasing customer access.

    Both the explicit administrator endpoint and the periodic system worker use
    this one transaction service.  It intentionally owns no approval, publish,
    pricing, company-grant, or personal-retail-grant operation.
    """

    @staticmethod
    def _model_audit_summary(snapshot: dict) -> dict:
        return {
            key: snapshot[key]
            for key in (
                "slug",
                "display_name",
                "provider_key",
                "billing_mode",
                "capability_version",
                "relay_capability_revision",
                "relay_capability_candidate_revision",
                "relay_capability_candidate_catalog_revision",
                "relay_capability_approved_catalog_revision",
                "relay_capability_approval_status",
                "active",
                "status",
                "capabilities",
            )
        }

    @classmethod
    def reconcile(
        cls,
        session: Session,
        *,
        catalog: RelayModelCatalog,
        actor: ReconciliationAuditActor,
        request_id: str,
        source: str,
        trigger: str,
    ) -> RelayCatalogReconciliationResult:
        previous_summary = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "model.relay_catalog.reconcile",
                AuditLog.target_type == "relay_model_catalog",
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        previous_callable_ids = set()
        previous_route_revisions: dict[str, str] = {}
        previous_catalog_revision = None
        if previous_summary is not None:
            previous_catalog_revision = previous_summary.after_summary.get(
                "catalog_revision"
            )
            stored_ids = previous_summary.after_summary.get(
                "customer_callable_model_ids", []
            )
            if isinstance(stored_ids, list) and all(
                isinstance(item, str) for item in stored_ids
            ):
                previous_callable_ids = set(stored_ids)
            stored_revisions = previous_summary.after_summary.get(
                "customer_callable_route_revisions", {}
            )
            if isinstance(stored_revisions, dict) and all(
                isinstance(model_id, str)
                and isinstance(revision, str)
                for model_id, revision in stored_revisions.items()
            ):
                previous_route_revisions = dict(stored_revisions)

        # Candidate metadata is the durable Platform-side ownership marker.
        # Include it so the first snapshot after deploying this reconciliation
        # contract can fail-close an already published Relay-governed model
        # even when no prior summary audit exists yet.
        governed_platform_ids = set(
            session.scalars(
                select(ModelDefinition.slug).where(
                    ModelDefinition.relay_capability_candidate_revision.is_not(
                        None
                    ),
                    ModelDefinition.relay_capability_candidate_catalog_revision.is_not(
                        None
                    ),
                )
            ).all()
        )

        current_callable_ids = {
            item.id for item in catalog.data if item.customer_callable
        }
        current_route_revisions = {
            item.id: item.published_route_revision
            for item in catalog.data
            if item.customer_callable
        }
        reviewed_candidate_ids = {
            item.id for item in catalog.data if not item.customer_callable
        }
        # A Relay catalog slug is not, by itself, Platform ownership evidence.
        # Only revoke a reviewed candidate when this Platform row was already
        # governed by Relay metadata or by the preceding callable snapshot.
        governed_reviewed_candidate_ids = reviewed_candidate_ids & (
            previous_callable_ids | governed_platform_ids
        )
        current_catalog_ids = {item.id for item in catalog.data}
        missing_published_ids = (
            previous_callable_ids | governed_platform_ids
        ) - current_catalog_ids
        route_revision_drift_ids = {
            model_id
            for model_id, revision in current_route_revisions.items()
            if model_id in previous_route_revisions
            and previous_route_revisions[model_id] != revision
        }
        invalidation_reasons = {
            **{
                item: "reviewed_candidate"
                for item in governed_reviewed_candidate_ids
            },
            **{item: "missing" for item in missing_published_ids},
            **{
                item: "published_route_revision_drift"
                for item in route_revision_drift_ids
            },
        }

        reconciliation = RelayCapabilityService.reconcile_catalog_drafts(
            session,
            catalog=catalog,
        )
        created_model_ids: list[str] = []
        synced_model_ids: list[str] = []
        invalidated_model_ids: list[str] = []
        actor_kwargs = actor.audit_kwargs()
        trigger_evidence = {
            "kind": actor.kind,
            "key": actor.key,
            "trigger": trigger,
        }

        if invalidation_reasons:
            invalidated_models = session.scalars(
                select(ModelDefinition)
                .where(ModelDefinition.slug.in_(sorted(invalidation_reasons)))
                .order_by(ModelDefinition.slug)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            for model in invalidated_models:
                before = cls._model_audit_summary(
                    ModelCatalogService.response(session, model=model)
                )
                # This is deliberately one-way. Relay may remove customer
                # callability, but a later route publication must not silently
                # restore Platform commercial approval or customer access.
                was_active = model.active
                disabled_company_grant_ids: list[str] = []
                disabled_personal_grant_ids: list[str] = []
                company_grants = session.scalars(
                    select(CompanyModelGrant)
                    .where(CompanyModelGrant.model_id == model.id)
                    .order_by(CompanyModelGrant.id)
                    .with_for_update()
                ).all()
                personal_grants = session.scalars(
                    select(PersonalRetailModelGrant)
                    .where(PersonalRetailModelGrant.model_id == model.id)
                    .order_by(PersonalRetailModelGrant.id)
                    .with_for_update()
                ).all()
                had_enabled_company = any(
                    grant.enabled for grant in company_grants
                )
                had_enabled_personal = any(
                    grant.enabled for grant in personal_grants
                )
                if (
                    not was_active
                    and not had_enabled_company
                    and not had_enabled_personal
                ):
                    continue
                model.active = False
                for grant in company_grants:
                    if grant.enabled:
                        grant.enabled = False
                        disabled_company_grant_ids.append(grant.id)
                for grant in personal_grants:
                    if grant.enabled:
                        grant.enabled = False
                        disabled_personal_grant_ids.append(grant.id)
                session.flush()
                invalidated_model_ids.append(model.id)
                AuditService.append(
                    session,
                    **actor_kwargs,
                    action="model.relay_route.invalidate",
                    target_type="model_definition",
                    target_id=model.id,
                    before_summary=before,
                    after_summary={
                        **cls._model_audit_summary(
                            ModelCatalogService.response(session, model=model)
                        ),
                        "relay_route_invalidation": {
                            "reason": invalidation_reasons[model.slug],
                            "catalog_revision": catalog.catalog_revision,
                            "execution_actor": trigger_evidence,
                            "automatic_reactivation": False,
                            "disabled_company_grant_ids": (
                                disabled_company_grant_ids
                            ),
                            "disabled_personal_grant_ids": (
                                disabled_personal_grant_ids
                            ),
                        },
                    },
                    request_id=request_id,
                )

        for item in reconciliation:
            model = item["model"]
            model_response = ModelCatalogService.response(session, model=model)
            if item["created"]:
                created_model_ids.append(model.id)
                AuditService.append(
                    session,
                    **actor_kwargs,
                    action="model.create",
                    target_type="model_definition",
                    target_id=model.id,
                    before_summary={},
                    after_summary={
                        **cls._model_audit_summary(model_response),
                        "creation_source": source,
                        "execution_actor": trigger_evidence,
                    },
                    request_id=request_id,
                )
            ownership_adoption = item["ownership_adoption"]
            if ownership_adoption is not None:
                AuditService.append(
                    session,
                    **actor_kwargs,
                    action="model.relay_catalog.provider_ownership_adopt",
                    target_type="model_definition",
                    target_id=model.id,
                    before_summary=ownership_adoption["before"],
                    after_summary={
                        **ownership_adoption["after"],
                        "reason": "已审计供应商目录接管旧版 Relay 候选草稿",
                        "source": source,
                        "execution_actor": trigger_evidence,
                    },
                    request_id=request_id,
                )
            if item["candidate_changed"] or ownership_adoption is not None:
                synced_model_ids.append(model.id)
            if item["candidate_changed"]:
                before_state = item["before_state"]
                after_state = item["after_state"]
                AuditService.append(
                    session,
                    **actor_kwargs,
                    action="model.relay_capability.candidate_sync",
                    target_type="model_definition",
                    target_id=model.id,
                    before_summary={
                        "candidate_revision": before_state[
                            "candidate_revision"
                        ],
                        "approved_revision": before_state[
                            "approved_revision"
                        ],
                    },
                    after_summary={
                        "relay_capability_decision": {
                            "reason": "Relay 模型目录自动对账",
                            "source": source,
                            "before_revision": before_state[
                                "candidate_revision"
                            ],
                            "after_revision": after_state[
                                "candidate_revision"
                            ],
                            "catalog_revision": after_state[
                                "candidate_catalog_revision"
                            ],
                            "capability_diff": after_state[
                                "capability_diff"
                            ],
                            "execution_actor": trigger_evidence,
                        }
                    },
                    request_id=request_id,
                )

        unchanged_count = sum(
            1
            for item in reconciliation
            if (
                not item["created"]
                and not item["candidate_changed"]
                and item["ownership_adoption"] is None
            )
        )
        summary_audit = None
        catalog_observation_changed = (
            previous_catalog_revision != catalog.catalog_revision
        )
        if (
            created_model_ids
            or synced_model_ids
            or invalidated_model_ids
            or catalog_observation_changed
        ):
            summary_audit = AuditService.append(
                session,
                **actor_kwargs,
                action="model.relay_catalog.reconcile",
                target_type="relay_model_catalog",
                target_id=catalog.catalog_revision,
                before_summary={},
                after_summary={
                    "catalog_revision": catalog.catalog_revision,
                    "published_route_revision": (
                        catalog.published_route_revision
                    ),
                    "relay_model_count": len(catalog.data),
                    "customer_callable_model_ids": sorted(
                        current_callable_ids
                    ),
                    "customer_callable_route_revisions": dict(
                        sorted(current_route_revisions.items())
                    ),
                    "reviewed_candidate_model_ids": sorted(
                        reviewed_candidate_ids
                    ),
                    "missing_previously_callable_model_ids": sorted(
                        missing_published_ids
                    ),
                    "missing_relay_governed_model_ids": sorted(
                        missing_published_ids
                    ),
                    "published_route_revision_drift_model_ids": sorted(
                        route_revision_drift_ids
                    ),
                    "created_count": len(created_model_ids),
                    "synced_count": len(synced_model_ids),
                    "invalidated_count": len(invalidated_model_ids),
                    "unchanged_count": unchanged_count,
                    "created_model_ids": created_model_ids,
                    "synced_model_ids": synced_model_ids,
                    "invalidated_model_ids": invalidated_model_ids,
                    "execution_actor": trigger_evidence,
                    "automatic_approval": False,
                    "automatic_publish": False,
                    "automatic_distribution": False,
                    "automatic_pricing": False,
                },
                request_id=request_id,
            )
        return RelayCatalogReconciliationResult(
            created_model_ids=tuple(created_model_ids),
            synced_model_ids=tuple(synced_model_ids),
            invalidated_model_ids=tuple(invalidated_model_ids),
            unchanged_count=unchanged_count,
            reconciliation_audit_id=(
                summary_audit.id if summary_audit is not None else None
            ),
        )
