from __future__ import annotations

from sqlalchemy import func, select

import pytest

from platform_api.models import (
    AuditLog,
    CompanyModelGrant,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelCapability,
    ModelDefinition,
    WalletAccount,
)
from platform_api.services.billing import WalletService

from .conftest import TEST_RELAY_CAPABILITY_REVISION, bootstrap
from .test_model_capability_v1_contract import _mode, canonical_capability


def _member(client, tenant: dict[str, str], headers: dict[str, str], suffix: str):
    email_suffix = suffix.lower().replace(" ", "-")
    response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=headers,
        json={
            "email": f"billing-{email_suffix}@example.com",
            "display_name": f"Billing {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _admin(client, suffix: str):
    response = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": f"billing-admin-{suffix}@example.com",
            "display_name": f"Billing Admin {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["user_id"]
    return user_id, {"X-Platform-Admin-User-ID": user_id}


def _model_with_grants(
    app,
    *,
    slug: str,
    grants: list[dict[str, object]],
    capability_config: dict[str, object],
) -> str:
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug=slug,
            display_name=slug.replace("-", " ").title(),
            provider_key="billing-test-provider",
            relay_capability_revision=TEST_RELAY_CAPABILITY_REVISION,
            billing_mode=(
                "per_second"
                if grants[0].get("price_per_second_cents") is not None
                else "per_item"
            ),
        )
        session.add(model)
        session.flush()
        session.add(
            ModelCapability(
                model_id=model.id,
                capability_key="generation",
                config=capability_config,
            )
        )
        for grant in grants:
            session.add(
                CompanyModelGrant(
                    company_id=str(grant["company_id"]),
                    model_id=model.id,
                    enabled=True,
                    price_per_second_cents=grant.get("price_per_second_cents"),
                    price_per_item_cents=grant.get("price_per_item_cents"),
                )
            )
        return model.id


