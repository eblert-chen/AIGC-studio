from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select

from platform_api.models import (
    BillingUnit,
    Company,
    CompanyBillingAccount,
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    GenerationTask,
    ModelDefinition,
    PointLedgerKind,
    PointLotSettlementValueAllocation,
    TaskPointLotAllocation,
    TaskStatus,
    new_id,
)


ACTIVATE_PATH = "/internal/billing/enterprise/contracts/activate"
OPEN_PATH = "/internal/billing/enterprise/cycles/open"
DUNNING_PATH = "/internal/billing/enterprise/dunning/run"


def _enable_point_company(app, tenant) -> None:
    with app.state.session_factory.begin() as session:
        company = session.get(Company, tenant["company_id"])
        assert company is not None
        company.billing_version = 2
        if session.get(CompanyPointWalletAccount, company.id) is None:
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


def _activation_body(tenant, start: datetime, *, credit_limit_points: int = 100):
    return {
        "company_id": tenant["company_id"],
        "contract_reference": "MSA-API-2026-001",
        "currency": "CNY",
        "timezone_name": "Asia/Shanghai",
        "cycle_day": 1,
        "payment_terms_days": 30,
        "credit_limit_points": credit_limit_points,
        "effective_at": start.isoformat(),
        "expires_at": None,
        "created_by_user_id": tenant["user_id"],
    }


