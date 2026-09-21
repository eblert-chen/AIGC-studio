"""Independent income-report regressions: period integrity and source conservation."""
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update

from platform_api.models import (
    BillingUnit, CompanyPointLedgerEntry, CompanyPointLot, GenerationTask,
    PersonalPointLot, PointLedgerKind, PointLotSettlementValueAllocation,
    TaskPointLotAllocation, TaskStatus, CompanyBillingAccount, CompanyInvoice,
    FinanceReconciliationException,
    ChannelCostEntry,
)
from platform_api.services.admin_analytics import AdminAnalyticsService
from platform_api.services.point_income import load_point_income, point_income_report
from platform_api.services.financial_reconciliation import FinancialReconciliationService

from .test_financial_reconciliation import (
    OCCURRED_AT, PERIOD_START, PERIOD_END,
    _seed_balanced_company_chain, _seed_balanced_personal_chain,
)


def test_operating_consumed_income_does_not_move_after_task_metadata_update(app):
    with app.state.session_factory.begin() as session:
        _, task = _seed_balanced_company_chain(session)
        session.execute(update(GenerationTask).where(GenerationTask.id == task.id).values(
            updated_at=PERIOD_END + timedelta(days=2),
        ))
        session.expire_all()
        original = AdminAnalyticsService.operating_series(
            session, start=PERIOD_START, end=PERIOD_END,
            granularity="day", _include_comparisons=False,
        )
        later = AdminAnalyticsService.operating_series(
            session, start=PERIOD_END, end=PERIOD_END + timedelta(days=3),
            granularity="day", _include_comparisons=False,
        )
        assert original["totals"]["settled_revenue_cents"] == 100
        assert later["totals"]["settled_revenue_cents"] == 0
        assert original["point_income"]["totals"]["settled_revenue_cents"] == 100
        assert later["point_income"]["totals"]["settled_revenue_cents"] == 0


def test_missing_provider_cost_stays_with_settlement_after_task_metadata_update(app):
    with app.state.session_factory.begin() as session:
        _, task = _seed_balanced_company_chain(session)
        session.execute(delete(ChannelCostEntry).where(ChannelCostEntry.task_id == task.id))
        session.execute(update(GenerationTask).where(GenerationTask.id == task.id).values(
            updated_at=PERIOD_END + timedelta(days=2),
        ))
        session.expire_all()
        original = AdminAnalyticsService.operating_series(
            session, start=PERIOD_START, end=PERIOD_END,
            granularity="day", _include_comparisons=False,
        )["totals"]
        assert original["settled_revenue_cents"] == 100
        assert original["cost_missing_task_count"] == 1
        assert original["gross_profit_cents"] is None
        assert original["finance_status"] == "incomplete"


@pytest.mark.parametrize("personal", [False, True])
def test_income_rejects_value_components_not_backed_by_source_lot(app, personal):
    with app.state.session_factory.begin() as session:
        seed = _seed_balanced_personal_chain if personal else _seed_balanced_company_chain
        _, task = seed(session)
        # Deliberate corruption in an isolated fixture. Both rows still obey
        # their local 10-cents/point constraints, but their cash sources disagree.
        session.execute(update(PersonalPointLot if personal else CompanyPointLot).values(
            cash_basis_cents=0, receivable_basis_cents=0, subsidy_cents=100,
        ))
        session.expire_all()
        income = load_point_income(session, [task])[task.id]
        assert not income.complete
        assert income.revenue_cents == 0
        report = point_income_report(session, start=PERIOD_START, end=PERIOD_END)
        assert report["totals"]["settled_revenue_cents"] == 0
        assert report["totals"]["reconciliation_status"] == "incomplete"


