from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from platform_api.models import (
    BillingUnit,
    ChannelCostEntry,
    ChannelCostSource,
    ChannelType,
    Company,
    CompanyPointLedgerEntry,
    CompanyPointWalletAccount,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelDefinition,
    PointLedgerKind,
    TaskStatus,
    User,
    WalletAccount,
)
from platform_api.services.admin_analytics import AdminAnalyticsService
from platform_api.services.errors import ConflictError


def _task(
    *,
    company: Company,
    user: User,
    model: ModelDefinition,
    suffix: str,
    status: TaskStatus,
    created_at: datetime,
    point_billing: bool,
    reserved: int = 0,
) -> GenerationTask:
    return GenerationTask(
        company_id=company.id,
        user_id=user.id,
        model_id=model.id,
        idempotency_key=f"company-health-{suffix}",
        request_fingerprint=suffix[0] * 64,
        status=status,
        request_payload={},
        billing_unit=BillingUnit.POINT if point_billing else BillingUnit.CNY_CENT,
        billing_version=2 if point_billing else 1,
        quote_cents=None if point_billing else max(1, reserved),
        quote_points=max(1, reserved) if point_billing else None,
        pricing_snapshot={},
        capability_snapshot={},
        reserved_cents=0 if point_billing else reserved,
        reserved_points=reserved if point_billing else 0,
        actual_cost_cents=None,
        actual_cost_points=None,
        created_at=created_at,
        updated_at=created_at,
    )


def _point_settle(
    *,
    company_id: str,
    key: str,
    amount: int,
    created_at: datetime,
    task_id: str | None = None,
) -> CompanyPointLedgerEntry:
    return CompanyPointLedgerEntry(
        company_id=company_id,
        kind=PointLedgerKind.SETTLE,
        amount_points=amount,
        available_delta_points=0,
        reserved_delta_points=-amount,
        idempotency_key=key,
        task_id=task_id,
        note="test settlement",
        created_at=created_at,
    )


