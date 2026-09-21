from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from platform_api.models import User
from platform_api.relay_client import (
    HttpxRelayOperationsClient,
    RelayPermanentError,
    RelayProviderResultReconciliation,
    RelayProviderResultReconciliationPage,
)

from .test_platform_admin import bootstrap_admin


TENANT_ID = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
JOB_ID = "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"
OPERATIONS_TOKEN = "operations-secret-32-bytes-long-value"
APPROVAL_KEY_ID = "platform-approval-v1"
APPROVAL_SECRET = "platform-approval-secret-32-bytes-long-value"


def provider_result_payload(
    *,
    kind: str = "provider_result_proof",
    status: str = "reconciliation_required",
) -> dict:
    now = "2026-08-28T02:03:04+00:00"
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "generation.provider_result_reconciliation",
        "job_id": JOB_ID,
        "tenant_id": TENANT_ID,
        "client_reference_id": "platform-task-1",
        "model": "seedance-1.0-pro",
        "mode": "text_to_video",
        "status": status,
        "progress": 0 if status == "reconciliation_required" else 100,
        "provider_route_id": 19,
        "provider_channel_id": 7,
        "provider_submission_attempt": 2,
        "upstream_task_id": "provider-task-one",
        "reconciliation_kind": kind,
        "resolution_supported": False,
        "evidence_retained": True,
        "error_code": (
            "PROVIDER_POLL_RECONCILIATION_REQUIRED"
            if status == "reconciliation_required"
            else ""
        ),
        "error_message": "Provider terminal proof requires manual reconciliation",
        "created_at": now,
        "updated_at": now,
    }


def provider_result_page(payload: dict | None = None) -> dict:
    return {
        "api_version": "v1",
        "schema_version": 1,
        "object": "list",
        "data": [payload or provider_result_payload()],
        "page": 1,
        "page_size": 25,
        "total": 1,
    }


def new_http_client(handler) -> HttpxRelayOperationsClient:
    return HttpxRelayOperationsClient(
        base_url="https://relay.example.test",
        tenant_id=TENANT_ID,
        operations_token=OPERATIONS_TOKEN,
        approval_key_id=APPROVAL_KEY_ID,
        approval_secret=APPROVAL_SECRET,
        transport=httpx.MockTransport(handler),
    )


def test_operations_client_reads_secret_free_provider_result_queue() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["x-relay-operations-token"] == OPERATIONS_TOKEN
        assert request.url.params["tenant_id"] == TENANT_ID
        if request.url.path.endswith("/provider-result-reconciliation") and (
            request.url.path
            == "/internal/platform-generation-operations/provider-result-reconciliation"
        ):
            assert request.url.params["page"] == "1"
            assert request.url.params["page_size"] == "25"
            return httpx.Response(200, json=provider_result_page())
        assert request.url.path.endswith(
            f"/{JOB_ID}/provider-result-reconciliation"
        )
        return httpx.Response(200, json=provider_result_payload())

    client = new_http_client(handler)
    page = client.list_provider_result_reconciliations(
        page=1,
        page_size=25,
        request_id="provider-result-list-request",
    )
    detail = client.get_provider_result_reconciliation(
        JOB_ID,
        request_id="provider-result-detail-request",
    )

    assert page.total == 1
    assert detail.job_id == page.data[0].job_id == UUID(JOB_ID)
    assert detail.reconciliation_kind == "provider_result_proof"
    assert detail.resolution_supported is False
    assert [request.headers["x-request-id"] for request in seen] == [
        "provider-result-list-request",
        "provider-result-detail-request",
    ]
    serialized = json.dumps(page.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "https://provider.example",
        "result_url",
        "temporary_result_json",
        "private_data",
        "fail_reason",
        "credential",
    ):
        assert forbidden not in serialized.lower()


@pytest.mark.parametrize("target", ("list", "detail"))
@pytest.mark.parametrize(
    ("forbidden_field", "forbidden_value"),
    (
        ("result_url", "https://provider.example/result.mp4?token=secret"),
        ("data", {"content": {"video_url": "https://provider.example/private"}}),
        ("fail_reason", "provider raw failure"),
        ("credential_version", "vault-version-secret"),
    ),
)
def test_operations_client_rejects_extra_provider_material_fields(
    target: str,
    forbidden_field: str,
    forbidden_value: object,
) -> None:
    item = provider_result_payload()
    item[forbidden_field] = forbidden_value

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=provider_result_page(item) if target == "list" else item,
        )

    client = new_http_client(handler)
    with pytest.raises(RelayPermanentError, match="response is invalid"):
        if target == "list":
            client.list_provider_result_reconciliations(page=1, page_size=25)
        else:
            client.get_provider_result_reconciliation(JOB_ID)