def test_income_rejects_two_tasks_claiming_the_same_settled_lot_capacity(app):
    with app.state.session_factory.begin() as session:
        company, first = _seed_balanced_company_chain(session)
        lot = session.scalar(select(CompanyPointLot).where(CompanyPointLot.company_id == company.id))
        second = GenerationTask(
            company_id=company.id, personal_workspace_id=None,
            user_id=first.user_id, model_id=first.model_id,
            idempotency_key="second-capacity-claim", request_fingerprint="e" * 64,
            status=TaskStatus.SUCCEEDED, request_payload={"prompt": "capacity proof"},
            billing_unit=BillingUnit.POINT, billing_version=2,
            quote_points=10, reserved_points=0, actual_cost_points=10,
            quote_cents=None, reserved_cents=0, actual_cost_cents=None,
            pricing_snapshot={}, capability_snapshot={},
            created_at=OCCURRED_AT, updated_at=OCCURRED_AT,
        )
        session.add(second)
        session.flush()
        settle = CompanyPointLedgerEntry(
            company_id=company.id, task_id=second.id, kind=PointLedgerKind.SETTLE,
            amount_points=10, available_delta_points=0, reserved_delta_points=-10,
            idempotency_key="second-capacity-settle", created_at=OCCURRED_AT,
        )
        allocation = TaskPointLotAllocation(
            company_id=company.id, task_id=second.id, lot_id=lot.id,
            allocated_points=10, reserved_points=0, settled_points=10,
            released_points=0, created_at=OCCURRED_AT,
        )
        session.add_all([settle, allocation])
        session.flush()
        session.add(PointLotSettlementValueAllocation(
            company_id=company.id, personal_workspace_id=None, task_id=second.id,
            company_task_allocation_id=allocation.id, company_settle_ledger_id=settle.id,
            settled_points=10, cash_basis_cents=100, receivable_basis_cents=0,
            subsidy_cents=0, created_at=OCCURRED_AT,
        ))
        session.flush()
        # The lot still owns just ten settled points / 100 cents; each task
        # looks locally complete, but together they cannot justify 200 cents.
        assert lot.settled_points == 10
        incomes = load_point_income(session, [first, second])
        assert not any(income.complete for income in incomes.values())
        assert sum(income.revenue_cents for income in incomes.values()) == 0


def test_late_receivable_becomes_immutable_replayable_finance_exception(app):
    from .test_enterprise_credit_admission_recovery import END, _invoice_context
    from .test_enterprise_monthly_billing import _settle_contract_task

    with app.state.session_factory.begin() as session:
        company, user, model, _, original_invoice, _ = _invoice_context(session)
        lot = session.scalar(select(CompanyPointLot).where(CompanyPointLot.company_id == company.id))
        task, settle, value = _settle_contract_task(
            session, company=company, user=user, model=model, lot=lot,
            points=10, key="finance-late-receivable", settled_at=END + timedelta(days=1),
        )
        company_id, invoice_id = company.id, original_invoice.id
        task_id, settle_id, value_id = task.id, settle.id, value.id
    args = dict(
        run_kind="daily", period_start=END, period_end=END + timedelta(days=3),
        idempotency_key="review-late-receivable", provider_statement_available=False,
        provider_cost_statement_available=False, started_at=END + timedelta(days=3),
    )
    with app.state.session_factory.begin() as session:
        result = FinancialReconciliationService.run(session, **args)
        evidence = [row for row in result.exceptions
                    if row.code == "INVOICE_LATE_RECEIVABLE_REQUIRES_ACTION"]
        assert len(evidence) == 1
        item = evidence[0]
        assert item.dimension == "cash"
        assert item.entity_id == value_id
        assert (item.expected_amount, item.actual_amount) == (100, 0)
        assert item.details["company_id"] == company_id
        assert item.details["task_id"] == task_id
        assert item.details["settle_ledger_id"] == settle_id
        assert item.details["reason"] == "no_covering_billing_cycle"
        exception_id, snapshot_sha = item.id, result.run.snapshot_sha256
    with app.state.session_factory.begin() as session:
        replay = FinancialReconciliationService.run(session, **args)
        assert not replay.created
        assert replay.run.snapshot_sha256 == snapshot_sha
        assert [row.id for row in replay.exceptions
                if row.code == "INVOICE_LATE_RECEIVABLE_REQUIRES_ACTION"] == [exception_id]
        assert session.get(FinanceReconciliationException, exception_id).evidence_sha256
        assert session.get(CompanyBillingAccount, company_id).unbilled_receivable_cents == 100
        assert session.get(CompanyInvoice, invoice_id).total_cents == 100