def _recharge(client, company_id: str, headers: dict[str, str], **body):
    response = client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=headers,
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_task(
    client,
    company_id: str,
    headers: dict[str, str],
    *,
    model_id: str,
    idempotency_key: str,
    request_payload: dict[str, object],
):
    response = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers=headers,
        json={
            "model_id": model_id,
            "idempotency_key": idempotency_key,
            "request_payload": request_payload,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _settle(app, company_id: str, task_id: str, amount_cents: int, suffix: str):
    with app.state.session_factory.begin() as session:
        WalletService.settle_success(
            session,
            company_id=company_id,
            task_id=task_id,
            actual_cost_cents=amount_cents,
            idempotency_key=f"billing-settle-{suffix}",
        )


def test_two_employees_consume_one_company_wallet_and_keep_employee_attribution(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    first_member = _member(client, tenant, tenant_headers, "Shared Wallet One")
    second_member = _member(client, tenant, tenant_headers, "Shared Wallet Two")
    first_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": first_member["user_id"],
    }
    second_headers = {
        "X-Company-ID": company_id,
        "X-User-ID": second_member["user_id"],
    }
    model_id = _model_with_grants(
        app,
        slug="shared-wallet-item-model",
        grants=[
            {
                "company_id": company_id,
                "price_per_item_cents": 200,
            }
        ],
        capability_config={"max_outputs": 1},
    )
    _recharge(
        client,
        company_id,
        tenant_headers,
        amount_cents=1000,
        idempotency_key="shared-wallet-recharge",
        note="one company wallet",
    )

    first_task = _create_task(
        client,
        company_id,
        first_headers,
        model_id=model_id,
        idempotency_key="shared-wallet-task-one",
        request_payload={"prompt": "first employee", "output_count": 1},
    )
    second_task = _create_task(
        client,
        company_id,
        second_headers,
        model_id=model_id,
        idempotency_key="shared-wallet-task-two",
        request_payload={"prompt": "second employee", "output_count": 1},
    )
    _settle(app, company_id, first_task["id"], 150, "shared-one")
    _settle(app, company_id, second_task["id"], 175, "shared-two")

    wallet = client.get(
        f"/api/v1/companies/{company_id}/wallet", headers=tenant_headers
    )
    assert wallet.status_code == 200, wallet.text
    assert wallet.json() == {
        "company_id": company_id,
        "available_cents": 675,
        "reserved_cents": 0,
    }

    report = client.get(
        f"/api/v1/companies/{company_id}/reports/consumption",
        headers=tenant_headers,
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["total"] == 2
    assert body["total_amount_cents"] == 325
    by_employee = {item["employee_user_id"]: item for item in body["items"]}
    assert set(by_employee) == {
        first_member["user_id"],
        second_member["user_id"],
    }
    assert by_employee[first_member["user_id"]]["amount_cents"] == 150
    assert by_employee[second_member["user_id"]]["amount_cents"] == 175
    assert all(item["company_id"] == company_id for item in body["items"])
    assert all(item["pricing_mode"] == "per_item" for item in body["items"])
    assert all(item["unit_price_cents"] == 200 for item in body["items"])
    assert all(item["quantity"] == 1 for item in body["items"])

    for member, expected_amount in (
        (first_member, 150),
        (second_member, 175),
    ):
        filtered = client.get(
            f"/api/v1/companies/{company_id}/reports/consumption",
            headers=tenant_headers,
            params={"employee_user_id": member["user_id"]},
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == 1
        assert filtered.json()["total_amount_cents"] == expected_amount

    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(WalletAccount.company_id)).where(
                WalletAccount.company_id == company_id
            )
        ) == 1
        task_users = set(
            session.scalars(
                select(GenerationTask.user_id).where(
                    GenerationTask.id.in_([first_task["id"], second_task["id"]])
                )
            ).all()
        )
        assert task_users == {
            first_member["user_id"],
            second_member["user_id"],
        }


def test_recharge_records_are_idempotent_paginated_time_filtered_and_scoped(
    app, client, tenant, tenant_headers
):
    company_id = tenant["company_id"]
    first = _recharge(
        client,
        company_id,
        tenant_headers,
        amount_cents=100,
        idempotency_key="recharge-detail-one",
        note="first recharge",
    )
    replay = _recharge(
        client,
        company_id,
        tenant_headers,
        amount_cents=100,
        idempotency_key="recharge-detail-one",
        note="first recharge",
    )
    assert replay["ledger_entry"]["id"] == first["ledger_entry"]["id"]
    assert replay["wallet"]["available_cents"] == 100
    second = _recharge(
        client,
        company_id,
        tenant_headers,
        amount_cents=200,
        idempotency_key="recharge-detail-two",
        note="second recharge",
    )
    third = _recharge(
        client,
        company_id,
        tenant_headers,
        amount_cents=300,
        idempotency_key="recharge-detail-three",
        note="third recharge",
    )

    model_id = _model_with_grants(
        app,
        slug="recharge-details-non-recharge-ledger",
        grants=[{"company_id": company_id, "price_per_item_cents": 50}],
        capability_config={"max_outputs": 1},
    )
    _create_task(
        client,
        company_id,
        tenant_headers,
        model_id=model_id,
        idempotency_key="recharge-detail-reserve-task",
        request_payload={"prompt": "reserve is not a recharge", "output_count": 1},
    )

    recharges_url = f"/api/v1/companies/{company_id}/wallet/recharges"
    first_page = client.get(
        recharges_url,
        headers=tenant_headers,
        params={"page": 1, "page_size": 2},
    )
    assert first_page.status_code == 200, first_page.text
    first_body = first_page.json()
    assert first_body["page"] == 1
    assert first_body["page_size"] == 2
    assert first_body["total"] == 3
    assert first_body["total_amount_cents"] == 600
    assert len(first_body["items"]) == 2
    assert all(item["kind"] == "recharge" for item in first_body["items"])

    second_page = client.get(
        recharges_url,
        headers=tenant_headers,
        params={"page": 2, "page_size": 2},
    )
    assert second_page.status_code == 200, second_page.text
    second_body = second_page.json()
    assert second_body["total"] == 3
    assert len(second_body["items"]) == 1
    listed_ids = {
        item["id"] for item in first_body["items"] + second_body["items"]
    }
    assert listed_ids == {
        first["ledger_entry"]["id"],
        second["ledger_entry"]["id"],
        third["ledger_entry"]["id"],
    }
    assert {
        item["note"] for item in first_body["items"] + second_body["items"]
    } == {"first recharge", "second recharge", "third recharge"}

    entire_window = client.get(
        recharges_url,
        headers=tenant_headers,
        params={
            "start_time": "2020-01-01T00:00:00Z",
            "end_time": "2100-01-01T00:00:00Z",
        },
    )
    assert entire_window.status_code == 200, entire_window.text
    assert entire_window.json()["total"] == 3
    future = client.get(
        recharges_url,
        headers=tenant_headers,
        params={"start_time": "2100-01-01T00:00:00Z"},
    )
    assert future.status_code == 200, future.text
    assert future.json()["total"] == 0
    old = client.get(
        recharges_url,
        headers=tenant_headers,
        params={"end_time": "2020-01-01T00:00:00Z"},
    )
    assert old.status_code == 200, old.text
    assert old.json()["total"] == 0
    reversed_range = client.get(
        recharges_url,
        headers=tenant_headers,
        params={
            "start_time": "2026-08-02T00:00:00Z",
            "end_time": "2026-08-01T00:00:00Z",
        },
    )
    assert reversed_range.status_code == 422

    member = _member(client, tenant, tenant_headers, "Recharge No Permission")
    denied = client.get(
        recharges_url,
        headers={
            "X-Company-ID": company_id,
            "X-User-ID": member["user_id"],
        },
    )
    assert denied.status_code == 403

    other = bootstrap(client, "recharge-details-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    other_records = client.get(
        f"/api/v1/companies/{other['company_id']}/wallet/recharges",
        headers=other_headers,
    )
    assert other_records.status_code == 200, other_records.text
    assert other_records.json()["total"] == 0
    forged = client.get(recharges_url, headers=other_headers)
    assert forged.status_code == 403

    conflict = client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 999,
            "idempotency_key": "recharge-detail-one",
            "note": "different operation",
        },
    )
    assert conflict.status_code == 409
    changed_note = client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 100,
            "idempotency_key": "recharge-detail-one",
            "note": "same amount but different note",
        },
    )
    assert changed_note.status_code == 409


