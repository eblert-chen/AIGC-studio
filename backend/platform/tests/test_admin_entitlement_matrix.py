from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select

from platform_api.models import (
    BillingUnit,
    Company,
    CompanyEntitlementBatchJournal,
    CompanyModelGrant,
    CompanyResourceGrant,
    ModelDefinition,
    ResourceDefinition,
    ResourceKind,
    User,
    AuditLog,
)
from platform_api.services.admin_entitlements import AdminEntitlementService
from platform_api.services.errors import ConflictError
from platform_api.services.company_points_billing import CompanyPointBillingService

from .test_model_capability_v1_contract import (
    _request_with_fixture_distribution_evidence,
)
from .test_server_pricing import add_model_and_grant, recharge
from .test_model_commercial_release import _admin_headers, _commercial_body, _prepare_video_draft


def _seed_entitlements(session):
    admin = User(
        email="matrix-admin@example.com",
        display_name="Matrix Admin",
        is_platform_admin=True,
    )
    source = Company(name="Source Company")
    target = Company(name="Target Company")
    feature = ResourceDefinition(
        key="feature.auto_publish",
        kind=ResourceKind.FEATURE,
        display_name="Auto publish",
        description="",
        active=True,
    )
    session.add_all((admin, source, target, feature))
    session.flush()
    return admin, source, target, feature


def test_matrix_coverage_preview_execute_and_idempotent_replay(app):
    with app.state.session_factory() as session:
        admin, _, target, feature = _seed_entitlements(session)
        session.commit()

        change = {
            "company_id": target.id,
            "item_kind": "resource",
            "item_id": feature.id,
            "enabled": True,
            "config_override": {"daily_limit": 10},
            "call_quota": 20,
            "concurrency_limit": 3,
            "effective_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "expires_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        }
        preview = AdminEntitlementService.preview_changes(
            session, changes=[change]
        )
        assert preview["changed_cells"] == 1
        assert preview["cells"][0]["operation"] == "create"

        result = AdminEntitlementService.execute_changes(
            session,
            changes=[change],
            expected_snapshot=preview["snapshot"],
            actor_user_id=admin.id,
            reason="Enable the contracted publishing feature",
            request_id="matrix-request-1",
            idempotency_key="matrix-batch-1",
        )
        session.commit()
        assert result["applied_cell_count"] == 1
        grant = session.query(CompanyResourceGrant).filter_by(
            company_id=target.id, resource_id=feature.id
        ).one()
        assert grant.enabled is True
        assert grant.config_override == {"daily_limit": 10}
        assert grant.call_quota == 20
        assert grant.concurrency_limit == 3
        assert grant.effective_at is not None
        assert grant.expires_at is not None

        replay = AdminEntitlementService.execute_changes(
            session,
            changes=[change],
            expected_snapshot=preview["snapshot"],
            actor_user_id=admin.id,
            reason="Enable the contracted publishing feature",
            request_id="matrix-request-2",
            idempotency_key="matrix-batch-1",
        )
        assert replay["idempotent_replay"] is True

        matrix = AdminEntitlementService.matrix(
            session,
            company_page=1,
            company_page_size=10,
            catalog_page=1,
            catalog_page_size=10,
            catalog_kind="feature",
            now=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )
        target_row = next(row for row in matrix["rows"] if row["company_id"] == target.id)
        assert target_row["cells"][0]["state"] == "enabled"
        assert target_row["cells"][0]["call_quota"] == 20
        assert target_row["cells"][0]["concurrency_limit"] == 3
        coverage = AdminEntitlementService.coverage(
            session, now=datetime(2026, 8, 7, tzinfo=timezone.utc)
        )
        item = next(item for item in coverage["items"] if item["item_id"] == feature.id)
        assert item["enabled_company_count"] == 1
        assert item["unconfigured_company_count"] == 1


