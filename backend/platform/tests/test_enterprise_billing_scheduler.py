from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    CompanyBillingAccount,
    CompanyBillingContractVersion,
    CompanyBillingCycle,
    CompanyInvoice,
    CompanyPointLedgerEntry,
    EnterpriseDunningRun,
    EnterpriseInvoiceStatus,
    PointLedgerKind,
)
from platform_api.services.enterprise_billing import EnterpriseBillingService
from platform_api.services.errors import ConflictError

from .test_enterprise_monthly_billing import (
    _activate,
    _seed_point_company,
    _settle_contract_task,
)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def test_scheduler_does_not_activate_uncontracted_companies(app) -> None:
    with app.state.session_factory.begin() as session:
        _seed_point_company(session)
        result = EnterpriseBillingService.run_once(
            session, as_of=datetime(2026, 8, 15, tzinfo=timezone.utc)
        )
        assert result == {
            "processed": False,
            "company_id": None,
            "closed_cycle_ids": [],
            "opened_cycle_id": None,
            "dunning_run_id": None,
        }
        assert session.scalar(select(func.count(CompanyBillingContractVersion.id))) == 0
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 0


def test_scheduler_opens_full_local_month_and_reuses_fixed_daily_dunning_intent(app) -> None:
    start = datetime(2026, 7, 31, 16, tzinfo=timezone.utc)  # Shanghai Aug 1 midnight.
    now = datetime(2026, 8, 15, 4, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        result = EnterpriseBillingService.run_once(session, as_of=now)
        assert result["processed"] is True and result["company_id"] == company.id
        cycle = session.get(CompanyBillingCycle, result["opened_cycle_id"])
        assert cycle is not None
        assert _utc(cycle.period_start) == start
        assert _utc(cycle.period_end) == datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
        run = session.get(EnterpriseDunningRun, result["dunning_run_id"])
        assert run is not None
        assert _utc(run.as_of) == datetime(2026, 8, 14, 16, tzinfo=timezone.utc)
        assert run.idempotency_key == f"enterprise-daily:{company.id}:2026-08-15"
        replay = EnterpriseBillingService.run_once(session, as_of=now + timedelta(hours=1))
        assert replay["processed"] is False
        assert session.scalar(select(func.count(EnterpriseDunningRun.id))) == 1
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 1
        assert session.scalar(
            select(func.count(CompanyPointLedgerEntry.id)).where(
                CompanyPointLedgerEntry.kind == PointLedgerKind.CREDIT
            )
        ) == 1


def test_scheduler_does_not_grant_retroactive_partial_month(app) -> None:
    effective = datetime(2026, 8, 15, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=effective)
        before_boundary = EnterpriseBillingService.run_once(
            session, as_of=effective + timedelta(days=1)
        )
        assert before_boundary["processed"] is True  # daily audit only.
        assert before_boundary["opened_cycle_id"] is None
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 0
        boundary = datetime(2026, 8, 31, 16, tzinfo=timezone.utc)
        after_boundary = EnterpriseBillingService.run_once(session, as_of=boundary)
        cycle = session.get(CompanyBillingCycle, after_boundary["opened_cycle_id"])
        assert cycle is not None
        assert _utc(cycle.period_start) == boundary
        assert _utc(cycle.period_end) == datetime(2026, 9, 30, 16, tzinfo=timezone.utc)


def test_scheduler_closes_due_cycle_and_preserves_existing_nonmidnight_boundaries(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=start, period_end=end
        )
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="scheduler-closed-task",
            settled_at=start + timedelta(days=1),
        )
        result = EnterpriseBillingService.run_once(session, as_of=end + timedelta(hours=2))
        assert result["closed_cycle_ids"] == [cycle.id]
        following = session.get(CompanyBillingCycle, result["opened_cycle_id"])
        assert following is not None
        assert _utc(following.period_start) == end
        assert _utc(following.period_end) == datetime(2026, 10, 1, tzinfo=timezone.utc)
        invoice = session.scalar(select(CompanyInvoice).where(CompanyInvoice.cycle_id == cycle.id))
        assert invoice is not None and invoice.total_cents == 100
        assert _utc(invoice.issued_at) == end + timedelta(hours=2)
        assert EnterpriseBillingService.run_once(
            session, as_of=end + timedelta(hours=3)
        )["processed"] is False
        assert session.scalar(select(func.count(CompanyInvoice.id))) == 1


def test_scheduler_applies_dunning_hold_before_opening_next_credit_cycle(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=start, period_end=end
        )
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="scheduler-overdue-task",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session, company_id=company.id, cycle_id=cycle.id, issued_at=end
        )
        assert invoice.due_at is not None
        result = EnterpriseBillingService.run_once(
            session, as_of=_utc(invoice.due_at) + timedelta(days=1)
        )
        assert result["processed"] is True
        assert result["opened_cycle_id"] is None
        assert session.get(CompanyBillingAccount, company.id).billing_hold is True
        assert invoice.status == EnterpriseInvoiceStatus.OVERDUE
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 1