def _open_body(tenant, start: datetime, end: datetime):
    return {
        "company_id": tenant["company_id"],
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def _activate_and_open(client, tenant, internal_headers, start, end):
    activated = client.post(
        ACTIVATE_PATH,
        headers=internal_headers,
        json=_activation_body(tenant, start),
    )
    assert activated.status_code == 200, activated.text
    opened = client.post(
        OPEN_PATH,
        headers=internal_headers,
        json=_open_body(tenant, start, end),
    )
    assert opened.status_code == 200, opened.text
    return activated.json(), opened.json()


def _append_contract_settlement(
    session,
    *,
    company_id: str,
    user_id: str,
    model: ModelDefinition,
    lot: CompanyPointLot,
    points: int,
    key: str,
    settled_at: datetime,
) -> tuple[str, str]:
    task = GenerationTask(
        id=new_id(),
        company_id=company_id,
        personal_workspace_id=None,
        user_id=user_id,
        model_id=model.id,
        idempotency_key=key,
        request_fingerprint=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        status=TaskStatus.SUCCEEDED,
        request_payload={"prompt": key, "output_count": 1},
        billing_unit=BillingUnit.POINT,
        billing_version=2,
        quote_cents=None,
        quote_points=points,
        pricing_snapshot={
            "schema_version": 2,
            "billing_unit": BillingUnit.POINT.value,
            "billing_version": 2,
            "charge_policy": "FIXED_QUOTE_ON_SUCCESS",
            "unit_price_points": points,
            "quantity": 1,
            "quote_points": points,
        },
        capability_snapshot={},
        reserved_cents=0,
        reserved_points=0,
        actual_cost_cents=None,
        actual_cost_points=points,
    )
    session.add(task)
    session.flush()
    allocation = TaskPointLotAllocation(
        id=new_id(),
        company_id=company_id,
        task_id=task.id,
        lot_id=lot.id,
        allocated_points=points,
        reserved_points=0,
        settled_points=points,
        released_points=0,
    )
    reserve = CompanyPointLedgerEntry(
        company_id=company_id,
        kind=PointLedgerKind.RESERVE,
        amount_points=points,
        available_delta_points=-points,
        reserved_delta_points=points,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key=f"reserve:{key}",
        task_id=task.id,
        created_at=settled_at - timedelta(microseconds=1),
    )
    settle = CompanyPointLedgerEntry(
        id=new_id(),
        company_id=company_id,
        kind=PointLedgerKind.SETTLE,
        amount_points=points,
        available_delta_points=0,
        reserved_delta_points=-points,
        reversal_reserved_delta_points=0,
        debt_delta_points=0,
        idempotency_key=f"settle:{key}",
        task_id=task.id,
        created_at=settled_at,
    )
    value = PointLotSettlementValueAllocation(
        id=new_id(),
        company_id=company_id,
        personal_workspace_id=None,
        task_id=task.id,
        company_task_allocation_id=allocation.id,
        personal_task_allocation_id=None,
        company_settle_ledger_id=settle.id,
        personal_settle_ledger_id=None,
        settled_points=points,
        cash_basis_cents=0,
        receivable_basis_cents=points * 10,
        subsidy_cents=0,
        created_at=settled_at,
    )
    session.add_all([allocation, reserve, settle, value])
    wallet = session.get(CompanyPointWalletAccount, company_id)
    account = session.get(CompanyBillingAccount, company_id)
    assert wallet is not None
    assert account is not None
    assert lot.available_points >= points
    lot.available_points -= points
    lot.settled_points += points
    wallet.available_points -= points
    account.unbilled_receivable_cents += points * 10
    session.flush()
    return task.id, value.id


def test_internal_mutations_require_service_auth_and_strict_aware_bodies(
    app,
    client,
    tenant,
    internal_headers,
) -> None:
    _enable_point_company(app, tenant)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    body = _activation_body(tenant, start)

    unauthenticated = client.post(ACTIVATE_PATH, json=body)
    assert unauthenticated.status_code == 401
    unauthenticated_open = client.post(
        OPEN_PATH,
        json={
            "company_id": tenant["company_id"],
            "period_start": start.isoformat(),
            "period_end": (start + timedelta(days=31)).isoformat(),
        },
    )
    assert unauthenticated_open.status_code == 401
    unauthenticated_close = client.post(
        "/internal/billing/enterprise/cycles/missing/close-and-issue",
        json={"company_id": tenant["company_id"], "issued_at": start.isoformat()},
    )
    assert unauthenticated_close.status_code == 401
    unauthenticated_dunning = client.post(
        DUNNING_PATH,
        json={
            "company_id": tenant["company_id"],
            "as_of": start.isoformat(),
            "idempotency_key": "unauthenticated-dunning",
        },
    )
    assert unauthenticated_dunning.status_code == 401

    unknown_field = client.post(
        ACTIVATE_PATH,
        headers=internal_headers,
        json={**body, "mode": "POSTPAID"},
    )
    assert unknown_field.status_code == 422
    assert unknown_field.json()["detail"][0]["type"] == "extra_forbidden"

    naive_time = client.post(
        ACTIVATE_PATH,
        headers=internal_headers,
        json={**body, "effective_at": "2026-08-01T00:00:00"},
    )
    assert naive_time.status_code == 422
    assert naive_time.json()["detail"][0]["type"] == "timezone_aware"

    naive_period = client.post(
        OPEN_PATH,
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "period_start": "2026-08-01T00:00:00",
            "period_end": "2026-09-01T00:00:00+00:00",
        },
    )
    assert naive_period.status_code == 422
    assert naive_period.json()["detail"][0]["type"] == "timezone_aware"
    naive_dunning = client.post(
        DUNNING_PATH,
        headers=internal_headers,
        json={
            "company_id": tenant["company_id"],
            "as_of": "2026-09-01T00:00:00",
            "idempotency_key": "naive-dunning-key",
        },
    )
    assert naive_dunning.status_code == 422
    assert naive_dunning.json()["detail"][0]["type"] == "timezone_aware"


