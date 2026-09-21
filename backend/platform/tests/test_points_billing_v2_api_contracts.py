from __future__ import annotations

import pytest

from platform_api.models import (
    BillingUnit,
    Company,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelDefinition,
    TaskStatus,
    User,
    WalletAccount,
)
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.dashboard import DashboardService
from platform_api.services.errors import ConflictError

from .test_personal_workspace import (
    _create_and_settle,
    _personal_user,
    _retail_model,
)
from .test_points_billing_v2_invariants import _point_task


def _assert_point_v2(payload: dict) -> None:
    assert payload["billing_unit"] == "POINT"
    assert payload["billing_version"] == 2


def _seed_mixed_company_billing_history(app, *, company_id: str, user_id: str) -> None:
    """Create one reconciled legacy settlement, migrate, then settle one point task."""

    with app.state.session_factory.begin() as session:
        company = session.get(Company, company_id)
        user = session.get(User, user_id)
        wallet = session.get(WalletAccount, company_id)
        assert company is not None and user is not None and wallet is not None

        model = ModelDefinition(
            slug=f"mixed-report-{company_id}",
            display_name="Mixed report model",
            provider_key="points-v2-report-test",
            billing_mode="per_item",
        )
        session.add(model)
        session.flush()

        old_task = GenerationTask(
            company_id=company_id,
            personal_workspace_id=None,
            user_id=user_id,
            model_id=model.id,
            idempotency_key="mixed-report-cent-task",
            request_fingerprint="c" * 64,
            status=TaskStatus.SUCCEEDED,
            request_payload={"prompt": "legacy cents", "output_count": 1},
            billing_unit=BillingUnit.CNY_CENT,
            billing_version=1,
            quote_cents=5,
            quote_points=None,
            pricing_snapshot={
                "schema_version": 1,
                "billing_unit": BillingUnit.CNY_CENT.value,
                "billing_version": 1,
                "mode": "per_item",
                "unit_price_cents": 5,
                "quantity": 1,
                "quote_cents": 5,
            },
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=5,
            actual_cost_points=None,
        )
        session.add(old_task)
        session.flush()

        # The wallet and append-only legacy ledger must agree before migration.
        wallet.available_cents = 15
        session.add_all(
            [
                LedgerEntry(
                    company_id=company_id,
                    kind=LedgerKind.RECHARGE,
                    amount_cents=20,
                    available_delta_cents=20,
                    reserved_delta_cents=0,
                    idempotency_key="mixed-report-cent-recharge",
                    task_id=None,
                    note="legacy opening",
                ),
                LedgerEntry(
                    company_id=company_id,
                    kind=LedgerKind.RESERVE,
                    amount_cents=5,
                    available_delta_cents=-5,
                    reserved_delta_cents=5,
                    idempotency_key="mixed-report-cent-reserve",
                    task_id=old_task.id,
                ),
                LedgerEntry(
                    company_id=company_id,
                    kind=LedgerKind.SETTLE,
                    amount_cents=5,
                    available_delta_cents=0,
                    reserved_delta_cents=-5,
                    idempotency_key="mixed-report-cent-settle",
                    task_id=old_task.id,
                ),
            ]
        )
        session.flush()

        CompanyPointBillingService.migrate(
            session,
            company_id=company_id,
            expected_available_cents=15,
            idempotency_key="mixed-report-migration",
        )
        point_task = _point_task(
            company=company,
            user=user,
            model=model,
            quote_points=1,
            key="mixed-report-point-task",
        )
        point_task.pricing_snapshot = {
            "schema_version": 2,
            "billing_unit": BillingUnit.POINT.value,
            "billing_version": 2,
            "mode": "per_item",
            "unit_price_points": 1,
            "quantity": 1,
            "quote_points": 1,
            "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
        }
        session.add(point_task)
        session.flush()
        CompanyPointBillingService.reserve(
            session,
            company_id=company_id,
            task_id=point_task.id,
            amount_points=1,
            idempotency_key="mixed-report-point-reserve",
        )
        CompanyPointBillingService.settle_success(
            session,
            company_id=company_id,
            task_id=point_task.id,
            actual_cost_points=1,
            idempotency_key="mixed-report-point-settle",
        )