@pytest.mark.parametrize(
    "mutation",
    (
        {"tenant_id": "6b4f72d2-4d64-4ef9-adf2-7ead3e125b4f"},
        {"job_id": "6b4f72d2-4d64-4ef9-adf2-7ead3e125b4f"},
        {"updated_at": "2026-08-27T02:03:04+00:00"},
        {"created_at": "2026-08-28T02:03:04"},
        {"created_at": "2026-08-28T10:03:04+08:00"},
        {"resolution_supported": True},
        {"evidence_retained": 1},
        {"error_message": "https://provider.example/result?token=secret"},
        {"error_message": "Provider terminal proof\nrequires manual reconciliation"},
        {"upstream_task_id": "https://provider.example/task-one"},
        {
            "status": "succeeded",
            "progress": 100,
            "error_code": "",
        },
    ),
)
def test_operations_client_rejects_provider_result_identity_or_state_drift(
    mutation: dict,
) -> None:
    item = provider_result_payload()
    item.update(mutation)
    client = new_http_client(lambda _: httpx.Response(200, json=item))

    with pytest.raises(RelayPermanentError, match="response is invalid"):
        client.get_provider_result_reconciliation(JOB_ID)


@pytest.mark.parametrize(
    ("status", "error_code"),
    (
        ("reconciliation_required", "PROVIDER_POLL_RECONCILIATION_REQUIRED"),
        ("succeeded", ""),
    ),
)
def test_operations_client_accepts_both_provider_material_states(
    status: str,
    error_code: str,
) -> None:
    item = provider_result_payload(kind="provider_material", status=status)
    item.update(
        {
            "error_code": error_code,
            "error_message": "Retained provider material requires manual reconciliation",
        }
    )
    client = new_http_client(lambda _: httpx.Response(200, json=item))

    result = client.get_provider_result_reconciliation(JOB_ID)

    assert result.status == status
    assert result.reconciliation_kind == "provider_material"


def test_operations_client_preserves_url_shaped_customer_reference() -> None:
    item = provider_result_payload()
    item["client_reference_id"] = "https://customer.example/tasks/business-42"
    client = new_http_client(lambda _: httpx.Response(200, json=item))

    result = client.get_provider_result_reconciliation(JOB_ID)

    assert result.client_reference_id == item["client_reference_id"]


def test_operations_client_rejects_duplicate_jobs_in_provider_result_page() -> None:
    item = provider_result_payload()
    page = provider_result_page(item)
    page.update({"data": [item, item], "page_size": 25, "total": 2})
    client = new_http_client(lambda _: httpx.Response(200, json=page))

    with pytest.raises(RelayPermanentError, match="response is invalid"):
        client.list_provider_result_reconciliations(page=1, page_size=25)


class FakeProviderResultOperationsClient:
    def __init__(self) -> None:
        self.item = RelayProviderResultReconciliation.model_validate(
            provider_result_payload()
        )
        self.calls: list[tuple[str, object]] = []

    def list_provider_result_reconciliations(
        self, *, page=1, page_size=50, request_id=None
    ):
        self.calls.append(("list", request_id))
        return RelayProviderResultReconciliationPage(
            api_version="v1",
            schema_version=1,
            object="list",
            data=[self.item],
            page=page,
            page_size=page_size,
            total=1,
        )

    def get_provider_result_reconciliation(self, job_id, *, request_id=None):
        self.calls.append(("detail", request_id))
        if job_id != str(self.item.job_id):
            raise RelayPermanentError("not found", response_status=404)
        return self.item