def test_ledger_entries_reject_orm_update_and_delete(
    app, client, tenant, tenant_headers
):
    created = _recharge(
        client,
        tenant["company_id"],
        tenant_headers,
        amount_cents=450,
        idempotency_key="immutable-ledger-recharge",
        note="original note",
    )
    ledger_id = created["ledger_entry"]["id"]

    with pytest.raises(RuntimeError, match="ledger entries are immutable"):
        with app.state.session_factory.begin() as session:
            entry = session.get(LedgerEntry, ledger_id)
            entry.note = "tampered note"

    with pytest.raises(RuntimeError, match="ledger entries are immutable"):
        with app.state.session_factory.begin() as session:
            entry = session.get(LedgerEntry, ledger_id)
            session.delete(entry)

    with app.state.session_factory() as session:
        preserved = session.get(LedgerEntry, ledger_id)
        assert preserved is not None
        assert preserved.note == "original note"
        assert preserved.amount_cents == 450


def test_admin_recharge_idempotent_replay_creates_one_ledger_and_one_audit(
    app, client, tenant
):
    admin_id, admin_headers = _admin(client, "recharge-replay")
    url = (
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/recharge"
    )
    body = {
        "amount_cents": 777,
        "idempotency_key": "admin-recharge-replay-key",
        "note": "manual finance adjustment",
    }
    first = client.post(
        url,
        headers={**admin_headers, "X-Request-ID": "admin-recharge-original"},
        json=body,
    )
    replay = client.post(
        url,
        headers={**admin_headers, "X-Request-ID": "admin-recharge-retry"},
        json=body,
    )
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["ledger_entry"]["id"] == first.json()["ledger_entry"]["id"]
    assert replay.json()["wallet"]["available_cents"] == 777

    conflicting_replay = client.post(
        url,
        headers={**admin_headers, "X-Request-ID": "admin-recharge-conflict"},
        json={**body, "amount_cents": 778},
    )
    assert conflicting_replay.status_code == 409

    with app.state.session_factory() as session:
        ledger_count = session.scalar(
            select(func.count(LedgerEntry.id)).where(
                LedgerEntry.company_id == tenant["company_id"],
                LedgerEntry.kind == LedgerKind.RECHARGE,
            )
        )
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "company.wallet.recharge",
                    AuditLog.target_id == tenant["company_id"],
                )
            ).all()
        )
        assert ledger_count == 1
        assert len(audits) == 1
        assert audits[0].actor_user_id == admin_id
        assert audits[0].request_id == "admin-recharge-original"
        assert audits[0].before_summary == {"available_cents": 0}
        assert audits[0].after_summary["available_cents"] == 777
        assert audits[0].after_summary["ledger_entry_id"] == first.json()[
            "ledger_entry"
        ]["id"]


