from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hmac
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyModelGrant,
    CompanyPointPriceVersion,
    ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan,
    ModelDefinition,
    PersonalRetailModelGrant,
    PointPriceVersionStatus,
    utcnow,
)
from ..schemas import (
    ProviderOnboardingCompanyStatus,
    ProviderOnboardingPersonalStatus,
    ProviderOnboardingStatusBatchRequest,
    ProviderOnboardingStatusBatchResponse,
    ProviderOnboardingStatusRequestItem,
    ProviderOnboardingStatusResponseItem,
)
from .provider_route_identity import canonical_sha256


def provider_onboarding_request_identity_sha256(
    body: ProviderOnboardingStatusBatchRequest,
) -> str:
    """Bind a response to the exact account/model identities that were queried."""

    items = [
        {
            "provider_account_id": item.provider_account_id,
            "provider_channel_id": item.provider_channel_id,
            "provider_name": item.provider_name,
            "request_key": item.request_key,
            "route_identity_sha256": item.route_identity_sha256,
        }
        for item in sorted(body.items, key=lambda row: row.request_key)
    ]
    return canonical_sha256({"schema_version": 1, "items": items})


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _empty_personal(*, state: str) -> ProviderOnboardingPersonalStatus:
    if state == "drifted":
        return ProviderOnboardingPersonalStatus(
            price_status="drifted",
            grant_status="drifted",
        )
    if state == "unavailable":
        return ProviderOnboardingPersonalStatus(
            price_status="unavailable",
            grant_status="unavailable",
        )
    return ProviderOnboardingPersonalStatus(
        price_status="not_configured",
        grant_status="not_granted",
    )


def _empty_company(*, state: str) -> ProviderOnboardingCompanyStatus:
    if state == "drifted":
        price_status = "drifted"
        grant_status = "drifted"
    elif state == "unavailable":
        price_status = "unavailable"
        grant_status = "unavailable"
    else:
        price_status = "not_applicable"
        grant_status = "not_applicable"
    return ProviderOnboardingCompanyStatus(
        price_status=price_status,
        grant_status=grant_status,
        grant_count=0,
        enabled_grant_count=0,
        active_grant_count=0,
        active_price_count=0,
    )


def _price_for_mode(
    *,
    billing_mode: str,
    price_per_second_points: int | None,
    price_per_item_points: int | None,
) -> tuple[int | None, bool]:
    if billing_mode == "per_second":
        expected = price_per_second_points
        unexpected = price_per_item_points
    elif billing_mode == "per_item":
        expected = price_per_item_points
        unexpected = price_per_second_points
    else:
        return None, False
    return expected, bool(
        isinstance(expected, int)
        and not isinstance(expected, bool)
        and expected > 0
        and unexpected is None
    )


