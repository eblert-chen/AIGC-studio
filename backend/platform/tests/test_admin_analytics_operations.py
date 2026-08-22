from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from platform_api.models import (
    AuditLog,
    ChannelCostEntry,
    ChannelCostSource,
    ChannelType,
    Company,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelDefinition,
    PublicationJob,
    PublicationJobStatus,
    PublisherConnection,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskArtifact,
    TaskStatus,
    TaskTimeoutEvent,
    User,
    WalletAccount,
)
from platform_api.routers.admin_operations import router
from platform_api.services.admin_analytics import AdminAnalyticsService


def _seed_financial_window(session):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    company = Company(name="Analytics Company")
    user = User(email="analytics@example.com", display_name="Analytics")
    model = ModelDefinition(
        slug="analytics-model",
        display_name="Analytics Model",
        provider_key="provider",
        billing_mode="per_item",
        active=True,
        published_at=start - timedelta(days=1),
    )
    session.add_all((company, user, model))
    session.flush()
    session.add(WalletAccount(company_id=company.id, available_cents=10_000, reserved_cents=0))
    first = GenerationTask(
        company_id=company.id,
        user_id=user.id,
        model_id=model.id,
        idempotency_key="analytics-first",
        request_fingerprint="1" * 64,
        status=TaskStatus.SUCCEEDED,
        request_payload={},
        quote_cents=500,
        pricing_snapshot={},
        capability_snapshot={},
        reserved_cents=0,
        actual_cost_cents=500,
        created_at=start + timedelta(hours=1),
        updated_at=start + timedelta(hours=1, seconds=10),
    )
    second = GenerationTask(
        company_id=company.id,
        user_id=user.id,
        model_id=model.id,
        idempotency_key="analytics-second",
        request_fingerprint="2" * 64,
        status=TaskStatus.SUCCEEDED,
        request_payload={},
        quote_cents=700,
        pricing_snapshot={},
        capability_snapshot={},
        reserved_cents=0,
        actual_cost_cents=700,
        created_at=start + timedelta(days=1, hours=1),
        updated_at=start + timedelta(days=1, hours=1, seconds=100),
    )
    failed = GenerationTask(
        company_id=company.id,
        user_id=user.id,
        model_id=model.id,
        idempotency_key="analytics-failed",
        request_fingerprint="3" * 64,
        status=TaskStatus.FAILED,
        request_payload={},
        quote_cents=300,
        pricing_snapshot={},
        capability_snapshot={},
        reserved_cents=0,
        actual_cost_cents=None,
        relay_error_snapshot={"code": "UPSTREAM_FAILED"},
        created_at=start + timedelta(days=1, hours=2),
        updated_at=start + timedelta(days=1, hours=2, seconds=30),
    )
    session.add_all((first, second, failed))
    session.flush()
    session.add_all(
        (
            LedgerEntry(
                company_id=company.id,
                kind=LedgerKind.RECHARGE,
                amount_cents=10_000,
                available_delta_cents=10_000,
                reserved_delta_cents=0,
                idempotency_key="analytics-recharge",
                note="recharge",
                created_at=start + timedelta(minutes=1),
            ),
            LedgerEntry(
                company_id=company.id,
                kind=LedgerKind.SETTLE,
                amount_cents=500,
                available_delta_cents=0,
                reserved_delta_cents=-500,
                idempotency_key="analytics-settle-first",
                task_id=first.id,
                note="settled",
                created_at=start + timedelta(hours=1, seconds=10),
            ),
            LedgerEntry(
                company_id=company.id,
                kind=LedgerKind.SETTLE,
                amount_cents=700,
                available_delta_cents=0,
                reserved_delta_cents=-700,
                idempotency_key="analytics-settle-second",
                task_id=second.id,
                note="settled",
                created_at=start + timedelta(days=1, hours=1, seconds=100),
            ),
            ChannelCostEntry(
                amount_cents=200,
                idempotency_key="analytics-cost-first",
                channel_key="official-one",
                channel_type=ChannelType.OFFICIAL,
                occurred_at=start + timedelta(hours=1, seconds=9),
                external_reference="provider-cost-1",
                company_id=company.id,
                task_id=first.id,
                note="cost",
                source=ChannelCostSource.PLATFORM_ADMIN,
                recorded_by_user_id=user.id,
            ),
            RelaySubmissionOutbox(
                company_id=company.id,
                task_id=second.id,
                status=RelayOutboxStatus.RECONCILIATION_REQUIRED,
                idempotency_key=f"platform-task-{second.id}",
                relay_payload={},
                attempt_count=1,
                next_attempt_at=start + timedelta(days=1),
                submission_outcome_uncertain_at=start + timedelta(days=1),
            ),
            TaskTimeoutEvent(
                company_id=company.id,
                task_id=failed.id,
                previous_status="processing",
                final_status="failed",
                outcome="timed_out",
                reason="analytics timeout fixture",
                released_cents=0,
                created_at=start + timedelta(days=1, hours=2, seconds=31),
            ),
        )
    )
    session.flush()
    return start, company, user, model