def test_contract_and_cycle_internal_endpoints_are_idempotent_and_truthful(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
) -> None:
    _enable_point_company(app, tenant)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    activation_body = _activation_body(tenant, start)

    first_contract = client.post(
        ACTIVATE_PATH,
        headers=internal_headers,
        json=activation_body,
    )
    replay_contract = client.post(
        ACTIVATE_PATH,
        headers=internal_headers,
        json=activation_body,
    )
    assert first_contract.status_code == replay_contract.status_code == 200
    assert first_contract.json()["created"] is True
    assert replay_contract.json()["created"] is False
    assert (
        replay_contract.json()["contract"]["id"]
        == first_contract.json()["contract"]["id"]
    )
    assert first_contract.json()["contract"]["receivable_per_point_cents"] == 10

    first_cycle = client.post(
        OPEN_PATH,
        headers=internal_headers,
        json=_open_body(tenant, start, end),
    )
    replay_cycle = client.post(
        OPEN_PATH,
        headers=internal_headers,
        json=_open_body(tenant, start, end),
    )
    assert first_cycle.status_code == replay_cycle.status_code == 200
    assert first_cycle.json()["created"] is True
    assert replay_cycle.json()["created"] is False
    assert replay_cycle.json()["cycle"]["id"] == first_cycle.json()["cycle"]["id"]
    assert replay_cycle.json()["credit_lot"]["id"] == first_cycle.json()["credit_lot"]["id"]
    lot = first_cycle.json()["credit_lot"]
    assert lot["source_kind"] == "contract"
    assert lot["original_points"] == 100
    assert lot["cash_basis_cents"] == 0
    assert lot["receivable_basis_cents"] == 1_000
    assert lot["subsidy_cents"] == 0

    listed = client.get(
        f"/api/v1/companies/{tenant['company_id']}/billing/cycles",
        headers=tenant_headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["status"] == "open"
    assert listed.json()["items"][0]["invoice_id"] is None


def test_company_billing_reads_require_billing_manage(
    app,
    client,
    tenant,
    tenant_headers,
) -> None:
    _enable_point_company(app, tenant)
    company_id = tenant["company_id"]
    owner_read = client.get(
        f"/api/v1/companies/{company_id}/billing/invoices",
        headers=tenant_headers,
    )
    assert owner_read.status_code == 200
    assert owner_read.json()["total"] == 0

    member_response = client.post(
        f"/api/v1/companies/{company_id}/members",
        headers=tenant_headers,
        json={
            "email": f"billing-denied-{uuid.uuid4().hex}@example.com",
            "display_name": "Billing denied",
        },
    )
    assert member_response.status_code == 201, member_response.text
    member = member_response.json()
    denied = client.put(
        (
            f"/api/v1/companies/{company_id}/members/"
            f"{member['membership_id']}/permission"
        ),
        headers=tenant_headers,
        json={"permission_code": "billing.manage", "effect": "deny"},
    )
    assert denied.status_code == 200, denied.text
    member_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": member["user_id"],
    }
    for suffix in ("cycles", "invoices"):
        response = client.get(
            f"/api/v1/companies/{company_id}/billing/{suffix}",
            headers=member_headers,
        )
        assert response.status_code == 403