def test_committed_pending_entitlement_journal_never_auto_reexecutes(app):
    with app.state.session_factory() as session:
        admin, _, target, feature = _seed_entitlements(session)
        session.commit()
        change = {
            "company_id": target.id,
            "item_kind": "resource",
            "item_id": feature.id,
            "enabled": True,
            "config_override": {},
        }
        preview = AdminEntitlementService.preview_changes(session, changes=[change])
        intent = {"operation": "matrix_batch", "cells": [change]}
        reason = "Exercise unknown journal outcome recovery"
        key = "matrix-persisted-pending-1"
        _, normalized_key, request_hash = AdminEntitlementService._execution_identity(
            expected_snapshot=preview["snapshot"],
            actor_user_id=admin.id,
            reason=reason,
            idempotency_key=key,
            request_intent=intent,
        )
        session.add(
            CompanyEntitlementBatchJournal(
                idempotency_key=normalized_key,
                actor_user_id=admin.id,
                request_sha256=request_hash,
                expected_snapshot=preview["snapshot"],
                state="pending",
                result_payload=None,
            )
        )
        session.commit()

        with pytest.raises(ConflictError, match="outcome is unknown"):
            AdminEntitlementService.execute_changes(
                session,
                changes=[change],
                expected_snapshot=preview["snapshot"],
                actor_user_id=admin.id,
                reason=reason,
                request_id="matrix-persisted-pending-request",
                idempotency_key=key,
                request_intent=intent,
            )
        assert session.scalar(
            select(func.count())
            .select_from(CompanyResourceGrant)
            .where(
                CompanyResourceGrant.company_id == target.id,
                CompanyResourceGrant.resource_id == feature.id,
            )
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "company.entitlements.batch",
                AuditLog.target_id == key,
            )
        ) == 0


