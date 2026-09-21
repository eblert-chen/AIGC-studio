from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update

from platform_api.models import (
    CompanyPointLot, GenerationTask, PersonalPointLot,
    PointLotSettlementValueAllocation,
)
from platform_api.services.admin_analytics import AdminAnalyticsService
from platform_api.services.point_income import load_point_income, point_income_report
from .test_financial_reconciliation import (
    PERIOD_START, PERIOD_END, _seed_balanced_company_chain, _seed_balanced_personal_chain,
)


@pytest.mark.parametrize("personal", [False, True])
@pytest.mark.parametrize("cash,receivable,subsidy", [(100, 0, 0), (0, 100, 0), (0, 0, 100), (30, 40, 30)])
def test_consumed_value_is_attributed_without_turning_promotions_into_revenue(app, personal, cash, receivable, subsidy):
    with app.state.session_factory.begin() as session:
        seed = _seed_balanced_personal_chain if personal else _seed_balanced_company_chain
        _, task = seed(session)
        # Fixture-only SQL builds each immutable source case before the report.
        values = dict(cash_basis_cents=cash, receivable_basis_cents=receivable, subsidy_cents=subsidy)
        session.execute(update(PersonalPointLot if personal else CompanyPointLot).values(**values))
        session.execute(update(PointLotSettlementValueAllocation).values(**values))
        session.expire_all()
        income = load_point_income(session, [task])[task.id]
        assert income.complete is True
        assert income.revenue_cents == cash + receivable
        report = point_income_report(session, start=PERIOD_START, end=PERIOD_END)
        row = report["scopes"]["personal" if personal else "company"]
        assert row["settled_revenue_cents"] == cash + receivable
        assert row["subsidy_cents"] == subsidy
        assert row["reconciliation_status"] == "complete"
        assert report["is_cash_flow"] is report["is_net_profit"] is False
        other = report["scopes"]["company" if personal else "personal"]
        assert other["task_count"] == 0
        if not personal:
            operating = AdminAnalyticsService.operating_series(session, start=PERIOD_START, end=PERIOD_END, granularity="day")
            assert operating["totals"]["settled_revenue_cents"] == cash + receivable
            assert operating["totals"]["point_subsidy_cents"] == subsidy
            assert operating["totals"]["gross_profit_cents"] == cash + receivable - 40
            models = AdminAnalyticsService.model_profitability(session, start=PERIOD_START, end=PERIOD_END)
            model = next(row for row in models["items"] if row["model_id"] == task.model_id)
            assert model["settled_revenue_cents"] == cash + receivable
            assert model["gross_profit_cents"] == cash + receivable - 40


@pytest.mark.parametrize("corruption", ["missing", "amount", "wrong_scope"])
def test_missing_or_inconsistent_value_fails_closed(app, corruption):
    with app.state.session_factory.begin() as session:
        _, task = _seed_balanced_company_chain(session)
        if corruption == "missing":
            session.execute(delete(PointLotSettlementValueAllocation))
        elif corruption == "amount":
            session.execute(update(PointLotSettlementValueAllocation).values(settled_points=9, cash_basis_cents=90))
        else:
            # SQLite test DB deliberately bypasses the application write path.
            from platform_api.models import Company
            other = Company(name="other scope")
            session.add(other)
            session.flush()
            session.execute(update(PointLotSettlementValueAllocation).values(company_id=other.id))
        session.expire_all()
        assert load_point_income(session, [task])[task.id].complete is False
        totals = AdminAnalyticsService.operating_series(session, start=PERIOD_START, end=PERIOD_END, granularity="day")["totals"]
        assert totals["settled_revenue_cents"] == 0
        assert totals["unattributed_point_settlement_count"] == 1
        assert totals["gross_profit_cents"] is None
        assert totals["finance_status"] == "incomplete"


def test_scope_report_uses_immutable_settlement_time_even_when_task_is_touched_later(app):
    with app.state.session_factory.begin() as session:
        _, task = _seed_balanced_company_chain(session)
        session.execute(update(GenerationTask).where(GenerationTask.id == task.id).values(updated_at=PERIOD_END + timedelta(days=2)))
        session.expire_all()
        original = point_income_report(session, start=PERIOD_START, end=PERIOD_END)
        later = point_income_report(session, start=PERIOD_END, end=PERIOD_END + timedelta(days=3))
        assert original["totals"]["settled_revenue_cents"] == 100
        assert later["totals"]["task_count"] == 0


def test_model_income_does_not_treat_a_different_periods_posting_as_missing(app):
    from platform_api.models import ChannelCostEntry, CompanyPointLedgerEntry
    with app.state.session_factory.begin() as session:
        _, task = _seed_balanced_company_chain(session)
        later_time = PERIOD_END + timedelta(days=2)
        session.execute(update(CompanyPointLedgerEntry).values(created_at=later_time))
        session.execute(update(ChannelCostEntry).values(occurred_at=later_time))
        session.execute(update(GenerationTask).values(updated_at=later_time))
        session.expire_all()
        original = AdminAnalyticsService.model_profitability(session, start=PERIOD_START, end=PERIOD_END)
        later = AdminAnalyticsService.model_profitability(session, start=PERIOD_END, end=PERIOD_END + timedelta(days=3))
        before = next(row for row in original["items"] if row["model_id"] == task.model_id)
        after = next(row for row in later["items"] if row["model_id"] == task.model_id)
        assert before["settled_revenue_cents"] == before["revenue_missing_task_count"] == 0
        assert after["settled_revenue_cents"] == 100
        assert after["gross_profit_cents"] == 60


def test_model_income_retains_unposted_gap_for_task_created_before_window(app):
    from platform_api.models import CompanyPointLedgerEntry
    with app.state.session_factory.begin() as session:
        _, task = _seed_balanced_company_chain(session)
        session.execute(delete(PointLotSettlementValueAllocation))
        session.execute(delete(CompanyPointLedgerEntry))
        session.execute(update(GenerationTask).values(
            created_at=PERIOD_START - timedelta(days=2),
            updated_at=PERIOD_START + timedelta(hours=1),
        ))
        session.expire_all()
        result = AdminAnalyticsService.model_profitability(session, start=PERIOD_START, end=PERIOD_END)
        row = next(row for row in result["items"] if row["model_id"] == task.model_id)
        assert row["revenue_missing_task_count"] == 1
        assert row["revenue_reconciliation_status"] == "incomplete"
        assert row["gross_profit_cents"] is None