def test_admin_consumption_report_filters_company_employee_model_and_time(
    app, client
):
    tenant_a = bootstrap(client, "admin-report-a")
    tenant_b = bootstrap(client, "admin-report-b")
    headers_a = {
        "X-Company-ID": tenant_a["company_id"],
        "X-User-ID": tenant_a["user_id"],
    }
    headers_b = {
        "X-Company-ID": tenant_b["company_id"],
        "X-User-ID": tenant_b["user_id"],
    }
    alpha = _member(client, tenant_a, headers_a, "Alpha Operator")
    beta = _member(client, tenant_a, headers_a, "Beta Operator")
    alpha_headers = {
        "X-Company-ID": tenant_a["company_id"],
        "X-User-ID": alpha["user_id"],
    }
    beta_headers = {
        "X-Company-ID": tenant_a["company_id"],
        "X-User-ID": beta["user_id"],
    }
    item_model_id = _model_with_grants(
        app,
        slug="admin-report-item-model",
        grants=[
            {
                "company_id": tenant_a["company_id"],
                "price_per_item_cents": 300,
            },
            {
                "company_id": tenant_b["company_id"],
                "price_per_item_cents": 300,
            },
        ],
        capability_config={"max_outputs": 1},
    )
    second_model_id = _model_with_grants(
        app,
        slug="admin-report-second-model",
        grants=[
            {
                "company_id": tenant_a["company_id"],
                "price_per_second_cents": 50,
            }
        ],
        capability_config={"durations": [4]},
    )
    _recharge(
        client,
        tenant_a["company_id"],
        headers_a,
        amount_cents=1000,
        idempotency_key="admin-report-recharge-a",
    )
    _recharge(
        client,
        tenant_b["company_id"],
        headers_b,
        amount_cents=1000,
        idempotency_key="admin-report-recharge-b",
    )
    alpha_task = _create_task(
        client,
        tenant_a["company_id"],
        alpha_headers,
        model_id=item_model_id,
        idempotency_key="admin-report-alpha-task",
        request_payload={"prompt": "alpha", "output_count": 1},
    )
    beta_task = _create_task(
        client,
        tenant_a["company_id"],
        beta_headers,
        model_id=second_model_id,
        idempotency_key="admin-report-beta-task",
        request_payload={"prompt": "beta", "duration_seconds": 4},
    )
    tenant_b_task = _create_task(
        client,
        tenant_b["company_id"],
        headers_b,
        model_id=item_model_id,
        idempotency_key="admin-report-company-b-task",
        request_payload={"prompt": "company b", "output_count": 1},
    )
    _settle(app, tenant_a["company_id"], alpha_task["id"], 120, "admin-alpha")
    _settle(app, tenant_a["company_id"], beta_task["id"], 190, "admin-beta")
    _settle(
        app,
        tenant_b["company_id"],
        tenant_b_task["id"],
        230,
        "admin-company-b",
    )

    _, admin_headers = _admin(client, "consumption-report")
    url = "/api/v1/platform-admin/reports/consumption"
    page = client.get(
        url,
        headers=admin_headers,
        params={"page": 1, "page_size": 2},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 3
    assert page.json()["total_amount_cents"] == 540
    assert len(page.json()["items"]) == 2

    company_a = client.get(
        url,
        headers=admin_headers,
        params={"company_id": tenant_a["company_id"]},
    )
    assert company_a.status_code == 200, company_a.text
    assert company_a.json()["total"] == 2
    assert company_a.json()["total_amount_cents"] == 310
    assert {
        item["company_id"] for item in company_a.json()["items"]
    } == {tenant_a["company_id"]}
    assert {
        item["employee_user_id"] for item in company_a.json()["items"]
    } == {alpha["user_id"], beta["user_id"]}

    company_b = client.get(
        url,
        headers=admin_headers,
        params={"company_id": tenant_b["company_id"]},
    )
    assert company_b.status_code == 200, company_b.text
    assert company_b.json()["total"] == 1
    assert company_b.json()["total_amount_cents"] == 230
    assert company_b.json()["items"][0]["task_id"] == tenant_b_task["id"]

    employee = client.get(
        url,
        headers=admin_headers,
        params={"employee_user_id": alpha["user_id"]},
    )
    assert employee.status_code == 200, employee.text
    assert employee.json()["total"] == 1
    assert employee.json()["total_amount_cents"] == 120
    assert employee.json()["items"][0]["task_id"] == alpha_task["id"]

    employee_query = client.get(
        url,
        headers=admin_headers,
        params={"employee_query": "alpha operator"},
    )
    assert employee_query.status_code == 200, employee_query.text
    assert employee_query.json()["total"] == 1
    assert employee_query.json()["items"][0]["employee_user_id"] == alpha[
        "user_id"
    ]

    item_model = client.get(
        url,
        headers=admin_headers,
        params={"model_id": item_model_id},
    )
    assert item_model.status_code == 200, item_model.text
    assert item_model.json()["total"] == 2
    assert item_model.json()["total_amount_cents"] == 350
    assert {
        item["company_id"] for item in item_model.json()["items"]
    } == {tenant_a["company_id"], tenant_b["company_id"]}

    combined = client.get(
        url,
        headers=admin_headers,
        params={
            "company_id": tenant_a["company_id"],
            "employee_user_id": beta["user_id"],
            "model_id": second_model_id,
            "start_time": "2020-01-01T00:00:00Z",
            "end_time": "2100-01-01T00:00:00Z",
        },
    )
    assert combined.status_code == 200, combined.text
    assert combined.json()["total"] == 1
    assert combined.json()["total_amount_cents"] == 190
    row = combined.json()["items"][0]
    assert row["task_id"] == beta_task["id"]
    assert row["company_name"] == "Company admin-report-a"
    assert row["pricing_mode"] == "per_second"
    assert row["unit_price_cents"] == 50
    assert row["quantity"] == 4

    future = client.get(
        url,
        headers=admin_headers,
        params={"start_time": "2100-01-01T00:00:00Z"},
    )
    assert future.status_code == 200, future.text
    assert future.json()["total"] == 0
    reversed_range = client.get(
        url,
        headers=admin_headers,
        params={
            "start_time": "2026-08-02T00:00:00Z",
            "end_time": "2026-08-01T00:00:00Z",
        },
    )
    assert reversed_range.status_code == 422

    owner_cannot_impersonate_admin = client.get(
        url,
        headers={"X-Platform-Admin-User-ID": tenant_a["user_id"]},
    )
    assert owner_cannot_impersonate_admin.status_code == 403


def test_model_billing_mode_change_is_versioned_and_frozen_after_grant(
    client, tenant
):
    _, admin_headers = _admin(client, "billing-mode-lifecycle")
    capabilities = [
        {
            "key": "generation",
            "config": canonical_capability(
                modes={"text_to_video": _mode()}
            ),
        }
    ]
    created = client.post(
        "/api/v1/platform-admin/models",
        headers=admin_headers,
        json={
            "slug": "billing-mode-lifecycle",
            "display_name": "Billing Mode Lifecycle",
            "provider_key": "billing-provider",
            "billing_mode": "per_second",
            "capabilities": [],
        },
    )
    assert created.status_code == 201, created.text
    model = created.json()

    changed = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json={
            "display_name": model["display_name"],
            "provider_key": model["provider_key"],
            "billing_mode": "per_item",
            "expected_capability_version": 1,
            "capabilities": capabilities,
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["billing_mode"] == "per_item"
    assert changed.json()["capability_version"] == 2

    published = client.post(
        f"/api/v1/platform-admin/models/{model['id']}/publish",
        headers=admin_headers,
    )
    assert published.status_code == 200, published.text
    grant = client.put(
        f"/api/v1/platform-admin/companies/{tenant['company_id']}/model-grants",
        headers=admin_headers,
        json={
            "model_id": model["id"],
            "enabled": True,
            "price_per_item_cents": 99,
        },
    )
    assert grant.status_code == 200, grant.text
    assert client.post(
        f"/api/v1/platform-admin/models/{model['id']}/disable",
        headers=admin_headers,
    ).status_code == 200

    renamed = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json={
            "display_name": "Billing Mode Lifecycle Renamed",
            "provider_key": model["provider_key"],
            "billing_mode": "per_item",
            "expected_capability_version": 2,
            "capabilities": capabilities,
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["capability_version"] == 3

    frozen = client.put(
        f"/api/v1/platform-admin/models/{model['id']}",
        headers=admin_headers,
        json={
            "display_name": "Billing Mode Lifecycle Renamed",
            "provider_key": model["provider_key"],
            "billing_mode": "per_second",
            "expected_capability_version": 3,
            "capabilities": capabilities,
        },
    )
    assert frozen.status_code == 409