def test_snapshot_conflict_copy_and_template_replace_semantics(app):
    with app.state.session_factory() as session:
        admin, source, target, feature = _seed_entitlements(session)
        source_grant = CompanyResourceGrant(
            company_id=source.id,
            resource_id=feature.id,
            enabled=True,
            config_override={"scope": "source"},
            call_quota=50,
            concurrency_limit=4,
            effective_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        session.add(source_grant)
        session.commit()

        copied = AdminEntitlementService.changes_from_company(
            session,
            source_company_id=source.id,
            target_company_ids=[target.id],
            mode="replace",
        )
        copy_preview = AdminEntitlementService.preview_changes(
            session, changes=copied
        )
        assert copy_preview["cells"][0]["after"]["config_override"] == {
            "scope": "source"
        }
        assert copy_preview["cells"][0]["after"]["call_quota"] == 50
        assert copy_preview["cells"][0]["after"]["concurrency_limit"] == 4

        # A concurrent grant edit invalidates the exact preview snapshot.
        session.add(
            CompanyResourceGrant(
                company_id=target.id,
                resource_id=feature.id,
                enabled=False,
                config_override={"scope": "concurrent"},
            )
        )
        session.commit()
        with pytest.raises(ConflictError, match="changed after preview"):
            AdminEntitlementService.execute_changes(
                session,
                changes=copied,
                expected_snapshot=copy_preview["snapshot"],
                actor_user_id=admin.id,
                reason="Copy source company configuration",
                request_id="matrix-request-stale",
                idempotency_key="matrix-batch-stale",
            )

        template_changes = AdminEntitlementService.changes_from_template(
            session,
            template_cells=[
                {
                    "item_kind": "resource",
                    "item_id": feature.id,
                    "enabled": True,
                    "config_override": {"scope": "template"},
                    "call_quota": 12,
                    "concurrency_limit": 2,
                }
            ],
            target_company_ids=[target.id],
            mode="replace",
        )
        template_preview = AdminEntitlementService.preview_changes(
            session, changes=template_changes
        )
        assert template_preview["cells"][0]["operation"] == "update"
        assert template_preview["cells"][0]["after"]["config_override"] == {
            "scope": "template"
        }
        assert template_preview["cells"][0]["after"]["call_quota"] == 12
        assert template_preview["cells"][0]["after"]["concurrency_limit"] == 2


def test_point_company_batch_and_copy_contracts_are_unit_safe(app, client):
    with app.state.session_factory() as session:
        admin, source, target, _ = _seed_entitlements(session)
        source.billing_version = 2
        target.billing_version = 2
        legacy = Company(name="Legacy cents target", billing_version=1)
        model = ModelDefinition(
            slug="point-entitlement-matrix-model",
            display_name="Point entitlement matrix model",
            provider_key="point-entitlement-test",
            billing_mode="per_item",
        )
        session.add_all((legacy, model))
        session.commit()

        change = {
            "company_id": target.id,
            "item_kind": "model",
            "item_id": model.id,
            "enabled": False,
            "billing_unit": BillingUnit.POINT.value,
            "billing_version": 2,
            "price_per_item_points": 17,
            "config_override": {},
        }
        preview = AdminEntitlementService.preview_changes(
            session,
            changes=[change],
        )
        after = preview["cells"][0]["after"]
        assert after["billing_unit"] == BillingUnit.POINT.value
        assert after["billing_version"] == 2
        assert after["price_per_item_points"] == 17
        assert after["price_per_item_cents"] is None

        AdminEntitlementService.execute_changes(
            session,
            changes=[change],
            expected_snapshot=preview["snapshot"],
            actor_user_id=admin.id,
            reason="Configure the point price through the batch matrix",
            request_id="point-entitlement-matrix-request",
            idempotency_key="point-entitlement-matrix-batch",
        )
        session.commit()
        grant = session.scalar(
            select(CompanyModelGrant).where(
                CompanyModelGrant.company_id == target.id,
                CompanyModelGrant.model_id == model.id,
            )
        )
        assert grant is not None
        assert grant.price_per_item_points == 17
        assert grant.price_per_item_cents is None

        copied = AdminEntitlementService.changes_from_company(
            session,
            source_company_id=target.id,
            target_company_ids=[source.id],
            mode="replace",
        )
        copied_model = next(
            cell for cell in copied if cell["item_kind"] == "model"
        )
        assert copied_model["billing_unit"] == BillingUnit.POINT.value
        assert copied_model["billing_version"] == 2
        assert copied_model["price_per_item_points"] == 17

        with pytest.raises(ConflictError, match="cross billing versions"):
            AdminEntitlementService.changes_from_company(
                session,
                source_company_id=target.id,
                target_company_ids=[legacy.id],
                mode="replace",
            )
        with pytest.raises(ConflictError, match="cross billing versions"):
            AdminEntitlementService.changes_from_template(
                session,
                template_cells=[
                    {
                        "item_kind": "model",
                        "item_id": model.id,
                        "enabled": False,
                        "billing_unit": BillingUnit.POINT.value,
                        "billing_version": 2,
                        "price_per_item_points": 17,
                    }
                ],
                target_company_ids=[legacy.id],
                mode="replace",
            )

        with pytest.raises(ConflictError, match="explicit POINT v2"):
            AdminEntitlementService.preview_changes(
                session,
                changes=[
                    {
                        **change,
                        "billing_unit": None,
                        "billing_version": None,
                    }
                ],
            )

        http_preview = client.post(
            "/api/v1/platform-admin/entitlements/batch/preview",
            headers={"X-Platform-Admin-User-ID": admin.id},
            json={
                "changes": [
                    {
                        **change,
                        "price_per_item_points": 19,
                    }
                ]
            },
        )
        assert http_preview.status_code == 200, http_preview.text
        http_after = http_preview.json()["cells"][0]["after"]
        assert http_after["billing_unit"] == BillingUnit.POINT.value
        assert http_after["billing_version"] == 2
        assert http_after["price_per_item_points"] == 19
        assert http_after["price_per_item_cents"] is None


def test_schedule_quota_and_concurrency_fail_closed_at_task_admission(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    model_id = add_model_and_grant(
        app,
        company_id,
        slug="policy-admission-model",
        second_price=None,
        item_price=100,
        capability_config={"max_outputs": 1},
    )
    now = datetime.now(timezone.utc)
    with app.state.session_factory.begin() as session:
        grant = session.query(CompanyModelGrant).filter_by(
            company_id=company_id, model_id=model_id
        ).one()
        grant.effective_at = now + timedelta(hours=1)
        grant.expires_at = now + timedelta(days=1)
        grant.call_quota = 1
    recharge(client, company_id, tenant_headers)

    unavailable = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "policy-before-effective",
            "request_payload": {"prompt": "future", "output_count": 1},
        },
    )
    assert unavailable.status_code == 404

    with app.state.session_factory.begin() as session:
        grant = session.query(CompanyModelGrant).filter_by(
            company_id=company_id, model_id=model_id
        ).one()
        grant.effective_at = now - timedelta(hours=1)

    first = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "policy-first-call",
            "request_payload": {"prompt": "first", "output_count": 1},
        },
    )
    assert first.status_code == 201, first.text
    exhausted = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "policy-quota-exhausted",
            "request_payload": {"prompt": "second", "output_count": 1},
        },
    )
    assert exhausted.status_code == 403

    with app.state.session_factory.begin() as session:
        grant = session.query(CompanyModelGrant).filter_by(
            company_id=company_id, model_id=model_id
        ).one()
        grant.call_quota = None
        grant.concurrency_limit = 1
    concurrent = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "policy-concurrency-blocked",
            "request_payload": {"prompt": "third", "output_count": 1},
        },
    )
    assert concurrent.status_code == 403