def test_company_health_keeps_v1_cents_and_v2_points_unit_safe(app):
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    with app.state.session_factory() as session:
        cents_company = Company(name="Legacy cents", billing_version=1)
        points_company = Company(name="Migrated points", billing_version=2)
        user = User(email="health-units@example.com", display_name="Health units")
        model = ModelDefinition(
            slug="health-units-model",
            display_name="Health units model",
            provider_key="provider",
            billing_mode="per_item",
            active=True,
            published_at=now - timedelta(days=10),
        )
        session.add_all((cents_company, points_company, user, model))
        session.flush()
        session.add_all(
            (
                WalletAccount(
                    company_id=cents_company.id,
                    available_cents=80,
                    reserved_cents=40,
                ),
                WalletAccount(
                    company_id=points_company.id,
                    available_cents=0,
                    reserved_cents=0,
                ),
                CompanyPointWalletAccount(
                    company_id=points_company.id,
                    available_points=9,
                    reserved_points=4,
                    migration_idempotency_key="health-points-migration",
                    migrated_from_available_cents=90,
                    migration_remainder_cents=0,
                    migration_rounding_grant_points=0,
                ),
            )
        )
        session.add_all(
            (
                _task(
                    company=cents_company,
                    user=user,
                    model=model,
                    suffix="cents-stale",
                    status=TaskStatus.PROCESSING,
                    created_at=now - timedelta(days=2),
                    point_billing=False,
                    reserved=40,
                ),
                _task(
                    company=points_company,
                    user=user,
                    model=model,
                    suffix="points-stale",
                    status=TaskStatus.PROCESSING,
                    created_at=now - timedelta(days=2),
                    point_billing=True,
                    reserved=4,
                ),
            )
        )
        session.add_all(
            (
                LedgerEntry(
                    company_id=cents_company.id,
                    kind=LedgerKind.SETTLE,
                    amount_cents=70,
                    available_delta_cents=0,
                    reserved_delta_cents=-70,
                    idempotency_key="health-cents-recent",
                    note="recent cents",
                    created_at=now - timedelta(hours=2),
                ),
                LedgerEntry(
                    company_id=cents_company.id,
                    kind=LedgerKind.SETTLE,
                    amount_cents=70,
                    available_delta_cents=0,
                    reserved_delta_cents=-70,
                    idempotency_key="health-cents-baseline",
                    note="baseline cents",
                    created_at=now - timedelta(days=3),
                ),
                _point_settle(
                    company_id=points_company.id,
                    key="health-points-recent",
                    amount=7,
                    created_at=now - timedelta(hours=2),
                ),
                _point_settle(
                    company_id=points_company.id,
                    key="health-points-baseline",
                    amount=7,
                    created_at=now - timedelta(days=3),
                ),
            )
        )
        session.commit()

        result = AdminAnalyticsService.company_health(
            session,
            page=1,
            page_size=50,
            now=now,
            low_balance_threshold_cents=100,
            low_balance_threshold_points=10,
            abnormal_spend_ratio=3,
        )

        assert result["low_balance_threshold_cents"] == 100
        assert result["low_balance_threshold_points"] == 10
        rows = {row["company_id"]: row for row in result["items"]}

        cents = rows[cents_company.id]
        assert cents["billing_unit"] == "CNY_CENT"
        assert cents["billing_version"] == 1
        assert cents["available_cents"] == 80
        assert cents["reserved_cents"] == 40
        assert cents["spend_24h_cents"] == 70
        assert cents["available_points"] is None
        assert cents["reserved_points"] is None
        assert cents["spend_24h_points"] is None
        cents_alerts = {alert["code"]: alert for alert in cents["alerts"]}
        assert cents_alerts["LOW_BALANCE"]["details"] == {
            "available_cents": 80,
            "threshold_cents": 100,
        }
        assert cents_alerts["STALE_RESERVED_BALANCE"]["details"][
            "reserved_cents"
        ] == 40
        assert "reserved_points" not in cents_alerts["STALE_RESERVED_BALANCE"][
            "details"
        ]
        assert cents_alerts["ABNORMAL_SPEND"]["details"]["spend_24h_cents"] == 70

        points = rows[points_company.id]
        assert points["billing_unit"] == "POINT"
        assert points["billing_version"] == 2
        assert points["available_points"] == 9
        assert points["reserved_points"] == 4
        assert points["spend_24h_points"] == 7
        assert points["available_cents"] is None
        assert points["reserved_cents"] is None
        assert points["spend_24h_cents"] is None
        point_alerts = {alert["code"]: alert for alert in points["alerts"]}
        assert point_alerts["LOW_BALANCE"]["details"] == {
            "available_points": 9,
            "threshold_points": 10,
        }
        assert point_alerts["STALE_RESERVED_BALANCE"]["details"][
            "reserved_points"
        ] == 4
        assert "reserved_cents" not in point_alerts["STALE_RESERVED_BALANCE"][
            "details"
        ]
        assert point_alerts["ABNORMAL_SPEND"]["details"]["spend_24h_points"] == 7


def test_company_health_rejects_missing_v2_wallet_and_invalid_point_threshold(app):
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    with app.state.session_factory() as session:
        session.add(Company(name="Broken point wallet", billing_version=2))
        session.commit()

        with pytest.raises(ConflictError, match="missing its POINT/v2 wallet"):
            AdminAnalyticsService.company_health(
                session,
                page=1,
                page_size=50,
                now=now,
            )

        with pytest.raises(ConflictError, match="point balance threshold"):
            AdminAnalyticsService.company_health(
                session,
                page=1,
                page_size=50,
                now=now,
                low_balance_threshold_points=-1,
            )