class ProviderOnboardingStatusProjectionService:
    """Read-only view of Platform commercial authority for exact Relay routes."""

    @classmethod
    def project(
        cls,
        session: Session,
        *,
        body: ProviderOnboardingStatusBatchRequest,
    ) -> ProviderOnboardingStatusBatchResponse:
        observed_at = utcnow()
        slugs = {item.route_identity.public_model_id for item in body.items}
        models = {
            model.slug: model
            for model in session.scalars(
                select(ModelDefinition).where(ModelDefinition.slug.in_(slugs))
            ).all()
        }
        model_ids = [model.id for model in models.values()]
        plans_by_model: dict[str, list[ModelCommercialReleasePlan]] = defaultdict(
            list
        )
        executions_by_plan: dict[str, ModelCommercialReleaseExecution] = {}
        if model_ids:
            plans = session.scalars(
                select(ModelCommercialReleasePlan)
                .where(ModelCommercialReleasePlan.model_id.in_(model_ids))
                .order_by(
                    ModelCommercialReleasePlan.model_id,
                    ModelCommercialReleasePlan.revision.desc(),
                    ModelCommercialReleasePlan.created_at.desc(),
                    ModelCommercialReleasePlan.id.desc(),
                )
            ).all()
            for plan in plans:
                plans_by_model[plan.model_id].append(plan)
            plan_ids = [plan.id for plan in plans]
            if plan_ids:
                executions_by_plan = {
                    execution.plan_id: execution
                    for execution in session.scalars(
                        select(ModelCommercialReleaseExecution).where(
                            ModelCommercialReleaseExecution.plan_id.in_(plan_ids)
                        )
                    ).all()
                }

        response_items = [
            cls._project_one(
                session,
                item=item,
                model=models.get(item.route_identity.public_model_id),
                plans_by_model=plans_by_model,
                executions_by_plan=executions_by_plan,
                observed_at=observed_at,
            )
            for item in body.items
        ]
        return ProviderOnboardingStatusBatchResponse(
            schema_version=1,
            observed_at=observed_at,
            request_identity_sha256=(
                provider_onboarding_request_identity_sha256(body)
            ),
            items=response_items,
        )

    @classmethod
    def _project_one(
        cls,
        session: Session,
        *,
        item: ProviderOnboardingStatusRequestItem,
        model: ModelDefinition | None,
        plans_by_model: Mapping[str, list[ModelCommercialReleasePlan]],
        executions_by_plan: Mapping[str, ModelCommercialReleaseExecution],
        observed_at: datetime,
    ) -> ProviderOnboardingStatusResponseItem:
        base = {
            "request_key": item.request_key,
            "public_model_id": item.route_identity.public_model_id,
            "route_identity_sha256": item.route_identity_sha256,
        }
        if model is None:
            return ProviderOnboardingStatusResponseItem(
                **base,
                publication_status="not_observed",
                blocker_code="platform_model_not_observed",
                personal=_empty_personal(state="not_observed"),
                company=_empty_company(state="not_observed"),
            )

        route_identity = item.route_identity.model_dump(mode="json")
        plans = plans_by_model.get(model.id, [])
        candidate_is_current = (
            model.relay_capability_candidate_revision
            == item.route_identity.capability_revision
        )
        exact_plans = [
            plan
            for plan in plans
            if plan.candidate_revision == item.route_identity.capability_revision
            and plan.approved_route_identity == route_identity
            and plan.approved_route_identity_sha256 is not None
            and hmac.compare_digest(
                plan.approved_route_identity_sha256,
                item.route_identity_sha256,
            )
        ]
        if not candidate_is_current:
            return ProviderOnboardingStatusResponseItem(
                **base,
                publication_status=(
                    "drifted"
                    if model.relay_capability_candidate_revision is not None
                    or exact_plans
                    else "not_observed"
                ),
                blocker_code=(
                    "platform_candidate_drift"
                    if model.relay_capability_candidate_revision is not None
                    or exact_plans
                    else "platform_candidate_not_observed"
                ),
                personal=_empty_personal(
                    state=(
                        "drifted"
                        if model.relay_capability_candidate_revision is not None
                        or exact_plans
                        else "not_observed"
                    )
                ),
                company=_empty_company(
                    state=(
                        "drifted"
                        if model.relay_capability_candidate_revision is not None
                        or exact_plans
                        else "not_observed"
                    )
                ),
            )

        exact_released = [
            (plan, executions_by_plan.get(plan.id))
            for plan in exact_plans
            if (
                (execution := executions_by_plan.get(plan.id)) is not None
                and execution.state == "released"
            )
        ]
        if exact_released:
            plan, execution = exact_released[0]
            assert execution is not None
            integrity = cls._released_integrity(
                model=model,
                plan=plan,
                execution=execution,
                route_identity=route_identity,
                route_identity_sha256=item.route_identity_sha256,
            )
            if integrity is not None:
                return ProviderOnboardingStatusResponseItem(
                    **base,
                    publication_status=integrity[0],
                    blocker_code=integrity[1],
                    plan_id=plan.id,
                    execution_id=execution.id,
                    publication_receipt_sha256=None,
                    personal=_empty_personal(state=integrity[0]),
                    company=_empty_company(state=integrity[0]),
                )
            personal = cls._personal_status(
                session,
                model=model,
                execution=execution,
            )
            company = cls._company_status(
                session,
                model=model,
                execution=execution,
                observed_at=observed_at,
            )
            return ProviderOnboardingStatusResponseItem(
                **base,
                publication_status=("released" if model.active else "disabled"),
                blocker_code=(None if model.active else "platform_model_disabled"),
                plan_id=plan.id,
                execution_id=execution.id,
                publication_receipt_sha256=execution.publication_receipt_sha256,
                personal=personal,
                company=company,
            )

        if exact_plans:
            plan = exact_plans[0]
            execution = executions_by_plan.get(plan.id)
            if execution is None:
                return ProviderOnboardingStatusResponseItem(
                    **base,
                    publication_status="unavailable",
                    blocker_code="commercial_execution_unavailable",
                    plan_id=plan.id,
                    personal=_empty_personal(state="unavailable"),
                    company=_empty_company(state="unavailable"),
                )
            if execution.state == "blocked":
                status = "blocked"
                blocker = execution.last_blocker_code or "commercial_release_blocked"
            elif execution.state == "approved":
                status = "reconciliation_pending"
                blocker = "commercial_release_reconciliation_pending"
            else:
                status = "drifted"
                blocker = "commercial_release_superseded"
            return ProviderOnboardingStatusResponseItem(
                **base,
                publication_status=status,
                blocker_code=blocker,
                plan_id=plan.id,
                execution_id=execution.id,
                publication_receipt_sha256=execution.publication_receipt_sha256,
                personal=_empty_personal(
                    state=("drifted" if status == "drifted" else status)
                ),
                company=_empty_company(
                    state=("drifted" if status == "drifted" else status)
                ),
            )

        same_candidate_plans = [
            plan
            for plan in plans
            if plan.candidate_revision == item.route_identity.capability_revision
        ]
        if same_candidate_plans:
            return ProviderOnboardingStatusResponseItem(
                **base,
                publication_status="drifted",
                blocker_code="provider_route_identity_drift",
                plan_id=same_candidate_plans[0].id,
                execution_id=(
                    executions_by_plan[same_candidate_plans[0].id].id
                    if same_candidate_plans[0].id in executions_by_plan
                    else None
                ),
                personal=_empty_personal(state="drifted"),
                company=_empty_company(state="drifted"),
            )
        return ProviderOnboardingStatusResponseItem(
            **base,
            publication_status="commercial_approval_pending",
            blocker_code="commercial_approval_not_recorded",
            personal=_empty_personal(state="commercial_approval_pending"),
            company=_empty_company(state="commercial_approval_pending"),
        )

    @staticmethod
    def _released_integrity(
        *,
        model: ModelDefinition,
        plan: ModelCommercialReleasePlan,
        execution: ModelCommercialReleaseExecution,
        route_identity: dict[str, Any],
        route_identity_sha256: str,
    ) -> tuple[str, str] | None:
        if (
            execution.released_route_identity_sha256 is None
            or not hmac.compare_digest(
                execution.released_route_identity_sha256,
                route_identity_sha256,
            )
        ):
            return "drifted", "released_route_identity_drift"
        receipt = execution.publication_receipt
        receipt_sha256 = execution.publication_receipt_sha256
        if (
            not isinstance(receipt, dict)
            or receipt_sha256 is None
            or not hmac.compare_digest(
                canonical_sha256(receipt),
                receipt_sha256,
            )
        ):
            return "unavailable", "publication_receipt_invalid"
        relay_release = receipt.get("relay_route_release")
        if not isinstance(relay_release, dict):
            return "unavailable", "publication_receipt_invalid"
        receipt_identity = relay_release.get("route_identity")
        receipt_identity_sha256 = relay_release.get("route_identity_sha256")
        if (
            receipt.get("schema_version") != 4
            or receipt.get("plan_id") != plan.id
            or receipt.get("plan_content_sha256") != plan.content_sha256
            or receipt.get("model_id") != model.id
            or receipt.get("model_slug") != model.slug
            or receipt.get("capability_revision")
            != route_identity["capability_revision"]
            or receipt.get("route_identity_sha256")
            != route_identity_sha256
            or receipt_identity != route_identity
            or not isinstance(receipt_identity_sha256, str)
            or not hmac.compare_digest(
                receipt_identity_sha256,
                route_identity_sha256,
            )
        ):
            return "unavailable", "publication_receipt_identity_invalid"
        release_fields = (
            "model_release_id",
            "model_release_revision",
            "published_route_revision",
            "routing_release_sha256",
            "provider_cost_readiness_sha256",
        )
        if relay_release.get("schema_version") != 1 or any(
            relay_release.get(field_name) != route_identity[field_name]
            for field_name in release_fields
        ):
            return "unavailable", "publication_receipt_identity_invalid"
        route_release_evidence = execution.route_release_evidence
        route_release_evidence_sha256 = relay_release.get(
            "route_release_evidence_sha256"
        )
        if (
            not isinstance(route_release_evidence, dict)
            or not isinstance(route_release_evidence_sha256, str)
            or not hmac.compare_digest(
                canonical_sha256(route_release_evidence),
                route_release_evidence_sha256,
            )
        ):
            return "unavailable", "publication_release_evidence_invalid"
        if (
            model.published_at is None
            or model.relay_capability_revision
            != route_identity["capability_revision"]
        ):
            return "drifted", "platform_publication_drift"
        return None

    @staticmethod
    def _personal_status(
        session: Session,
        *,
        model: ModelDefinition,
        execution: ModelCommercialReleaseExecution,
    ) -> ProviderOnboardingPersonalStatus:
        receipt = execution.publication_receipt or {}
        expected_grant_id = receipt.get("personal_grant_id")
        if (
            execution.personal_grant_id is None
            or expected_grant_id != execution.personal_grant_id
        ):
            return _empty_personal(state="unavailable")
        grant = session.get(PersonalRetailModelGrant, execution.personal_grant_id)
        if grant is None or grant.model_id != model.id:
            return _empty_personal(state="unavailable")
        price, valid_price = _price_for_mode(
            billing_mode=model.billing_mode,
            price_per_second_points=grant.price_per_second_points,
            price_per_item_points=grant.price_per_item_points,
        )
        if valid_price:
            price_status = "active"
        elif (
            grant.price_per_second_points is None
            and grant.price_per_item_points is None
        ):
            price_status = "not_configured"
        else:
            price_status = "drifted"
        return ProviderOnboardingPersonalStatus(
            price_status=price_status,
            grant_status=("active" if grant.enabled else "disabled"),
            price_points=(price if valid_price else None),
            grant_id=grant.id,
        )

    @staticmethod
    def _company_status(
        session: Session,
        *,
        model: ModelDefinition,
        execution: ModelCommercialReleaseExecution,
        observed_at: datetime,
    ) -> ProviderOnboardingCompanyStatus:
        receipt = execution.publication_receipt or {}
        receipt_grants = receipt.get("company_grants")
        receipt_company_ids = receipt.get("company_ids")
        if not isinstance(receipt_grants, list) or not isinstance(
            receipt_company_ids, list
        ):
            return _empty_company(state="unavailable")
        expected_count = execution.company_grant_count
        if (
            expected_count != len(receipt_grants)
            or expected_count != len(receipt_company_ids)
            or expected_count != receipt.get("company_grant_count")
            or execution.company_ids != receipt_company_ids
        ):
            return _empty_company(state="unavailable")
        if expected_count == 0:
            return _empty_company(state="not_applicable")
        grant_ids: list[str] = []
        expected_companies: dict[str, str] = {}
        for row in receipt_grants:
            if not isinstance(row, dict):
                return _empty_company(state="unavailable")
            grant_id = row.get("grant_id")
            company_id = row.get("company_id")
            if not isinstance(grant_id, str) or not isinstance(company_id, str):
                return _empty_company(state="unavailable")
            grant_ids.append(grant_id)
            expected_companies[grant_id] = company_id
        if (
            len(set(grant_ids)) != expected_count
            or len(set(receipt_company_ids)) != expected_count
            or set(expected_companies.values()) != set(receipt_company_ids)
        ):
            return _empty_company(state="unavailable")
        grants = session.scalars(
            select(CompanyModelGrant).where(CompanyModelGrant.id.in_(grant_ids))
        ).all()
        grants_by_id = {grant.id: grant for grant in grants}
        if len(grants_by_id) != expected_count or any(
            grant.model_id != model.id
            or grant.company_id != expected_companies[grant.id]
            for grant in grants
        ):
            return ProviderOnboardingCompanyStatus(
                price_status="unavailable",
                grant_status="unavailable",
                grant_count=expected_count,
                enabled_grant_count=0,
                active_grant_count=0,
                active_price_count=0,
            )

        enabled_count = sum(grant.enabled for grant in grants)
        active_grant_count = sum(
            grant.enabled
            and (
                grant.effective_at is None
                or _as_utc(grant.effective_at) <= observed_at
            )
            and (
                grant.expires_at is None
                or _as_utc(grant.expires_at) > observed_at
            )
            for grant in grants
        )
        active_price_count = 0
        for grant in grants:
            price, valid_price = _price_for_mode(
                billing_mode=model.billing_mode,
                price_per_second_points=grant.price_per_second_points,
                price_per_item_points=grant.price_per_item_points,
            )
            version = (
                session.get(
                    CompanyPointPriceVersion,
                    grant.point_price_active_version_id,
                )
                if grant.point_price_active_version_id is not None
                else None
            )
            if (
                valid_price
                and version is not None
                and version.status == PointPriceVersionStatus.ACTIVE
                and version.company_id == grant.company_id
                and version.grant_id == grant.id
                and version.model_id == model.id
                and version.billing_mode == model.billing_mode
                and version.unit_price_points == price
            ):
                active_price_count += 1

        if active_price_count == expected_count:
            price_status = "active"
        elif active_price_count == 0:
            price_status = "not_configured"
        else:
            price_status = "partial"
        if active_grant_count == expected_count:
            grant_status = "active"
        elif active_grant_count == 0:
            grant_status = "disabled"
        else:
            grant_status = "partial"
        return ProviderOnboardingCompanyStatus(
            price_status=price_status,
            grant_status=grant_status,
            grant_count=expected_count,
            enabled_grant_count=enabled_count,
            active_grant_count=active_grant_count,
            active_price_count=active_price_count,
        )