def test_close_endpoint_and_reads_preserve_half_open_cycle_and_replay(
    app,
    client,
    tenant,
    tenant_headers,
    internal_headers,
) -> None:
    _enable_point_company(app, tenant)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    _, opened = _activate_and_open(
        client,
        tenant,
        internal_headers,
        start,
        end,
    )
    cycle_id = opened["cycle"]["id"]
    lot_id = opened["credit_lot"]["id"]

    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug=f"enterprise-api-{uuid.uuid4().hex}",
            display_name="Enterprise API model",
            provider_key="enterprise-api-test",
            billing_mode="per_item",
        )
        session.add(model)
        session.flush()
        lot = session.get(CompanyPointLot, lot_id)
        assert lot is not None
        at_start_task_id, at_start_value_id = _append_contract_settlement(
            session,
            company_id=tenant["company_id"],
            user_id=tenant["user_id"],
            model=model,
            lot=lot,
            points=12,
            key="enterprise-api-at-start",
            settled_at=start,
        )
        at_end_task_id, at_end_value_id = _append_contract_settlement(
            session,
            company_id=tenant["company_id"],
            user_id=tenant["user_id"],
            model=model,
            lot=lot,
            points=7,
            key="enterprise-api-at-end",
            settled_at=end,
        )

    close_path = (
        f"/internal/billing/enterprise/cycles/{cycle_id}/close-and-issue"
    )
    close_body = {
        "company_id": tenant["company_id"],
        "issued_at": end.isoformat(),
    }
    first_close = client.post(
        close_path,
        headers=internal_headers,
        json=close_body,
    )
    assert first_close.status_code == 200, first_close.text
    first_payload = first_close.json()
    assert first_payload["issued"] is True
    assert first_payload["cycle"]["status"] == "issued"
    assert first_payload["invoice"]["subtotal_cents"] == 120
    assert first_payload["invoice"]["total_cents"] == 120
    assert first_payload["invoice"]["paid_cents"] == 0
    assert first_payload["invoice"]["outstanding_cents"] == 120
    assert len(first_payload["lines"]) == 1
    assert first_payload["lines"][0]["task_id"] == at_start_task_id
    assert first_payload["lines"][0]["value_allocation_id"] == at_start_value_id
    assert first_payload["lines"][0]["points"] == 12
    assert at_end_task_id not in {line["task_id"] for line in first_payload["lines"]}
    assert at_end_value_id not in {
        line["value_allocation_id"] for line in first_payload["lines"]
    }

    replay = client.post(
        close_path,
        headers=internal_headers,
        json={
            **close_body,
            "issued_at": (end + timedelta(minutes=5)).isoformat(),
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["issued"] is False
    assert replay.json()["invoice"]["id"] == first_payload["invoice"]["id"]
    assert replay.json()["lines"] == first_payload["lines"]

    cycles = client.get(
        f"/api/v1/companies/{tenant['company_id']}/billing/cycles",
        headers=tenant_headers,
    )
    assert cycles.status_code == 200, cycles.text
    assert cycles.json()["total"] == 1
    assert cycles.json()["items"][0]["invoice_id"] == first_payload["invoice"]["id"]

    invoices = client.get(
        f"/api/v1/companies/{tenant['company_id']}/billing/invoices",
        headers=tenant_headers,
    )
    assert invoices.status_code == 200, invoices.text
    assert invoices.json()["total"] == 1
    assert invoices.json()["items"][0]["total_cents"] == 120
    detail = client.get(
        (
            f"/api/v1/companies/{tenant['company_id']}/billing/invoices/"
            f"{first_payload['invoice']['id']}"
        ),
        headers=tenant_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["invoice"] == invoices.json()["items"][0]
    assert detail.json()["lines"] == first_payload["lines"]

    with app.state.session_factory() as session:
        account = session.get(CompanyBillingAccount, tenant["company_id"])
        assert account is not None
        assert account.unbilled_receivable_cents == 70
        invoiced_values = set(
            session.scalars(
                select(PointLotSettlementValueAllocation.id).where(
                    PointLotSettlementValueAllocation.id.in_(
                        {at_start_value_id, at_end_value_id}
                    )
                )
            ).all()
        )
        assert invoiced_values == {at_start_value_id, at_end_value_id}

    dunning_body = {
        "company_id": tenant["company_id"],
        "as_of": (end + timedelta(days=30)).isoformat(),
        "idempotency_key": f"enterprise-api-dunning-{cycle_id}",
    }
    dunning = client.post(
        DUNNING_PATH,
        headers=internal_headers,
        json=dunning_body,
    )
    assert dunning.status_code == 200, dunning.text
    dunning_payload = dunning.json()
    assert dunning_payload["created"] is True
    assert dunning_payload["status"] == "completed"
    assert dunning_payload["scanned_count"] == 1
    assert dunning_payload["overdue_count"] == 1
    assert dunning_payload["hold_count"] == 1
    assert dunning_payload["current_billing_hold"] is True
    assert (
        dunning_payload["current_billing_hold_reason"] == "delinquent_invoice"
    )
    assert dunning_payload["current_dunning_level"] == 1
    assert {action["action"] for action in dunning_payload["actions"]} == {
        "mark_overdue",
        "apply_hold",
    }

    dunning_replay = client.post(
        DUNNING_PATH,
        headers=internal_headers,
        json=dunning_body,
    )
    assert dunning_replay.status_code == 200, dunning_replay.text
    replay_payload = dunning_replay.json()
    assert replay_payload["created"] is False
    assert replay_payload["run_id"] == dunning_payload["run_id"]
    assert replay_payload["intent_sha256"] == dunning_payload["intent_sha256"]
    assert replay_payload["actions"] == dunning_payload["actions"]

    conflicting_replay = client.post(
        DUNNING_PATH,
        headers=internal_headers,
        json={
            **dunning_body,
            "as_of": (end + timedelta(days=30, seconds=1)).isoformat(),
        },
    )
    assert conflicting_replay.status_code == 409
