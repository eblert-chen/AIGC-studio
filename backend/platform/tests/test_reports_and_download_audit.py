from __future__ import annotations

import csv
from io import StringIO

import httpx
import pytest
from sqlalchemy import func, select

from platform_api.models import (
    CompanyMembership,
    DownloadRecord,
    GenerationTask,
    ModelDefinition,
    User,
)
from platform_api.relay_client import HttpxRelayClient
from platform_api.services.billing import WalletService

from .conftest import bootstrap
from .test_artifact_bridge_and_production_safety import (
    ASSET_ID,
    RELAY_JOB_ID,
    make_task_downloadable,
)
from .test_relay_boundary import recharge_and_create


def _relay_client(handler) -> HttpxRelayClient:
    return HttpxRelayClient(
        base_url="http://relay.internal",
        client_id="platform-service",
        api_key="server-only-secret",
        transport=httpx.MockTransport(handler),
        allow_local_http=True,
    )


def test_successful_download_issuance_is_audited_and_company_scoped(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="download-audit"
    )
    make_task_downloadable(app, task["id"])

    def success_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "api_version": "v1",
                "schema_version": 1,
                "url": "http://127.0.0.1:8100/private/signed-token",
                "expires_seconds": 300,
            },
        )

    app.state.relay_client = _relay_client(success_handler)
    download_path = (
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{ASSET_ID}/download"
    )
    issued = client.get(
        download_path,
        headers={**tenant_headers, "X-Request-ID": "download-request-001"},
    )
    assert issued.status_code == 200, issued.text

    records = client.get(
        f"/api/v1/companies/{tenant['company_id']}/download-records",
        headers=tenant_headers,
        params={"task_id": task["id"], "asset_id": ASSET_ID},
    )
    assert records.status_code == 200, records.text
    body = records.json()
    assert body["total"] == 1
    assert body["items"][0]["task_id"] == task["id"]
    assert body["items"][0]["requested_by_user_id"] == tenant["user_id"]
    assert body["items"][0]["expires_seconds"] == 300
    assert body["items"][0]["request_id"] == "download-request-001"
    assert body["items"][0]["expires_at"] > body["items"][0]["created_at"]

    other = bootstrap(client, "download-audit-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    isolated = client.get(
        f"/api/v1/companies/{other['company_id']}/download-records",
        headers=other_headers,
    )
    assert isolated.status_code == 200
    assert isolated.json()["total"] == 0
    forged = client.get(
        f"/api/v1/companies/{tenant['company_id']}/download-records",
        headers=other_headers,
    )
    assert forged.status_code == 403

    with app.state.session_factory.begin() as session:
        member_user = User(
            email="no-reports@example.com", display_name="No Reports"
        )
        session.add(member_user)
        session.flush()
        session.add(
            CompanyMembership(
                company_id=tenant["company_id"], user_id=member_user.id
            )
        )
        member_user_id = member_user.id
    denied = client.get(
        f"/api/v1/companies/{tenant['company_id']}/download-records",
        headers={
            "X-Company-ID": tenant["company_id"],
            "X-User-ID": member_user_id,
        },
    )
    assert denied.status_code == 403

    with pytest.raises(RuntimeError, match="download records are immutable"):
        with app.state.session_factory.begin() as session:
            record = session.scalar(select(DownloadRecord))
            record.asset_id = "tampered"
    with pytest.raises(RuntimeError, match="download records are immutable"):
        with app.state.session_factory.begin() as session:
            record = session.scalar(select(DownloadRecord))
            session.delete(record)


def test_failed_download_issuance_does_not_create_a_record(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="download-audit-failed"
    )
    make_task_downloadable(app, task["id"])
    app.state.relay_client = _relay_client(
        lambda _: httpx.Response(503, json={"detail": "temporarily down"})
    )

    response = client.get(
        f"/api/v1/companies/{tenant['company_id']}/tasks/{task['id']}"
        f"/artifacts/{ASSET_ID}/download",
        headers=tenant_headers,
    )
    assert response.status_code == 503
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count(DownloadRecord.id))) == 0