def test_single_company_grant_apis_round_trip_entitlement_policy(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    admin_headers = _admin_headers(client, "single-policy-admin")
    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.migrate(session, company_id=company_id, expected_available_cents=0, idempotency_key="single-policy-points")
    model = _prepare_video_draft(app, client, admin_headers)
    model_id = model["id"]
    plan = client.put(f"/api/v1/platform-admin/models/{model_id}/commercial-release-plan", headers=admin_headers, json=_commercial_body(model=model))
    assert plan.status_code == 200, plan.text
    release = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=admin_headers)
    assert release.status_code == 200 and release.json()["released_count"] == 1, release.text
    now = datetime.now(timezone.utc)
    effective_at = now - timedelta(minutes=5)
    expires_at = now + timedelta(days=10)
    with app.state.session_factory.begin() as session:
        resource = ResourceDefinition(
            key="feature.single_policy",
            kind=ResourceKind.FEATURE,
            display_name="Single policy feature",
            description="",
            active=True,
        )
        session.add(resource)
        session.flush()
        resource_id = resource.id
    grant_version = next(
        item["updated_at"]
        for item in client.get(
            f"/api/v1/companies/{company_id}/model-grants",
            headers=tenant_headers,
        ).json()
        if item["model_id"] == model_id
    )

    model_response = _request_with_fixture_distribution_evidence(
        client,
        model_id,
        lambda: client.put(
            f"/api/v1/platform-admin/companies/{company_id}/model-grants",
            headers=admin_headers,
            json={
                "model_id": model_id,
                "expected_updated_at": grant_version,
                "enabled": True,
                "price_per_second_points": 25,
                "config_override": {},
                "call_quota": 80,
                "concurrency_limit": 4,
                "effective_at": effective_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
        ),
    )
    assert model_response.status_code == 200, model_response.text
    assert model_response.json()["call_quota"] == 80
    assert model_response.json()["concurrency_limit"] == 4
    assert model_response.json()["effective_at"] is not None
    assert model_response.json()["expires_at"] is not None

    resource_response = client.put(
        f"/api/v1/platform-admin/companies/{company_id}/resources/{resource_id}",
        headers=admin_headers,
        json={
            "enabled": True,
            "config_override": {"scope": "contract"},
            "call_quota": 40,
            "concurrency_limit": 2,
            "effective_at": effective_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
    )
    assert resource_response.status_code == 200, resource_response.text
    assert resource_response.json()["call_quota"] == 40
    assert resource_response.json()["concurrency_limit"] == 2

    entitlements = client.get(
        f"/api/v1/platform-admin/companies/{company_id}/entitlements",
        headers=admin_headers,
    )
    assert entitlements.status_code == 200, entitlements.text
    model_row = next(
        item for item in entitlements.json()["models"] if item["model_id"] == model_id
    )
    resource_row = next(
        item
        for item in entitlements.json()["resources"]
        if item["resource_id"] == resource_id
    )
    assert model_row["call_quota"] == 80
    assert model_row["concurrency_limit"] == 4
    assert resource_row["call_quota"] == 40
    assert resource_row["concurrency_limit"] == 2

    available_models = client.get(
        f"/api/v1/companies/{company_id}/models", headers=tenant_headers
    )
    assert available_models.status_code == 200, available_models.text
    available_model = next(
        item for item in available_models.json() if item["id"] == model_id
    )
    assert available_model["call_quota"] == 80
    assert available_model["concurrency_limit"] == 4
    available_resources = client.get(
        f"/api/v1/companies/{company_id}/resources", headers=tenant_headers
    )
    assert available_resources.status_code == 200, available_resources.text
    available_resource = next(
        item for item in available_resources.json() if item["id"] == resource_id
    )
    assert available_resource["call_quota"] == 40
    assert available_resource["concurrency_limit"] == 2

    invalid_schedule = client.put(
        f"/api/v1/platform-admin/companies/{company_id}/resources/{resource_id}",
        headers=admin_headers,
        json={
            "enabled": True,
            "effective_at": expires_at.isoformat(),
            "expires_at": effective_at.isoformat(),
        },
    )
    assert invalid_schedule.status_code == 422


def test_0025_entitlement_policy_migration_adds_and_removes_columns(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "entitlement-policy.db"
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{database_path.as_posix()}"
    )
    command.upgrade(config, "0024_platform_admin_access")
    command.upgrade(config, "0025_entitlement_policy")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table_name in ("company_model_grants", "company_resource_grants"):
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert {
                "call_quota",
                "concurrency_limit",
                "effective_at",
                "expires_at",
            }.issubset(columns)
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0025_entitlement_policy"
    engine.dispose()

    command.downgrade(config, "0024_platform_admin_access")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("company_model_grants")
        }
        assert "call_quota" not in columns
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0024_platform_admin_access"
    engine.dispose()