def test_migration_reports_keep_cents_and_points_separate_and_versioned(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    company_id = tenant["company_id"]
    _seed_mixed_company_billing_history(
        app,
        company_id=company_id,
        user_id=tenant["user_id"],
    )

    tasks_response = client.get(
        f"/api/v1/companies/{company_id}/tasks",
        headers=tenant_headers,
    )
    assert tasks_response.status_code == 200, tasks_response.text
    point_task = next(
        item
        for item in tasks_response.json()
        if item["billing_unit"] == BillingUnit.POINT.value
    )
    assert point_task["billing_version"] == 2
    assert point_task["quote_cents"] is None
    assert point_task["quote_points"] == 1
    assert point_task["reserved_cents"] == 0
    assert point_task["reserved_points"] == 0
    assert point_task["actual_cost_cents"] is None
    assert point_task["actual_cost_points"] == 1

    with app.state.session_factory() as session:
        dashboard = DashboardService.build(session, page=1, page_size=100)
    dashboard_row = next(
        item for item in dashboard["companies"] if item["company_id"] == company_id
    )
    assert dashboard_row["billing_unit"] == BillingUnit.POINT
    assert dashboard_row["billing_version"] == 2
    assert dashboard_row["recharge_points"] == 2
    assert dashboard_row["consumption_points"] == 1
    assert dashboard_row["available_points"] == 1
    assert dashboard_row["reserved_points"] == 0
    assert dashboard["platform_recharge_points"] >= 2
    assert dashboard["platform_consumption_points"] >= 1
    assert dashboard["unattributed_point_settlement_count"] >= 1
    assert dashboard["revenue_reconciliation_status"] == "incomplete"
    assert dashboard["finance_status"] == "incomplete"
    assert dashboard["gross_profit_cents"] is None

    contracts = (
        (
            f"/api/v1/companies/{company_id}/reports/tasks",
            "total_actual_cost_cents",
            5,
            "total_actual_cost_points",
            1,
        ),
        (
            f"/api/v1/companies/{company_id}/reports/consumption",
            "total_amount_cents",
            5,
            "total_amount_points",
            1,
        ),
        (
            f"/api/v1/companies/{company_id}/wallet/recharges",
            "total_amount_cents",
            20,
            "total_amount_points",
            2,
        ),
    )
    for path, cents_key, cents_total, points_key, points_total in contracts:
        response = client.get(path, headers=tenant_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert body["billing_unit"] == "MIXED"
        assert body["billing_version"] is None
        assert body[cents_key] == cents_total
        assert body[points_key] == points_total
        assert {
            (item["billing_unit"], item["billing_version"])
            for item in body["items"]
        } == {("CNY_CENT", 1), ("POINT", 2)}
        for item in body["items"]:
            if item["billing_unit"] == "CNY_CENT":
                assert item.get("amount_points") is None
                assert item.get("quote_points") is None
                assert item.get("actual_cost_points") is None
            else:
                assert item.get("amount_cents") is None
                assert item.get("quote_cents") is None
                assert item.get("actual_cost_cents") is None

        empty = client.get(
            path,
            headers=tenant_headers,
            params={"start_time": "2100-01-01T00:00:00Z"},
        )
        assert empty.status_code == 200, empty.text
        empty_body = empty.json()
        assert empty_body["total"] == 0
        assert empty_body["billing_unit"] == "POINT"
        assert empty_body["billing_version"] == 2
        assert empty_body[cents_key] == 0
        assert empty_body[points_key] == 0


def test_personal_wallet_model_task_and_artwork_explicitly_declare_point_v2(
    app,
    client,
) -> None:
    user_id = _personal_user(app, "explicit-point-v2")
    headers = {"X-User-ID": user_id}
    model_id = _retail_model(app)

    me = client.get("/api/v1/personal/me", headers=headers)
    wallet_before = client.get("/api/v1/personal/wallet", headers=headers)
    models = client.get("/api/v1/personal/models", headers=headers)
    assert me.status_code == wallet_before.status_code == models.status_code == 200
    _assert_point_v2(me.json())
    _assert_point_v2(wallet_before.json())
    assert models.json()
    assert all(
        item["billing_unit"] == "POINT" and item["billing_version"] == 2
        for item in models.json()
    )

    created = _create_and_settle(app, client, headers, model_id)
    _assert_point_v2(created)
    assert "quote_cents" not in created

    wallet = client.get("/api/v1/personal/wallet", headers=headers)
    tasks = client.get("/api/v1/personal/tasks", headers=headers)
    artworks = client.get("/api/v1/personal/artworks", headers=headers)
    assert wallet.status_code == tasks.status_code == artworks.status_code == 200
    _assert_point_v2(wallet.json())
    _assert_point_v2(tasks.json())
    _assert_point_v2(artworks.json())
    assert tasks.json()["items"] and artworks.json()["items"]
    _assert_point_v2(tasks.json()["items"][0])
    _assert_point_v2(artworks.json()["items"][0])
    assert "quote_cents" not in tasks.json()["items"][0]
    assert "actual_cost_cents" not in artworks.json()["items"][0]


def test_dashboard_fails_closed_when_company_wallet_projection_is_missing(
    app,
    tenant,
) -> None:
    with app.state.session_factory.begin() as session:
        company = session.get(Company, tenant["company_id"])
        assert company is not None
        company.billing_version = 2

    with app.state.session_factory() as session:
        with pytest.raises(ConflictError, match="积分钱包投影不一致"):
            DashboardService.build(session, page=1, page_size=100)