def test_operating_task_and_model_analytics_keep_missing_cost_explicit(app):
    with app.state.session_factory() as session:
        start, _, _, model = _seed_financial_window(session)
        session.commit()

        series = AdminAnalyticsService.operating_series(
            session,
            start=start,
            end=start + timedelta(days=3),
            granularity="day",
        )
        assert series["points"][0]["gross_profit_cents"] == 300
        assert series["points"][0]["cost_reconciliation_status"] == "complete"
        assert series["points"][1]["known_gross_profit_cents"] == 700
        assert series["points"][1]["gross_profit_cents"] is None
        assert series["points"][1]["cost_missing_task_count"] == 1
        assert series["totals"]["gross_profit_cents"] is None
        assert series["comparisons"]["period_over_period"]["status"] == "unavailable"
        assert (
            series["comparisons"]["period_over_period"]["metrics"]
            ["settled_revenue_cents"]["absolute_change"]
            is None
        )

        operations = AdminAnalyticsService.task_operations(
            session, start=start, end=start + timedelta(days=3)
        )
        assert operations["status_counts"]["succeeded"] == 2
        assert operations["status_counts"]["failed"] == 1
        assert operations["submission_unknown_count"] == 1
        assert operations["timeout_count"] == 1
        assert operations["latency_seconds"]["terminal_p50"] == 30.0
        assert operations["latency_seconds"]["terminal_p95"] == 100.0
        assert operations["failure_reasons"] == [
            {"error_code": "UPSTREAM_FAILED", "count": 1}
        ]
        assert operations["trend_data_status"] == "available"
        assert operations["trend_granularity"] == "day"
        assert len(operations["trend_points"]) == 3
        assert operations["trend_points"][0]["total_task_count"] == 1
        assert operations["trend_points"][0]["terminal_latency_p50_seconds"] == 10.0
        assert operations["trend_points"][1]["failed_count"] == 1
        assert operations["trend_points"][1]["timeout_count"] == 1
        assert operations["trend_points"][1]["submission_unknown_count"] == 1
        distribution = operations["terminal_latency_distribution_seconds"]
        assert distribution["sample_count"] == 3
        assert {
            item["range"]: item["count"] for item in distribution["bins"]
        } == {
            "[0,10]": 1,
            "(10,30]": 1,
            "(30,60]": 0,
            "(60,120]": 1,
            "(120,300]": 0,
            "(300,600]": 0,
            "(600,+inf)": 0,
        }

        profitability = AdminAnalyticsService.model_profitability(
            session, start=start, end=start + timedelta(days=3)
        )
        row = next(item for item in profitability["items"] if item["model_id"] == model.id)
        assert row["settled_revenue_cents"] == 1_200
        assert row["provider_cost_cents"] == 200
        assert row["known_gross_profit_cents"] == 1_000
        assert row["gross_profit_cents"] is None
        assert row["cost_reconciliation_status"] == "incomplete"


