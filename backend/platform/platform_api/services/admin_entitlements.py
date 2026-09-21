from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    BillingUnit,
    Company,
    CompanyEntitlementBatchJournal,
    CompanyModelGrant,
    CompanyResourceGrant,
    ModelDefinition,
    ResourceDefinition,
    ResourceKind,
    new_id,
    utcnow,
)
from .audit import AuditService
from .errors import ConflictError, NotFoundError
from .commercial_pricing import CommercialPricingPolicy
from .models import ModelGrantService
from .model_release_guard import require_model_release_snapshot
from .resources import ResourceGrantService


EntitlementKind = Literal["model", "resource"]
CopyMode = Literal["merge", "replace"]
MAX_BATCH_CELLS = 500


def _company_billing_contract(company: Company) -> tuple[BillingUnit, int]:
    if company.billing_version == 1:
        return BillingUnit.CNY_CENT, 1
    if company.billing_version == 2:
        return BillingUnit.POINT, 2
    raise ConflictError("company billing version is invalid")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    normalized = _utc(value)
    return normalized.isoformat() if normalized else None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    return value


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _grant_schedule(grant: Any) -> tuple[datetime | None, datetime | None]:
    return (
        _utc(getattr(grant, "effective_at", None)),
        _utc(getattr(grant, "expires_at", None)),
    )


def _cell_state(
    *, grant: Any | None, catalog_active: bool, now: datetime
) -> str:
    if not catalog_active:
        return "retired"
    if grant is None:
        return "unconfigured"
    effective_at, expires_at = _grant_schedule(grant)
    if expires_at is not None and expires_at <= now:
        return "expired"
    if effective_at is not None and effective_at > now and grant.enabled:
        return "scheduled"
    return "enabled" if grant.enabled else "disabled"


def _model_catalog_active(model: ModelDefinition) -> bool:
    return bool(model.active and model.published_at is not None)


def _catalog_item(kind: EntitlementKind, item: Any) -> dict[str, Any]:
    if kind == "model":
        active = _model_catalog_active(item)
        lifecycle = (
            "draft"
            if item.published_at is None
            else ("active" if item.active else "retired")
        )
        return {
            "item_kind": "model",
            "item_id": item.id,
            "catalog_key": item.slug,
            "display_name": item.display_name,
            "resource_kind": None,
            "catalog_active": active,
            "lifecycle": lifecycle,
            "billing_mode": item.billing_mode,
            "catalog_version": item.capability_version,
        }
    return {
        "item_kind": "resource",
        "item_id": item.id,
        "catalog_key": item.key,
        "display_name": item.display_name,
        "resource_kind": _enum_value(item.kind),
        "catalog_active": bool(item.active),
        "lifecycle": "active" if item.active else "retired",
        "billing_mode": None,
        "catalog_version": None,
    }


def _grant_before(
    kind: EntitlementKind,
    grant: Any | None,
    *,
    billing_unit: BillingUnit | None = None,
    billing_version: int | None = None,
) -> dict[str, Any]:
    if grant is None:
        result: dict[str, Any] = {"configured": False}
        if kind == "model" and billing_unit is not None:
            result.update(
                {
                    "billing_unit": billing_unit.value,
                    "billing_version": billing_version,
                    "price_per_second_cents": None,
                    "price_per_item_cents": None,
                    "price_per_second_points": None,
                    "price_per_item_points": None,
                }
            )
        return result
    result: dict[str, Any] = {
        "configured": True,
        "grant_id": grant.id,
        "enabled": bool(grant.enabled),
        "config_override": grant.config_override or {},
        "call_quota": grant.call_quota,
        "concurrency_limit": grant.concurrency_limit,
        "updated_at": _iso(grant.updated_at),
        "effective_at": _iso(getattr(grant, "effective_at", None)),
        "expires_at": _iso(getattr(grant, "expires_at", None)),
    }
    if kind == "model":
        result.update(
            {
                "billing_unit": (
                    billing_unit.value if billing_unit is not None else None
                ),
                "billing_version": billing_version,
                "price_per_second_cents": grant.price_per_second_cents,
                "price_per_item_cents": grant.price_per_item_cents,
                "price_per_second_points": grant.price_per_second_points,
                "price_per_item_points": grant.price_per_item_points,
            }
        )
    return result