def test_operating_series_fails_closed_for_missing_and_duplicate_point_settles(app):
    start = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with app.state.session_factory() as session:
        company = Company(name="Point reconcile", billing_version=2)
        user = User(email="point-reconcile@example.com", display_name="Point reconcile")
        model = ModelDefinition(
            slug="point-reconcile-model",
            display_name="Point reconcile model",
            provider_key="provider",
            billing_mode="per_item",
            active=True,
            published_at=start - timedelta(days=1),
        )
        session.add_all((company, user, model))
        session.flush()
        session.add(
            CompanyPointWalletAccount(
                company_id=company.id,
                available_points=100,
                reserved_points=0,
                migration_idempotency_key="point-reconcile-migration",
                migrated_from_available_cents=1_000,
                migration_remainder_cents=0,
                migration_rounding_grant_points=0,
            )
        )
        missing = _task(
            company=company,
            user=user,
            model=model,
            suffix="missing-settle",
            status=TaskStatus.SUCCEEDED,
            created_at=start + timedelta(hours=2),
            point_billing=True,
        )
        duplicate = _task(
            company=company,
            user=user,
            model=model,
            suffix="duplicate-settle",
            status=TaskStatus.SUCCEEDED,
            created_at=start + timedelta(hours=3),
            point_billing=True,
        )
        missing.actual_cost_points = 10
        duplicate.actual_cost_points = 10
        session.add_all((missing, duplicate))
        session.flush()
        session.add_all(
            (
                _point_settle(
                    company_id=company.id,
                    key="duplicate-settle-a",
                    amount=5,
                    created_at=start + timedelta(hours=3, minutes=1),
                    task_id=duplicate.id,
                ),
                _point_settle(
                    company_id=company.id,
                    key="duplicate-settle-b",
                    amount=5,
                    created_at=start + timedelta(hours=3, minutes=2),
                    task_id=duplicate.id,
                ),
                ChannelCostEntry(
                    amount_cents=10,
                    idempotency_key="missing-settle-provider-cost",
                    channel_key="point-channel",
                    channel_type=ChannelType.OFFICIAL,
                    occurred_at=start + timedelta(hours=2),
                    external_reference="missing-settle-cost",
                    company_id=company.id,
                    task_id=missing.id,
                    note="provider cost",
                    source=ChannelCostSource.PLATFORM_ADMIN,
                    recorded_by_user_id=user.id,
                ),
                ChannelCostEntry(
                    amount_cents=10,
                    idempotency_key="duplicate-settle-provider-cost",
                    channel_key="point-channel",
                    channel_type=ChannelType.OFFICIAL,
                    occurred_at=start + timedelta(hours=3),
                    external_reference="duplicate-settle-cost",
                    company_id=company.id,
                    task_id=duplicate.id,
                    note="provider cost",
                    source=ChannelCostSource.PLATFORM_ADMIN,
                    recorded_by_user_id=user.id,
                ),
            )
        )
        session.commit()

        result = AdminAnalyticsService.operating_series(
            session,
            start=start,
            end=start + timedelta(days=1),
            granularity="day",
            _include_comparisons=False,
        )

        row = result["points"][0]
        assert result["point_bucket_time_basis"] == "immutable_settle_ledger_created_at"
        assert row["point_succeeded_task_count"] == 2
        assert row["point_settlement_count"] == 0
        assert row["settled_points"] == 0
        assert row["point_settlement_missing_task_count"] == 1
        assert row["point_settlement_duplicate_task_count"] == 1
        assert row["cost_reconciliation_status"] == "complete"
        assert row["revenue_reconciliation_status"] == "incomplete"
        assert row["finance_status"] == "incomplete"
        assert row["known_gross_profit_cents"] is None
        assert row["gross_profit_cents"] is None
        assert row["gross_margin"] is None

        profitability = AdminAnalyticsService.model_profitability(
            session,
            start=start,
            end=start + timedelta(days=1),
        )
        model_row = next(
            item for item in profitability["items"] if item["model_id"] == model.id
        )
        assert model_row["revenue_unavailable_task_count"] == 1
        assert model_row["revenue_missing_task_count"] == 1
        assert model_row["revenue_reconciliation_status"] == "unavailable"
        assert model_row["known_gross_profit_cents"] is None
        assert model_row["gross_profit_cents"] is None
        assert model_row["gross_margin"] is None
        assert profitability["revenue_unavailable_task_count"] == 1
        assert profitability["revenue_missing_task_count"] == 1
        assert profitability["revenue_reconciliation_status"] == "unavailable"