def test_operating_series_returns_real_period_and_year_comparisons(app):
    with app.state.session_factory() as session:
        start, company, user, model = _seed_financial_window(session)

        def add_baseline(
            *,
            suffix: str,
            task_created_at: datetime,
            recharge_cents: int,
            revenue_cents: int,
            provider_cost_cents: int,
        ) -> None:
            task = GenerationTask(
                company_id=company.id,
                user_id=user.id,
                model_id=model.id,
                idempotency_key=f"analytics-baseline-{suffix}",
                request_fingerprint=("6" if suffix == "previous" else "7") * 64,
                status=TaskStatus.SUCCEEDED,
                request_payload={},
                quote_cents=revenue_cents,
                pricing_snapshot={},
                capability_snapshot={},
                reserved_cents=0,
                actual_cost_cents=revenue_cents,
                created_at=task_created_at,
                updated_at=task_created_at + timedelta(seconds=20),
            )
            session.add(task)
            session.flush()
            session.add_all(
                (
                    LedgerEntry(
                        company_id=company.id,
                        kind=LedgerKind.RECHARGE,
                        amount_cents=recharge_cents,
                        available_delta_cents=recharge_cents,
                        reserved_delta_cents=0,
                        idempotency_key=f"analytics-baseline-recharge-{suffix}",
                        note="comparison recharge",
                        created_at=task_created_at - timedelta(minutes=5),
                    ),
                    LedgerEntry(
                        company_id=company.id,
                        kind=LedgerKind.SETTLE,
                        amount_cents=revenue_cents,
                        available_delta_cents=0,
                        reserved_delta_cents=-revenue_cents,
                        idempotency_key=f"analytics-baseline-settle-{suffix}",
                        task_id=task.id,
                        note="comparison settlement",
                        created_at=task.updated_at,
                    ),
                    ChannelCostEntry(
                        amount_cents=provider_cost_cents,
                        idempotency_key=f"analytics-baseline-cost-{suffix}",
                        channel_key="official-one",
                        channel_type=ChannelType.OFFICIAL,
                        occurred_at=task.updated_at - timedelta(seconds=1),
                        external_reference=f"comparison-cost-{suffix}",
                        company_id=company.id,
                        task_id=task.id,
                        note="comparison cost",
                        source=ChannelCostSource.PLATFORM_ADMIN,
                        recorded_by_user_id=user.id,
                    ),
                )
            )

        add_baseline(
            suffix="previous",
            task_created_at=start - timedelta(days=2),
            recharge_cents=5_000,
            revenue_cents=300,
            provider_cost_cents=100,
        )
        add_baseline(
            suffix="year",
            task_created_at=start.replace(year=start.year - 1) + timedelta(hours=1),
            recharge_cents=8_000,
            revenue_cents=600,
            provider_cost_cents=250,
        )
        session.commit()

        series = AdminAnalyticsService.operating_series(
            session,
            start=start,
            end=start + timedelta(days=3),
            granularity="day",
        )
        period = series["comparisons"]["period_over_period"]
        assert period["status"] == "partial"
        assert period["baseline_totals"]["settled_revenue_cents"] == 300
        assert period["metrics"]["recharge_cents"] == {
            "current": 10_000,
            "baseline": 5_000,
            "absolute_change": 5_000,
            "change_rate": 1.0,
        }
        assert period["metrics"]["settled_revenue_cents"]["absolute_change"] == 900
        assert period["metrics"]["settled_revenue_cents"]["change_rate"] == 3.0
        assert period["metrics"]["gross_profit_cents"]["absolute_change"] is None

        yearly = series["comparisons"]["year_over_year"]
        assert yearly["status"] == "partial"
        assert yearly["baseline_totals"]["settled_revenue_cents"] == 600
        assert yearly["metrics"]["recharge_cents"]["absolute_change"] == 2_000
        assert yearly["metrics"]["recharge_cents"]["change_rate"] == 0.25
        assert yearly["metrics"]["settled_revenue_cents"]["absolute_change"] == 600
        assert yearly["metrics"]["settled_revenue_cents"]["change_rate"] == 1.0