def test_idle_company_does_not_starve_later_company_in_same_daily_run(app) -> None:
    start = datetime(2026, 7, 31, 16, tzinfo=timezone.utc)
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company_ids = []
        for _ in range(2):
            company, user, _ = _seed_point_company(session)
            _activate(session, company=company, user=user, effective_at=start)
            company_ids.append(company.id)
        first = EnterpriseBillingService.run_once(session, as_of=now)
        second = EnterpriseBillingService.run_once(session, as_of=now + timedelta(minutes=1))
        assert first["processed"] is second["processed"] is True
        assert {first["company_id"], second["company_id"]} == set(company_ids)
        assert EnterpriseBillingService.run_once(session, as_of=now + timedelta(minutes=2))[
            "processed"
        ] is False
        assert session.scalar(select(func.count(EnterpriseDunningRun.id))) == 2
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 2


def test_scheduler_catches_up_missing_months_without_skipping_receivables_or_minting_credit(
    app,
) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        _, lot, _ = EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=start, period_end=end
        )
        # The worker was offline; already authorized points were consumed in
        # September. Catch-up must invoice that month, not jump to November.
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="scheduler-catch-up-task",
            settled_at=end + timedelta(days=1),
        )
        for offset in range(3):
            result = EnterpriseBillingService.run_once(
                session, as_of=now + timedelta(minutes=offset)
            )
            assert result["processed"] is True
            assert len(result["closed_cycle_ids"]) == 1
            assert result["opened_cycle_id"] is not None
        assert EnterpriseBillingService.run_once(
            session, as_of=now + timedelta(minutes=3)
        )["processed"] is False
        cycles = list(
            session.scalars(select(CompanyBillingCycle).order_by(CompanyBillingCycle.period_start))
        )
        assert len(cycles) == 4
        assert all(
            _utc(previous.period_end) == _utc(following.period_start)
            for previous, following in zip(cycles, cycles[1:])
        )
        assert session.scalar(select(func.count(CompanyInvoice.id))) == 3
        assert session.scalar(select(func.sum(CompanyInvoice.total_cents))) == 100
        assert session.get(CompanyBillingAccount, company.id).unbilled_receivable_cents == 0
        assert session.scalar(select(func.count(EnterpriseDunningRun.id))) == 1
        assert session.scalar(
            select(func.sum(CompanyPointLedgerEntry.amount_points)).where(
                CompanyPointLedgerEntry.kind == PointLedgerKind.CREDIT
            )
        ) == 100


def test_scheduler_requires_contract_to_cover_the_entire_new_cycle(app) -> None:
    start = datetime(2026, 7, 31, 16, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, _ = _seed_point_company(session)
        EnterpriseBillingService.activate_contract(
            session,
            company_id=company.id,
            contract_reference="MSA-PARTIAL-COVERAGE",
            currency="CNY",
            timezone_name="Asia/Shanghai",
            cycle_day=1,
            payment_terms_days=30,
            credit_limit_points=100,
            effective_at=start,
            expires_at=start + timedelta(days=20),
            created_by_user_id=user.id,
        )
        result = EnterpriseBillingService.run_once(session, as_of=start + timedelta(days=5))
        assert result["processed"] is True and result["opened_cycle_id"] is None
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 0
        assert session.scalar(select(func.count(CompanyPointLedgerEntry.id))) == 0


def test_scheduler_rejects_corrupt_invoice_and_rolls_back_daily_run(app) -> None:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with app.state.session_factory.begin() as session:
        company, user, model = _seed_point_company(session)
        _activate(session, company=company, user=user, effective_at=start)
        cycle, lot, _ = EnterpriseBillingService.open_cycle(
            session, company_id=company.id, period_start=start, period_end=end
        )
        _settle_contract_task(
            session,
            company=company,
            user=user,
            model=model,
            lot=lot,
            points=10,
            key="scheduler-corrupt-task",
            settled_at=start + timedelta(days=1),
        )
        _, invoice, _, _ = EnterpriseBillingService.close_and_issue_cycle(
            session, company_id=company.id, cycle_id=cycle.id, issued_at=end
        )
        invoice.paid_cents = 100  # missing verified payment/AR credit evidence.
    with pytest.raises(ConflictError):
        with app.state.session_factory.begin() as session:
            EnterpriseBillingService.run_once(session, as_of=end + timedelta(days=1))
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(EnterpriseDunningRun.id))) == 0
        assert session.scalar(select(func.count(CompanyBillingCycle.id))) == 1


def test_scheduler_http_requires_internal_auth_and_accepts_only_empty_json(
    app, client, internal_headers, monkeypatch
) -> None:
    path = "/internal/billing/enterprise/run-once"
    monkeypatch.setattr(
        "platform_api.services.enterprise_billing.utcnow",
        lambda: datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    assert client.post(path, json={}).status_code == 401
    assert client.post(
        path, headers=internal_headers, json={"as_of": "2099-01-01T00:00:00Z"}
    ).status_code == 422
    response = client.post(path, headers=internal_headers, json={})
    assert response.status_code == 200, response.text
    assert response.json()["processed"] is False
