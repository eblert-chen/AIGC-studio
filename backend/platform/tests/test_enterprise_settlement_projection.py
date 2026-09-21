from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select

from platform_api.models import (
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyMembership,
    CompanyPointWalletAccount,
    GenerationTask,
    ModelDefinition,
    PointLotSettlementValueAllocation,
    TaskStatus,
    User,
    UserAccountType,
)
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.enterprise_billing import EnterpriseBillingService


def test_contract_point_settlement_projects_unbilled_receivable_in_same_transaction(app, monkeypatch) -> None:
    contract_timezone = timezone(timedelta(hours=8))
    period_start = datetime(2026, 8, 1, tzinfo=contract_timezone)
    period_end = datetime(2026, 9, 1, tzinfo=contract_timezone)
    # Admission must happen inside this fixture's open contract cycle.
    # The production clock deliberately rejects historical/expired credit.
    monkeypatch.setattr(
        "platform_api.services.company_points_billing.utcnow",
        lambda: period_start + timedelta(days=14),
    )
    with app.state.session_factory.begin() as session:
        suffix = uuid4().hex
        company = Company(name=f"Settlement projection {suffix}", billing_version=2)
        user = User(
            email=f"settlement-projection-{suffix}@example.test",
            display_name="Enterprise operator",
            account_type=UserAccountType.COMPANY,
        )
        model = ModelDefinition(
            slug=f"settlement-projection-{suffix}",
            display_name="Settlement projection",
            provider_key="enterprise-projection-test",
            billing_mode="per_item",
        )
        session.add_all([company, user, model])
        session.flush()
        session.add(CompanyMembership(company_id=company.id, user_id=user.id))
        session.add(
            CompanyPointWalletAccount(
                company_id=company.id,
                available_points=0,
                reserved_points=0,
                reversal_reserved_points=0,
                debt_points=0,
                migration_idempotency_key=f"native-v2:{company.id}",
                migrated_from_available_cents=0,
                migration_remainder_cents=0,
                migration_rounding_grant_points=0,
            )
        )
        session.flush()
        EnterpriseBillingService.activate_contract(
            session,
            company_id=company.id,
            contract_reference="MSA-PROJECTION-001",
            currency="CNY",
            timezone_name="Asia/Shanghai",
            cycle_day=1,
            payment_terms_days=30,
            credit_limit_points=10,
            effective_at=period_start - timedelta(days=1),
            created_by_user_id=user.id,
        )
        EnterpriseBillingService.open_cycle(
            session,
            company_id=company.id,
            period_start=period_start,
            period_end=period_end,
        )
        task = GenerationTask(
            company_id=company.id,
            personal_workspace_id=None,
            user_id=user.id,
            model_id=model.id,
            idempotency_key="contract-task-projection-0001",
            request_fingerprint="a" * 64,
            status=TaskStatus.DRAFT,
            request_payload={"prompt": "project receivable", "output_count": 1},
            billing_unit=BillingUnit.POINT,
            billing_version=2,
            quote_cents=None,
            quote_points=2,
            pricing_snapshot={
                "schema_version": 2,
                "billing_unit": "point",
                "billing_version": 2,
                "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
                "unit_price_points": 2,
                "quantity": 1,
                "quote_points": 2,
            },
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=None,
            actual_cost_points=None,
        )
        session.add(task)
        session.flush()
        CompanyPointBillingService.reserve(
            session,
            company_id=company.id,
            task_id=task.id,
            amount_points=2,
            idempotency_key="contract-task-projection-reserve",
        )
        CompanyPointBillingService.settle_success(
            session,
            company_id=company.id,
            task_id=task.id,
            actual_cost_points=2,
            idempotency_key="contract-task-projection-settle",
        )
        account = session.get(CompanyBillingAccount, company.id)
        assert account is not None
        assert account.unbilled_receivable_cents == 20
        assert session.scalar(
            select(func.sum(PointLotSettlementValueAllocation.receivable_basis_cents))
        ) == 20