def test_admin_operations_router_is_admin_only_and_disables_caching(app):
    app.include_router(router)
    with app.state.session_factory() as session:
        admin = User(
            email="operations-admin@example.com",
            display_name="Operations Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.commit()
        admin_id = admin.id

    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/platform-admin/analytics/task-operations")
        assert unauthorized.status_code == 401
        response = client.get(
            "/api/v1/platform-admin/analytics/task-operations",
            headers={"X-Platform-Admin-User-ID": admin_id},
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["total_task_count"] == 0
        assert response.json()["trend_data_status"] == "empty"
        assert response.json()["trend_points"] == []
        assert response.json()["terminal_latency_distribution_seconds"] == {
            "sample_count": 0,
            "bins": [],
        }


def test_exception_center_exposes_and_executes_real_publication_reconciliation(
    client, app
):
    with app.state.session_factory.begin() as session:
        _, company, user, _ = _seed_financial_window(session)
        task = session.query(GenerationTask).filter_by(
            company_id=company.id, status=TaskStatus.SUCCEEDED
        ).first()
        assert task is not None
        artifact = TaskArtifact(
            company_id=company.id,
            task_id=task.id,
            asset_id="exception-artifact",
            position=0,
            media_type="video",
            content_type="video/mp4",
            size_bytes=1024,
            sha256="4" * 64,
        )
        connection = PublisherConnection(
            company_id=company.id,
            created_by_user_id=user.id,
            provider="mock",
            display_name="Exception test account",
            external_account_id="exception-account",
            config={"mock": True},
        )
        session.add_all((artifact, connection))
        session.flush()
        job = PublicationJob(
            company_id=company.id,
            created_by_user_id=user.id,
            task_artifact_id=artifact.id,
            connection_id=connection.id,
            idempotency_key="exception-publication-job",
            request_fingerprint="5" * 64,
            status=PublicationJobStatus.SUBMISSION_UNKNOWN,
            title="Unknown provider outcome",
            caption="",
            timezone="Asia/Shanghai",
            attempt_count=1,
            error_code="submission_unknown",
        )
        admin = User(
            email="exception-admin@example.com",
            display_name="Exception Admin",
            is_platform_admin=True,
        )
        session.add_all((job, admin))
        session.flush()
        company_id = company.id
        job_id = job.id
        admin_id = admin.id

    listed = client.get(
        "/api/v1/platform-admin/analytics/exceptions",
        headers={"X-Platform-Admin-User-ID": admin_id},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["sources"]["publishing"]["source_status"] == "available"
    assert listed.json()["sources"]["publishing"]["data_status"] == "available"
    assert listed.json()["sources"]["publishing"]["exception_count"] >= 1
    item = next(
        row for row in listed.json()["items"] if row["target_id"] == job_id
    )
    assert item["actions"] == [
        {
            "code": "reconcile_publication_submission",
            "method": "POST",
            "path": (
                "/api/v1/platform-admin/analytics/exceptions/"
                f"companies/{company_id}/publication-jobs/{job_id}/reconcile"
            ),
            "requires_external_verification": True,
        }
    ]

    response = client.post(
        item["actions"][0]["path"],
        headers={
            "X-Platform-Admin-User-ID": admin_id,
            "X-Request-ID": "admin-publication-reconcile",
        },
        json={
            "outcome": "failed",
            "error_code": "provider_confirmed_absent",
            "error_message": "Provider console confirms no external post exists",
            "reason": "Verified in the provider console by operations",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    assert response.json()["status"] == "failed"

    with app.state.session_factory() as session:
        saved = session.get(PublicationJob, job_id)
        assert saved is not None
        assert saved.status == PublicationJobStatus.FAILED
        audit = session.query(AuditLog).filter_by(
            action="publishing.job.reconcile", target_id=job_id
        ).one()
        assert audit.actor_user_id == admin_id
        assert audit.after_summary["change_reason"] == (
            "Verified in the provider console by operations"
        )