def platform_admin_headers(app) -> dict[str, str]:
    with app.state.session_factory.begin() as session:
        admin = User(
            email="provider-result-admin@example.com",
            display_name="Provider Result Admin",
            is_platform_admin=True,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
    return {
        "X-Platform-Admin-User-ID": admin_id,
        "X-Request-ID": "provider-result-facade-request",
    }


def test_admin_provider_result_facade_is_read_only_no_store_and_secret_free(
    client,
    app,
) -> None:
    relay = FakeProviderResultOperationsClient()
    app.state.relay_operations_client = relay
    headers = platform_admin_headers(app)

    unauthenticated = client.get(
        "/api/v1/platform-admin/relay/provider-result-reconciliation"
    )
    assert unauthenticated.status_code in {401, 403}

    listed = client.get(
        "/api/v1/platform-admin/relay/provider-result-reconciliation?page=1&page_size=25",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "private, no-store"
    assert listed.json()["object"] == "list"
    assert listed.json()["data"][0]["resolution_supported"] is False

    detail = client.get(
        f"/api/v1/platform-admin/relay/provider-result-reconciliation/{JOB_ID}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.headers["cache-control"] == "private, no-store"
    assert detail.json()["object"] == (
        "generation.provider_result_reconciliation"
    )
    serialized = json.dumps(
        {"list": listed.json(), "detail": detail.json()}, sort_keys=True
    ).lower()
    for forbidden in (
        "result_url",
        "temporary_result_json",
        "private_data",
        "fail_reason",
        "credential",
        "token=",
    ):
        assert forbidden not in serialized
    assert relay.calls == [
        ("list", "provider-result-facade-request"),
        ("detail", "provider-result-facade-request"),
    ]

    invalid = client.get(
        "/api/v1/platform-admin/relay/provider-result-reconciliation/not-a-uuid",
        headers=headers,
    )
    assert invalid.status_code == 422
    unsupported = client.post(
        f"/api/v1/platform-admin/relay/provider-result-reconciliation/{JOB_ID}/resolve",
        headers=headers,
        json={},
    )
    assert unsupported.status_code == 404


def test_provider_result_facade_enforces_delegated_relay_read_permission(
    client,
    app,
) -> None:
    relay = FakeProviderResultOperationsClient()
    app.state.relay_operations_client = relay
    _, owner_headers = bootstrap_admin(client, "provider-result-owner")
    reader_id, reader_headers = bootstrap_admin(client, "provider-result-reader")
    manager_id, manager_headers = bootstrap_admin(client, "provider-result-manager")

    reader_role = client.post(
        "/api/v1/platform-admin/access/roles",
        headers=owner_headers,
        json={
            "key": "provider-result-reader",
            "display_name": "Provider result reader",
            "description": "Reads the secret-free provider reconciliation queue",
            "permission_codes": [
                "platform.admin_access.read",
                "platform.relay_health.read",
            ],
            "change_reason": "Delegate read-only Relay incident visibility",
        },
    )
    assert reader_role.status_code == 201, reader_role.text
    assigned_reader = client.put(
        f"/api/v1/platform-admin/access/users/{reader_id}",
        headers=owner_headers,
        json={
            "role_ids": [reader_role.json()["id"]],
            "permission_overrides": {},
            "expected_lock_version": 0,
            "change_reason": "Grant provider result queue read access",
        },
    )
    assert assigned_reader.status_code == 200, assigned_reader.text

    manager_role = client.post(
        "/api/v1/platform-admin/access/roles",
        headers=owner_headers,
        json={
            "key": "provider-result-manager-only",
            "display_name": "Provider result manager only",
            "description": "Confirms manage permission does not imply read permission",
            "permission_codes": [
                "platform.admin_access.read",
                "platform.relay_health.manage",
            ],
            "change_reason": "Verify explicit read and manage separation",
        },
    )
    assert manager_role.status_code == 201, manager_role.text
    assigned_manager = client.put(
        f"/api/v1/platform-admin/access/users/{manager_id}",
        headers=owner_headers,
        json={
            "role_ids": [manager_role.json()["id"]],
            "permission_overrides": {},
            "expected_lock_version": 0,
            "change_reason": "Verify manage-only access remains unable to read",
        },
    )
    assert assigned_manager.status_code == 200, assigned_manager.text

    path = "/api/v1/platform-admin/relay/provider-result-reconciliation"
    reader_list = client.get(path, headers=reader_headers)
    reader_detail = client.get(f"{path}/{JOB_ID}", headers=reader_headers)
    assert reader_list.status_code == 200, reader_list.text
    assert reader_detail.status_code == 200, reader_detail.text

    manager_list = client.get(path, headers=manager_headers)
    assert manager_list.status_code == 403
    assert "platform.relay_health.read" in manager_list.json()["detail"]


@pytest.mark.parametrize("target", ("list", "detail"))
def test_admin_provider_result_facade_fails_closed_on_relay_extra_fields(
    client,
    app,
    target: str,
) -> None:
    headers = platform_admin_headers(app)
    item = provider_result_payload()
    item["result_url"] = "https://provider.example/result.mp4?token=secret"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(
            "/internal/platform-generation-operations/provider-result-reconciliation"
        ):
            return httpx.Response(200, json=provider_result_page(item))
        return httpx.Response(200, json=item)

    app.state.relay_operations_client = new_http_client(handler)
    path = "/api/v1/platform-admin/relay/provider-result-reconciliation"
    if target == "detail":
        path += f"/{JOB_ID}"
    response = client.get(path, headers=headers)
    assert response.status_code == 502
    assert "provider.example" not in response.text
    assert "token=secret" not in response.text