class AdminEntitlementService:
    @staticmethod
    def matrix(
        session: Session,
        *,
        company_page: int,
        company_page_size: int,
        catalog_page: int,
        catalog_page_size: int,
        company_query: str | None = None,
        catalog_query: str | None = None,
        catalog_kind: str | None = None,
        include_retired: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc(now or datetime.now(timezone.utc))
        assert current is not None
        company_filters = []
        if company_query:
            needle = f"%{company_query.strip().casefold()}%"
            company_filters.append(func.lower(Company.name).like(needle))
        total_companies = int(
            session.scalar(select(func.count(Company.id)).where(*company_filters)) or 0
        )
        companies = list(
            session.scalars(
                select(Company)
                .where(*company_filters)
                .order_by(Company.name, Company.id)
                .offset((company_page - 1) * company_page_size)
                .limit(company_page_size)
            ).all()
        )

        catalog: list[tuple[EntitlementKind, Any]] = []
        if catalog_kind in {None, "all", "model"}:
            model_statement = select(ModelDefinition)
            if not include_retired:
                model_statement = model_statement.where(
                    ModelDefinition.active.is_(True),
                    ModelDefinition.published_at.is_not(None),
                )
            if catalog_query:
                needle = f"%{catalog_query.strip().casefold()}%"
                model_statement = model_statement.where(
                    or_(
                        func.lower(ModelDefinition.display_name).like(needle),
                        func.lower(ModelDefinition.slug).like(needle),
                    )
                )
            catalog.extend(("model", item) for item in session.scalars(model_statement))

        allowed_resource_kinds = {
            "feature": ResourceKind.FEATURE,
            "agent": ResourceKind.AGENT,
            "external_api": ResourceKind.EXTERNAL_API,
        }
        if catalog_kind in {None, "all", *allowed_resource_kinds.keys()}:
            resource_statement = select(ResourceDefinition)
            if catalog_kind in allowed_resource_kinds:
                resource_statement = resource_statement.where(
                    ResourceDefinition.kind == allowed_resource_kinds[catalog_kind]
                )
            if not include_retired:
                resource_statement = resource_statement.where(
                    ResourceDefinition.active.is_(True)
                )
            if catalog_query:
                needle = f"%{catalog_query.strip().casefold()}%"
                resource_statement = resource_statement.where(
                    or_(
                        func.lower(ResourceDefinition.display_name).like(needle),
                        func.lower(ResourceDefinition.key).like(needle),
                    )
                )
            catalog.extend(
                ("resource", item) for item in session.scalars(resource_statement)
            )
        if catalog_kind not in {
            None,
            "all",
            "model",
            "feature",
            "agent",
            "external_api",
        }:
            raise ConflictError("unsupported catalog_kind")

        catalog.sort(
            key=lambda pair: (
                0 if pair[0] == "model" else 1,
                _enum_value(getattr(pair[1], "kind", "")),
                getattr(pair[1], "display_name", ""),
                pair[1].id,
            )
        )
        total_catalog_items = len(catalog)
        start = (catalog_page - 1) * catalog_page_size
        selected_catalog = catalog[start : start + catalog_page_size]
        company_ids = [company.id for company in companies]
        model_ids = [item.id for kind, item in selected_catalog if kind == "model"]
        resource_ids = [item.id for kind, item in selected_catalog if kind == "resource"]
        model_grants = {
            (grant.company_id, grant.model_id): grant
            for grant in session.scalars(
                select(CompanyModelGrant).where(
                    CompanyModelGrant.company_id.in_(company_ids),
                    CompanyModelGrant.model_id.in_(model_ids),
                )
            ).all()
        } if company_ids and model_ids else {}
        resource_grants = {
            (grant.company_id, grant.resource_id): grant
            for grant in session.scalars(
                select(CompanyResourceGrant).where(
                    CompanyResourceGrant.company_id.in_(company_ids),
                    CompanyResourceGrant.resource_id.in_(resource_ids),
                )
            ).all()
        } if company_ids and resource_ids else {}

        columns = [_catalog_item(kind, item) for kind, item in selected_catalog]
        rows: list[dict[str, Any]] = []
        for company in companies:
            billing_unit, billing_version = _company_billing_contract(company)
            cells = []
            for kind, item in selected_catalog:
                grant = (
                    model_grants.get((company.id, item.id))
                    if kind == "model"
                    else resource_grants.get((company.id, item.id))
                )
                catalog_active = (
                    _model_catalog_active(item) if kind == "model" else bool(item.active)
                )
                cell = {
                    "item_kind": kind,
                    "item_id": item.id,
                    "state": _cell_state(
                        grant=grant, catalog_active=catalog_active, now=current
                    ),
                    **_grant_before(
                        kind,
                        grant,
                        billing_unit=billing_unit,
                        billing_version=billing_version,
                    ),
                }
                cells.append(cell)
            rows.append(
                {
                    "company_id": company.id,
                    "company_name": company.name,
                    "company_status": _enum_value(company.status),
                    "billing_unit": billing_unit.value,
                    "billing_version": billing_version,
                    "cells": cells,
                }
            )
        return {
            "generated_at": current.isoformat(),
            "grant_schedule_data_status": (
                "available"
                if hasattr(CompanyModelGrant, "effective_at")
                and hasattr(CompanyResourceGrant, "effective_at")
                else "unavailable"
            ),
            "company_page": company_page,
            "company_page_size": company_page_size,
            "total_companies": total_companies,
            "catalog_page": catalog_page,
            "catalog_page_size": catalog_page_size,
            "total_catalog_items": total_catalog_items,
            "columns": columns,
            "rows": rows,
        }

    @staticmethod
    def coverage(
        session: Session,
        *,
        include_retired: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _utc(now or datetime.now(timezone.utc))
        assert current is not None
        total_companies = int(session.scalar(select(func.count(Company.id))) or 0)
        models = list(
            session.scalars(select(ModelDefinition).order_by(ModelDefinition.slug)).all()
        )
        resources = list(
            session.scalars(
                select(ResourceDefinition).order_by(
                    ResourceDefinition.kind, ResourceDefinition.key
                )
            ).all()
        )
        model_grants: dict[str, list[CompanyModelGrant]] = {}
        for grant in session.scalars(select(CompanyModelGrant)):
            model_grants.setdefault(grant.model_id, []).append(grant)
        resource_grants: dict[str, list[CompanyResourceGrant]] = {}
        for grant in session.scalars(select(CompanyResourceGrant)):
            resource_grants.setdefault(grant.resource_id, []).append(grant)

        items: list[dict[str, Any]] = []
        for kind, definitions, grants_by_item in (
            ("model", models, model_grants),
            ("resource", resources, resource_grants),
        ):
            for definition in definitions:
                catalog_active = (
                    _model_catalog_active(definition)
                    if kind == "model"
                    else bool(definition.active)
                )
                if not include_retired and not catalog_active:
                    continue
                grants = grants_by_item.get(definition.id, [])
                states = Counter(
                    _cell_state(grant=grant, catalog_active=catalog_active, now=current)
                    for grant in grants
                )
                configured = len(grants)
                item = _catalog_item(kind, definition)
                item.update(
                    {
                        "configured_company_count": configured,
                        "unconfigured_company_count": max(
                            0, total_companies - configured
                        ),
                        "enabled_company_count": states["enabled"],
                        "disabled_company_count": states["disabled"],
                        "scheduled_company_count": states["scheduled"],
                        "expired_company_count": states["expired"],
                        "coverage_rate": (
                            round(states["enabled"] / total_companies, 6)
                            if total_companies
                            else None
                        ),
                    }
                )
                items.append(item)
        items.sort(
            key=lambda item: (
                0 if item["item_kind"] == "model" else 1,
                item["resource_kind"] or "",
                item["display_name"],
                item["item_id"],
            )
        )
        return {
            "generated_at": current.isoformat(),
            "total_companies": total_companies,
            "items": items,
        }

    @staticmethod
    def _normalize_changes(changes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in changes:
            kind = raw.get("item_kind")
            company_id = str(raw.get("company_id", "")).strip()
            item_id = str(raw.get("item_id", "")).strip()
            if kind not in {"model", "resource"}:
                raise ConflictError("item_kind must be model or resource")
            if not company_id or not item_id:
                raise ConflictError("company_id and item_id are required")
            key = (company_id, kind, item_id)
            if key in seen:
                raise ConflictError("batch contains duplicate entitlement cells")
            seen.add(key)
            billing_unit = raw.get("billing_unit")
            if billing_unit is not None:
                billing_unit = _enum_value(billing_unit)
                if billing_unit not in {
                    BillingUnit.CNY_CENT.value,
                    BillingUnit.POINT.value,
                }:
                    raise ConflictError("billing_unit is invalid")
            billing_version = raw.get("billing_version")
            if billing_version is not None:
                try:
                    billing_version = int(billing_version)
                except (TypeError, ValueError) as exc:
                    raise ConflictError("billing_version is invalid") from exc
            point_prices = (
                raw.get("price_per_second_points"),
                raw.get("price_per_item_points"),
            )
            cent_prices = (
                raw.get("price_per_second_cents"),
                raw.get("price_per_item_cents"),
            )
            if kind == "resource" and (
                billing_unit is not None
                or billing_version is not None
                or any(price is not None for price in (*point_prices, *cent_prices))
            ):
                raise ConflictError("resource grants do not have a billing contract")
            result.append(
                {
                    "company_id": company_id,
                    "item_kind": kind,
                    "item_id": item_id,
                    "enabled": bool(raw.get("enabled")),
                    "billing_unit": billing_unit,
                    "billing_version": billing_version,
                    "price_per_second_cents": raw.get("price_per_second_cents"),
                    "price_per_item_cents": raw.get("price_per_item_cents"),
                    "price_per_second_points": raw.get("price_per_second_points"),
                    "price_per_item_points": raw.get("price_per_item_points"),
                    "config_override": raw.get("config_override"),
                    "call_quota": raw.get("call_quota"),
                    "concurrency_limit": raw.get("concurrency_limit"),
                    "effective_at": raw.get("effective_at"),
                    "expires_at": raw.get("expires_at"),
                    "effective_at_set": bool(
                        raw.get("effective_at_set", "effective_at" in raw)
                    ),
                    "expires_at_set": bool(
                        raw.get("expires_at_set", "expires_at" in raw)
                    ),
                    "call_quota_set": bool(
                        raw.get("call_quota_set", "call_quota" in raw)
                    ),
                    "concurrency_limit_set": bool(
                        raw.get(
                            "concurrency_limit_set", "concurrency_limit" in raw
                        )
                    ),
                }
            )
        if not result:
            raise ConflictError("batch must contain at least one entitlement cell")
        if len(result) > MAX_BATCH_CELLS:
            raise ConflictError(f"batch cannot exceed {MAX_BATCH_CELLS} entitlement cells")
        result.sort(key=lambda item: (item["company_id"], item["item_kind"], item["item_id"]))
        return result

    @classmethod
    def preview_changes(
        cls,
        session: Session,
        *,
        changes: Iterable[dict[str, Any]],
        lock_rows: bool = False,
        expected_release_snapshots: Mapping[
            str, Mapping[str, Any]
        ] | None = None,
    ) -> dict[str, Any]:
        normalized = cls._normalize_changes(changes)
        company_ids = sorted({item["company_id"] for item in normalized})
        model_ids = sorted(
            {item["item_id"] for item in normalized if item["item_kind"] == "model"}
        )
        resource_ids = sorted(
            {item["item_id"] for item in normalized if item["item_kind"] == "resource"}
        )
        company_statement = select(Company).where(Company.id.in_(company_ids)).order_by(Company.id)
        model_statement = select(ModelDefinition).where(ModelDefinition.id.in_(model_ids)).order_by(ModelDefinition.id)
        resource_statement = select(ResourceDefinition).where(ResourceDefinition.id.in_(resource_ids)).order_by(ResourceDefinition.id)
        model_grant_statement = select(CompanyModelGrant).where(
            CompanyModelGrant.company_id.in_(company_ids),
            CompanyModelGrant.model_id.in_(model_ids),
        ).order_by(CompanyModelGrant.company_id, CompanyModelGrant.model_id)
        resource_grant_statement = select(CompanyResourceGrant).where(
            CompanyResourceGrant.company_id.in_(company_ids),
            CompanyResourceGrant.resource_id.in_(resource_ids),
        ).order_by(CompanyResourceGrant.company_id, CompanyResourceGrant.resource_id)
        if lock_rows:
            company_statement = company_statement.with_for_update()
            model_statement = model_statement.execution_options(
                populate_existing=True
            ).with_for_update()
            resource_statement = resource_statement.with_for_update()
            model_grant_statement = model_grant_statement.with_for_update()
            resource_grant_statement = resource_grant_statement.with_for_update()
        companies = {item.id: item for item in session.scalars(company_statement)}
        models = {item.id: item for item in session.scalars(model_statement)}
        model_grants = {
            (grant.company_id, grant.model_id): grant
            for grant in session.scalars(model_grant_statement)
        } if model_ids else {}
        # Match generation admission's lock order: models, model grants,
        # resources, then resource grants. A batch edit can touch all four.
        resources = {item.id: item for item in session.scalars(resource_statement)}
        resource_grants = {
            (grant.company_id, grant.resource_id): grant
            for grant in session.scalars(resource_grant_statement)
        } if resource_ids else {}

        missing_companies = sorted(set(company_ids) - set(companies))
        missing_models = sorted(set(model_ids) - set(models))
        missing_resources = sorted(set(resource_ids) - set(resources))
        if missing_companies:
            raise NotFoundError(f"companies do not exist: {', '.join(missing_companies)}")
        if missing_models:
            raise NotFoundError(f"models do not exist: {', '.join(missing_models)}")
        if missing_resources:
            raise NotFoundError(f"resources do not exist: {', '.join(missing_resources)}")
        for model_id, snapshot in (expected_release_snapshots or {}).items():
            model = models.get(model_id)
            if model is None:
                raise NotFoundError(f"models do not exist: {model_id}")
            require_model_release_snapshot(
                model=model,
                expected_snapshot=snapshot,
            )

        changes_out: list[dict[str, Any]] = []
        for desired in normalized:
            kind = desired["item_kind"]
            company = companies[desired["company_id"]]
            billing_unit, billing_version = _company_billing_contract(company)
            item = models[desired["item_id"]] if kind == "model" else resources[desired["item_id"]]
            grant = (
                model_grants.get((desired["company_id"], desired["item_id"]))
                if kind == "model"
                else resource_grants.get((desired["company_id"], desired["item_id"]))
            )
            before = _grant_before(
                kind,
                grant,
                billing_unit=billing_unit,
                billing_version=billing_version,
            )
            if kind == "model":
                if desired["enabled"] and not _model_catalog_active(item):
                    raise ConflictError("cannot enable a draft or retired model")
                declared_unit = desired["billing_unit"]
                declared_version = desired["billing_version"]
                if (declared_unit is None) != (declared_version is None):
                    raise ConflictError(
                        "billing_unit and billing_version must be supplied together"
                    )
                if declared_unit is None and billing_unit == BillingUnit.POINT:
                    raise ConflictError(
                        "point entitlement changes require explicit POINT v2 billing evidence"
                    )
                if declared_unit is not None and (
                    declared_unit != billing_unit.value
                    or declared_version != billing_version
                ):
                    raise ConflictError(
                        "entitlement billing contract does not match target company"
                    )
                second_cents = desired["price_per_second_cents"]
                item_cents = desired["price_per_item_cents"]
                second_points = desired["price_per_second_points"]
                item_points = desired["price_per_item_points"]
                if (
                    second_cents is None
                    and item_cents is None
                    and second_points is None
                    and item_points is None
                    and grant is not None
                ):
                    second_cents = grant.price_per_second_cents
                    item_cents = grant.price_per_item_cents
                    second_points = grant.price_per_second_points
                    item_points = grant.price_per_item_points
                cent_prices = (second_cents, item_cents)
                point_prices = (second_points, item_points)
                if billing_unit == BillingUnit.CNY_CENT:
                    if any(price is not None for price in point_prices):
                        raise ConflictError(
                            "legacy company entitlement cannot contain point prices"
                        )
                    second_price, item_price = cent_prices
                else:
                    if any(price is not None for price in cent_prices):
                        raise ConflictError(
                            "point company entitlement cannot contain cent prices"
                        )
                    second_price, item_price = point_prices
                if desired["enabled"] and second_price is None and item_price is None:
                    raise ConflictError("enabling a model requires its catalog billing price")
                if second_price is not None and item_price is not None:
                    raise ConflictError("a model grant can configure only one billing price")
                if second_price is not None and item.billing_mode != "per_second":
                    raise ConflictError("model price does not match catalog billing_mode")
                if item_price is not None and item.billing_mode != "per_item":
                    raise ConflictError("model price does not match catalog billing_mode")
                if second_price is not None and int(second_price) <= 0:
                    raise ConflictError("model price must be positive")
                if item_price is not None and int(item_price) <= 0:
                    raise ConflictError("model price must be positive")
                if second_price is not None or item_price is not None:
                    CommercialPricingPolicy.require_price(
                        session,
                        model=item,
                        unit_price=second_price if second_price is not None else item_price,
                        billing_unit=billing_unit.value,
                        enabled=desired["enabled"],
                        current_unit_price=(
                            (grant.price_per_second_points or grant.price_per_item_points)
                            if grant is not None and billing_unit == BillingUnit.POINT
                            else (grant.price_per_second_cents or grant.price_per_item_cents)
                            if grant is not None else None
                        ),
                        config_override=(desired["config_override"] if desired["config_override"] is not None else grant.config_override if grant is not None else {}),
                        scope="company",
                        expected_release_snapshot=(expected_release_snapshots or {}).get(item.id),
                    )
                after = {
                    "configured": not (
                        grant is None and not desired["enabled"] and second_price is None and item_price is None
                    ),
                    "enabled": desired["enabled"],
                    "billing_unit": billing_unit.value,
                    "billing_version": billing_version,
                    "price_per_second_cents": (
                        second_price
                        if billing_unit == BillingUnit.CNY_CENT
                        else None
                    ),
                    "price_per_item_cents": (
                        item_price
                        if billing_unit == BillingUnit.CNY_CENT
                        else None
                    ),
                    "price_per_second_points": (
                        second_price if billing_unit == BillingUnit.POINT else None
                    ),
                    "price_per_item_points": (
                        item_price if billing_unit == BillingUnit.POINT else None
                    ),
                    "config_override": (
                        desired["config_override"]
                        if desired["config_override"] is not None
                        else ((grant.config_override or {}) if grant else {})
                    ),
                    "call_quota": (
                        desired["call_quota"]
                        if desired["call_quota_set"]
                        else getattr(grant, "call_quota", None)
                    ),
                    "concurrency_limit": (
                        desired["concurrency_limit"]
                        if desired["concurrency_limit_set"]
                        else getattr(grant, "concurrency_limit", None)
                    ),
                    "effective_at": (
                        desired["effective_at"]
                        if desired["effective_at_set"]
                        else getattr(grant, "effective_at", None)
                    ),
                    "expires_at": (
                        desired["expires_at"]
                        if desired["expires_at_set"]
                        else getattr(grant, "expires_at", None)
                    ),
                }
            else:
                if desired["enabled"] and not item.active:
                    raise ConflictError("cannot enable a retired resource")
                after = {
                    "configured": not (grant is None and not desired["enabled"]),
                    "enabled": desired["enabled"],
                    "config_override": (
                        desired["config_override"]
                        if desired["config_override"] is not None
                        else ((grant.config_override or {}) if grant else {})
                    ),
                    "call_quota": (
                        desired["call_quota"]
                        if desired["call_quota_set"]
                        else getattr(grant, "call_quota", None)
                    ),
                    "concurrency_limit": (
                        desired["concurrency_limit"]
                        if desired["concurrency_limit_set"]
                        else getattr(grant, "concurrency_limit", None)
                    ),
                    "effective_at": (
                        desired["effective_at"]
                        if desired["effective_at_set"]
                        else getattr(grant, "effective_at", None)
                    ),
                    "expires_at": (
                        desired["expires_at"]
                        if desired["expires_at_set"]
                        else getattr(grant, "expires_at", None)
                    ),
                }
            if after["call_quota"] is not None and int(after["call_quota"]) <= 0:
                raise ConflictError("call_quota must be positive")
            if (
                after["concurrency_limit"] is not None
                and int(after["concurrency_limit"]) <= 0
            ):
                raise ConflictError("concurrency_limit must be positive")
            effective_at = _utc(after["effective_at"])
            expires_at = _utc(after["expires_at"])
            if effective_at is not None and expires_at is not None and effective_at >= expires_at:
                raise ConflictError("effective_at must be before expires_at")
            before_comparable = {
                key: value
                for key, value in before.items()
                if key not in {"grant_id", "updated_at"}
            }
            operation = (
                "noop"
                if _canonical(before_comparable) == _canonical(after)
                else ("create" if grant is None and after["configured"] else "update")
            )
            if not after["configured"]:
                operation = "noop"
            catalog_version = (
                item.capability_version if kind == "model" else _iso(item.updated_at)
            )
            changes_out.append(
                {
                    "company_id": desired["company_id"],
                    "company_name": companies[desired["company_id"]].name,
                    "item_kind": kind,
                    "item_id": desired["item_id"],
                    "catalog_key": item.slug if kind == "model" else item.key,
                    "catalog_version": catalog_version,
                    "operation": operation,
                    "before": before,
                    "after": after,
                }
            )
        snapshot_payload = {
            "cells": changes_out,
            "company_versions": {
                company_id: _iso(companies[company_id].updated_at)
                for company_id in sorted(companies)
            },
        }
        snapshot = _sha256(snapshot_payload)
        return {
            "snapshot": snapshot,
            "total_cells": len(changes_out),
            "changed_cells": sum(item["operation"] != "noop" for item in changes_out),
            "created_cells": sum(item["operation"] == "create" for item in changes_out),
            "updated_cells": sum(item["operation"] == "update" for item in changes_out),
            "cells": changes_out,
        }

    @classmethod
    def _execution_identity(
        cls,
        *,
        expected_snapshot: str,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
        request_intent: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        normalized_reason = reason.strip()
        if len(normalized_reason) < 3 or len(normalized_reason) > 240:
            raise ConflictError("change reason must contain 3 to 240 characters")
        normalized_key = idempotency_key.strip()
        if not normalized_key or len(normalized_key) > 120:
            raise ConflictError("idempotency_key must contain 1 to 120 characters")
        canonical_intent = _canonical(dict(request_intent))
        if not isinstance(canonical_intent.get("operation"), str) or not (
            canonical_intent["operation"].strip()
        ):
            raise ConflictError("entitlement request intent requires an operation")
        request_hash = _sha256(
            {
                "actor_user_id": actor_user_id,
                "expected_snapshot": expected_snapshot,
                "reason": normalized_reason,
                "request_intent": canonical_intent,
            }
        )
        return normalized_reason, normalized_key, request_hash

    @classmethod
    def idempotent_replay(
        cls,
        session: Session,
        *,
        expected_snapshot: str,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
        request_intent: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        (
            _,
            normalized_key,
            request_hash,
        ) = cls._execution_identity(
            expected_snapshot=expected_snapshot,
            actor_user_id=actor_user_id,
            reason=reason,
            idempotency_key=idempotency_key,
            request_intent=request_intent,
        )
        journal = session.scalar(
            select(CompanyEntitlementBatchJournal).where(
                CompanyEntitlementBatchJournal.idempotency_key
                == normalized_key
            ).execution_options(populate_existing=True)
        )
        if journal is None:
            return None
        return cls._journal_result(
            journal,
            actor_user_id=actor_user_id,
            request_hash=request_hash,
            expected_snapshot=expected_snapshot,
        )

    @staticmethod
    def _journal_result(
        journal: CompanyEntitlementBatchJournal,
        *,
        actor_user_id: str,
        request_hash: str,
        expected_snapshot: str,
    ) -> dict[str, Any]:
        if journal.state == "frozen":
            raise ConflictError(
                "this idempotency_key belongs to a legacy entitlement batch; "
                "automatic replay or re-execution is forbidden and operator "
                "reconciliation is required"
            )
        if (
            journal.actor_user_id != actor_user_id
            or journal.request_sha256 != request_hash
            or journal.expected_snapshot != expected_snapshot
        ):
            raise ConflictError("idempotency_key is already used by another batch")
        if journal.state == "completed" and isinstance(
            journal.result_payload, dict
        ):
            stored = journal.result_payload
            return {**stored, "idempotent_replay": True}
        if journal.state == "pending" and journal.result_payload is None:
            raise ConflictError(
                "entitlement batch is pending or its commit outcome is unknown; "
                "automatic re-execution is forbidden and operator reconciliation "
                "is required"
            )
        raise ConflictError("stored entitlement batch journal is incomplete")

    @classmethod
    def _claim_execution(
        cls,
        session: Session,
        *,
        expected_snapshot: str,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
        request_intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        (
            normalized_reason,
            normalized_key,
            request_hash,
        ) = cls._execution_identity(
            expected_snapshot=expected_snapshot,
            actor_user_id=actor_user_id,
            reason=reason,
            idempotency_key=idempotency_key,
            request_intent=request_intent,
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
                CompanyEntitlementBatchJournal
            ).values(**values)
        elif dialect_name == "sqlite":
            insert_statement = sqlite_insert(
                CompanyEntitlementBatchJournal
            ).values(**values)
        else:  # pragma: no cover - production is PostgreSQL; tests use SQLite.
            raise RuntimeError("unsupported Platform database dialect")
        claimed_id = session.scalar(
            insert_statement.on_conflict_do_nothing(
                index_elements=["idempotency_key"]
            ).returning(CompanyEntitlementBatchJournal.id)
        )
        if claimed_id is not None:
            journal = session.get(
                CompanyEntitlementBatchJournal, claimed_id
            )
        else:
            journal = session.scalar(
                select(CompanyEntitlementBatchJournal)
                .where(
                    CompanyEntitlementBatchJournal.idempotency_key
                    == normalized_key
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        if journal is None:
            raise ConflictError(
                "entitlement batch idempotency journal is temporarily unavailable; "
                "retry without changing the request"
            )
        if claimed_id is None:
            return {
                "journal": journal,
                "normalized_reason": normalized_reason,
                "normalized_key": normalized_key,
                "request_hash": request_hash,
                "replay": cls._journal_result(
                    journal,
                    actor_user_id=actor_user_id,
                    request_hash=request_hash,
                    expected_snapshot=expected_snapshot,
                ),
            }
        return {
            "journal": journal,
            "normalized_reason": normalized_reason,
            "normalized_key": normalized_key,
            "request_hash": request_hash,
            "replay": None,
        }

    @classmethod
    def execute_changes(
        cls,
        session: Session,
        *,
        changes: Iterable[dict[str, Any]],
        expected_snapshot: str,
        actor_user_id: str,
        reason: str,
        request_id: str,
        idempotency_key: str,
        expected_release_snapshots: Mapping[
            str, Mapping[str, Any]
        ] | None = None,
        request_intent: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_changes: list[dict[str, Any]] | None = None
        if request_intent is None:
            # Direct service callers predate the HTTP intent contract. Keep
            # their idempotency stable by deriving a complete, immutable
            # intent once from their supplied cells. Production routes always
            # pass the original client intent explicitly.
            normalized_changes = cls._normalize_changes(changes)
            request_intent = {
                "operation": "service_batch",
                "cells": normalized_changes,
            }
        claim = cls._claim_execution(
            session,
            expected_snapshot=expected_snapshot,
            actor_user_id=actor_user_id,
            reason=reason,
            idempotency_key=idempotency_key,
            request_intent=request_intent,
        )
        replay = claim["replay"]
        if replay is not None:
            return replay
        journal = claim["journal"]
        normalized_reason = claim["normalized_reason"]
        normalized_key = claim["normalized_key"]
        request_hash = claim["request_hash"]
        if normalized_changes is None:
            normalized_changes = cls._normalize_changes(changes)

        preview = cls.preview_changes(
            session,
            changes=normalized_changes,
            lock_rows=True,
            expected_release_snapshots=expected_release_snapshots,
        )
        if preview["snapshot"] != expected_snapshot:
            raise ConflictError("entitlement matrix changed after preview; preview again")

        applied: list[dict[str, Any]] = []
        for cell in preview["cells"]:
            if cell["operation"] == "noop":
                continue
            after = cell["after"]
            if cell["item_kind"] == "model":
                grant = ModelGrantService.upsert_grant(
                    session,
                    company_id=cell["company_id"],
                    model_id=cell["item_id"],
                    enabled=after["enabled"],
                    price_per_second_cents=after["price_per_second_cents"],
                    price_per_item_cents=after["price_per_item_cents"],
                    price_per_second_points=after["price_per_second_points"],
                    price_per_item_points=after["price_per_item_points"],
                    config_override=after["config_override"],
                    call_quota=after["call_quota"],
                    concurrency_limit=after["concurrency_limit"],
                    effective_at=after["effective_at"],
                    expires_at=after["expires_at"],
                    expected_release_snapshot=(
                        (expected_release_snapshots or {}).get(
                            cell["item_id"]
                        )
                    ),
                )
            else:
                grant = ResourceGrantService.upsert_company_grant(
                    session,
                    company_id=cell["company_id"],
                    resource_id=cell["item_id"],
                    enabled=after["enabled"],
                    config_override=after["config_override"],
                    call_quota=after["call_quota"],
                    concurrency_limit=after["concurrency_limit"],
                    effective_at=after["effective_at"],
                    expires_at=after["expires_at"],
                )
            applied.append(
                {
                    "company_id": cell["company_id"],
                    "item_kind": cell["item_kind"],
                    "item_id": cell["item_id"],
                    "grant_id": grant.id,
                    "operation": cell["operation"],
                }
            )
        result = {
            "batch_id": normalized_key,
            "snapshot": expected_snapshot,
            "applied_cell_count": len(applied),
            "applied": applied,
            "idempotent_replay": False,
        }
        AuditService.append(
            session,
            actor_user_id=actor_user_id,
            action="company.entitlements.batch",
            target_type="entitlement_batch",
            target_id=normalized_key,
            before_summary={
                "reason": normalized_reason,
                "snapshot": expected_snapshot,
                "cells": [
                    {
                        "company_id": cell["company_id"],
                        "item_kind": cell["item_kind"],
                        "item_id": cell["item_id"],
                        "value": _canonical(cell["before"]),
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
                        "company_id": cell["company_id"],
                        "item_kind": cell["item_kind"],
                        "item_id": cell["item_id"],
                        "value": _canonical(cell["after"]),
                    }
                    for cell in preview["cells"]
                    if cell["operation"] != "noop"
                ],
                "result": result,
            },
            request_id=request_id,
        )
        journal.state = "completed"
        journal.result_payload = _canonical(result)
        journal.updated_at = utcnow()
        session.flush()
        return result

    @classmethod
    def changes_from_company(
        cls,
        session: Session,
        *,
        source_company_id: str,
        target_company_ids: Iterable[str],
        mode: CopyMode,
        include_models: bool = True,
        include_resources: bool = True,
    ) -> list[dict[str, Any]]:
        source_company = session.get(Company, source_company_id)
        if source_company is None:
            raise NotFoundError("source company does not exist")
        targets = sorted({item.strip() for item in target_company_ids if item.strip()})
        if source_company_id in targets:
            raise ConflictError("source company cannot also be a copy target")
        target_companies = {
            company.id: company
            for company in session.scalars(
                select(Company).where(Company.id.in_(targets))
            ).all()
        }
        if set(target_companies) != set(targets):
            raise NotFoundError("one or more target companies do not exist")
        if not targets:
            raise ConflictError("at least one target company is required")
        changes: list[dict[str, Any]] = []
        if include_models:
            source_unit, source_version = _company_billing_contract(source_company)
            mismatched_targets = [
                target
                for target in targets
                if _company_billing_contract(target_companies[target])
                != (source_unit, source_version)
            ]
            if mismatched_targets:
                raise ConflictError(
                    "model entitlement copy cannot cross billing versions"
                )
            source = {
                grant.model_id: grant
                for grant in session.scalars(
                    select(CompanyModelGrant).where(
                        CompanyModelGrant.company_id == source_company_id
                    )
                )
            }
            target_grants = list(
                session.scalars(
                    select(CompanyModelGrant).where(
                        CompanyModelGrant.company_id.in_(targets)
                    )
                ).all()
            )
            target_by_company: dict[str, dict[str, CompanyModelGrant]] = {
                target: {} for target in targets
            }
            for grant in target_grants:
                target_by_company[grant.company_id][grant.model_id] = grant
            item_ids = set(source)
            if mode == "replace":
                item_ids.update(grant.model_id for grant in target_grants)
            for target in targets:
                for item_id in sorted(item_ids):
                    source_grant = source.get(item_id)
                    target_grant = target_by_company[target].get(item_id)
                    if source_grant is not None:
                        changes.append(
                            {
                                "company_id": target,
                                "item_kind": "model",
                                "item_id": item_id,
                                "enabled": source_grant.enabled,
                                "billing_unit": source_unit.value,
                                "billing_version": source_version,
                                "price_per_second_cents": source_grant.price_per_second_cents,
                                "price_per_item_cents": source_grant.price_per_item_cents,
                                "price_per_second_points": source_grant.price_per_second_points,
                                "price_per_item_points": source_grant.price_per_item_points,
                                "config_override": source_grant.config_override or {},
                                "call_quota": source_grant.call_quota,
                                "concurrency_limit": source_grant.concurrency_limit,
                                "effective_at": getattr(source_grant, "effective_at", None),
                                "expires_at": getattr(source_grant, "expires_at", None),
                            }
                        )
                    elif mode == "replace" and target_grant is not None:
                        target_unit, target_version = _company_billing_contract(
                            target_companies[target]
                        )
                        changes.append(
                            {
                                "company_id": target,
                                "item_kind": "model",
                                "item_id": item_id,
                                "enabled": False,
                                "billing_unit": target_unit.value,
                                "billing_version": target_version,
                                "price_per_second_cents": target_grant.price_per_second_cents,
                                "price_per_item_cents": target_grant.price_per_item_cents,
                                "price_per_second_points": target_grant.price_per_second_points,
                                "price_per_item_points": target_grant.price_per_item_points,
                                "config_override": target_grant.config_override or {},
                                "call_quota": target_grant.call_quota,
                                "concurrency_limit": target_grant.concurrency_limit,
                            }
                        )
        if include_resources:
            source = {
                grant.resource_id: grant
                for grant in session.scalars(
                    select(CompanyResourceGrant).where(
                        CompanyResourceGrant.company_id == source_company_id
                    )
                )
            }
            target_grants = list(
                session.scalars(
                    select(CompanyResourceGrant).where(
                        CompanyResourceGrant.company_id.in_(targets)
                    )
                ).all()
            )
            target_by_company = {target: {} for target in targets}
            for grant in target_grants:
                target_by_company[grant.company_id][grant.resource_id] = grant
            item_ids = set(source)
            if mode == "replace":
                item_ids.update(grant.resource_id for grant in target_grants)
            for target in targets:
                for item_id in sorted(item_ids):
                    source_grant = source.get(item_id)
                    target_grant = target_by_company[target].get(item_id)
                    if source_grant is not None:
                        changes.append(
                            {
                                "company_id": target,
                                "item_kind": "resource",
                                "item_id": item_id,
                                "enabled": source_grant.enabled,
                                "config_override": source_grant.config_override or {},
                                "call_quota": source_grant.call_quota,
                                "concurrency_limit": source_grant.concurrency_limit,
                                "effective_at": getattr(source_grant, "effective_at", None),
                                "expires_at": getattr(source_grant, "expires_at", None),
                            }
                        )
                    elif mode == "replace" and target_grant is not None:
                        changes.append(
                            {
                                "company_id": target,
                                "item_kind": "resource",
                                "item_id": item_id,
                                "enabled": False,
                                "config_override": target_grant.config_override or {},
                                "call_quota": target_grant.call_quota,
                                "concurrency_limit": target_grant.concurrency_limit,
                            }
                        )
        if len(changes) > MAX_BATCH_CELLS:
            raise ConflictError(f"copy expands beyond {MAX_BATCH_CELLS} entitlement cells")
        if not changes:
            raise ConflictError("source company has no selected entitlement configuration")
        return changes

    @classmethod
    def changes_from_template(
        cls,
        session: Session,
        *,
        template_cells: Iterable[dict[str, Any]],
        target_company_ids: Iterable[str],
        mode: CopyMode,
    ) -> list[dict[str, Any]]:
        targets = sorted({item.strip() for item in target_company_ids if item.strip()})
        if not targets:
            raise ConflictError("at least one target company is required")
        target_companies = {
            company.id: company
            for company in session.scalars(
                select(Company).where(Company.id.in_(targets))
            ).all()
        }
        if set(target_companies) != set(targets):
            raise NotFoundError("one or more target companies do not exist")
        specs = [dict(cell) for cell in template_cells]
        if not specs:
            raise ConflictError("template must contain at least one entitlement cell")
        model_contracts: set[tuple[str, int]] = set()
        for spec in specs:
            if spec.get("item_kind") != "model":
                continue
            declared_unit = spec.get("billing_unit")
            declared_version = spec.get("billing_version")
            has_point_price = any(
                spec.get(field) is not None
                for field in (
                    "price_per_second_points",
                    "price_per_item_points",
                )
            )
            if declared_unit is None and declared_version is None:
                if has_point_price:
                    raise ConflictError(
                        "point entitlement templates require explicit POINT v2 billing evidence"
                    )
                declared_unit = BillingUnit.CNY_CENT.value
                declared_version = 1
                spec["billing_unit"] = declared_unit
                spec["billing_version"] = declared_version
            elif declared_unit is None or declared_version is None:
                raise ConflictError(
                    "billing_unit and billing_version must be supplied together"
                )
            declared_unit = _enum_value(declared_unit)
            try:
                declared_version = int(declared_version)
            except (TypeError, ValueError) as exc:
                raise ConflictError("template billing version is invalid") from exc
            if (declared_unit, declared_version) not in {
                (BillingUnit.CNY_CENT.value, 1),
                (BillingUnit.POINT.value, 2),
            }:
                raise ConflictError("template billing contract is invalid")
            spec["billing_unit"] = declared_unit
            spec["billing_version"] = declared_version
            model_contracts.add((declared_unit, declared_version))
        if len(model_contracts) > 1:
            raise ConflictError(
                "one entitlement template cannot mix billing versions"
            )
        if model_contracts:
            template_contract = next(iter(model_contracts))
            if any(
                (
                    _company_billing_contract(target_companies[target])[0].value,
                    _company_billing_contract(target_companies[target])[1],
                )
                != template_contract
                for target in targets
            ):
                raise ConflictError(
                    "model entitlement template cannot cross billing versions"
                )
        changes = [
            {**spec, "company_id": company_id}
            for company_id in targets
            for spec in specs
        ]
        if mode == "replace":
            selected_model_ids = {
                spec["item_id"] for spec in specs if spec.get("item_kind") == "model"
            }
            selected_resource_ids = {
                spec["item_id"] for spec in specs if spec.get("item_kind") == "resource"
            }
            template_kinds = {spec.get("item_kind") for spec in specs}
            if "model" in template_kinds:
                for grant in session.scalars(
                    select(CompanyModelGrant).where(
                        CompanyModelGrant.company_id.in_(targets),
                        ~CompanyModelGrant.model_id.in_(selected_model_ids),
                    )
                ):
                    changes.append(
                        {
                            "company_id": grant.company_id,
                            "item_kind": "model",
                            "item_id": grant.model_id,
                            "enabled": False,
                            "billing_unit": _company_billing_contract(
                                target_companies[grant.company_id]
                            )[0].value,
                            "billing_version": _company_billing_contract(
                                target_companies[grant.company_id]
                            )[1],
                            "price_per_second_cents": grant.price_per_second_cents,
                            "price_per_item_cents": grant.price_per_item_cents,
                            "price_per_second_points": grant.price_per_second_points,
                            "price_per_item_points": grant.price_per_item_points,
                            "config_override": grant.config_override or {},
                            "call_quota": grant.call_quota,
                            "concurrency_limit": grant.concurrency_limit,
                        }
                    )
            if "resource" in template_kinds:
                for grant in session.scalars(
                    select(CompanyResourceGrant).where(
                        CompanyResourceGrant.company_id.in_(targets),
                        ~CompanyResourceGrant.resource_id.in_(selected_resource_ids),
                    )
                ):
                    changes.append(
                        {
                            "company_id": grant.company_id,
                            "item_kind": "resource",
                            "item_id": grant.resource_id,
                            "enabled": False,
                            "config_override": grant.config_override or {},
                            "call_quota": grant.call_quota,
                            "concurrency_limit": grant.concurrency_limit,
                        }
                    )
        return cls._normalize_changes(changes)