def test_task_and_consumption_reports_filter_and_aggregate_with_tenant_isolation(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="report-success"
    )
    with app.state.session_factory.begin() as session:
        WalletService.settle_success(
            session,
            company_id=tenant["company_id"],
            task_id=task["id"],
            actual_cost_cents=325,
            idempotency_key="report-settle-success",
        )

    task_report_url = (
        f"/api/v1/companies/{tenant['company_id']}/reports/tasks"
    )
    task_report = client.get(
        task_report_url,
        headers=tenant_headers,
        params={
            "employee_user_id": tenant["user_id"],
            "model_id": task["model_id"],
            "status": "succeeded",
            "start_time": "2020-01-01T00:00:00Z",
            "end_time": "2100-01-01T00:00:00Z",
        },
    )
    assert task_report.status_code == 200, task_report.text
    task_body = task_report.json()
    assert task_body["total"] == 1
    assert task_body["total_actual_cost_cents"] == 325
    assert task_body["items"][0]["task_id"] == task["id"]
    assert task_body["items"][0]["request_payload"]["prompt"] == "可靠提交测试"

    wrong_status = client.get(
        task_report_url,
        headers=tenant_headers,
        params={"status": "failed"},
    )
    assert wrong_status.status_code == 200
    assert wrong_status.json()["total"] == 0
    future = client.get(
        task_report_url,
        headers=tenant_headers,
        params={"start_time": "2100-01-01T00:00:00Z"},
    )
    assert future.status_code == 200
    assert future.json()["total"] == 0

    consumption_url = (
        f"/api/v1/companies/{tenant['company_id']}/reports/consumption"
    )
    consumption = client.get(
        consumption_url,
        headers=tenant_headers,
        params={
            "employee_user_id": tenant["user_id"],
            "model_id": task["model_id"],
            "status": "succeeded",
        },
    )
    assert consumption.status_code == 200, consumption.text
    consumption_body = consumption.json()
    assert consumption_body["total"] == 1
    assert consumption_body["total_amount_cents"] == 325
    assert consumption_body["items"][0]["task_id"] == task["id"]

    other = bootstrap(client, "report-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    isolated = client.get(
        f"/api/v1/companies/{other['company_id']}/reports/consumption",
        headers=other_headers,
    )
    assert isolated.status_code == 200
    assert isolated.json()["total_amount_cents"] == 0
    assert (
        client.get(task_report_url, headers=other_headers).status_code == 403
    )


def test_report_time_range_requires_offsets_and_increasing_bounds(
    client, tenant, tenant_headers
):
    url = f"/api/v1/companies/{tenant['company_id']}/reports/tasks"
    assert (
        client.get(
            url,
            headers=tenant_headers,
            params={"start_time": "2026-08-01T00:00:00"},
        ).status_code
        == 422
    )
    reversed_range = client.get(
        url,
        headers=tenant_headers,
        params={
            "start_time": "2026-08-02T00:00:00Z",
            "end_time": "2026-08-01T00:00:00Z",
        },
    )
    assert reversed_range.status_code == 422


def test_csv_exports_are_utf8_safe_against_spreadsheet_formula_injection(
    app, client, tenant, tenant_headers
):
    task = recharge_and_create(
        app, client, tenant, tenant_headers, id_suffix="report-csv"
    )
    with app.state.session_factory.begin() as session:
        WalletService.settle_success(
            session,
            company_id=tenant["company_id"],
            task_id=task["id"],
            actual_cost_cents=300,
            idempotency_key="report-csv-settle",
        )
        session.get(User, tenant["user_id"]).display_name = "=HYPERLINK(\"bad\")"
        session.get(ModelDefinition, task["model_id"]).display_name = "+SUM(1,1)"

    for report_name in ("tasks", "consumption"):
        response = client.get(
            f"/api/v1/companies/{tenant['company_id']}/reports/"
            f"{report_name}/export.csv",
            headers=tenant_headers,
        )
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        parsed = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        flattened = [cell for row in parsed for cell in row]
        assert "'=HYPERLINK(\"bad\")" in flattened
        assert "'+SUM(1,1)" in flattened
        assert "=HYPERLINK(\"bad\")" not in flattened
        assert "+SUM(1,1)" not in flattened
