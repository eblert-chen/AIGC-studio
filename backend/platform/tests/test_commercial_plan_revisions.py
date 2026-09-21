from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from platform_api.models import ModelCommercialReleasePlan
from platform_api.relay_client import (
    RelayModelCatalog,
    RelayModelReleaseEvidence,
    relay_sha256_revision,
)

from .test_model_commercial_release import (
    _admin_headers, _commercial_body, _prepare_video_draft,
)


def _rotate_and_accept(app) -> None:
    catalog_data = app.state.relay_client.catalog.model_dump(mode="json")
    for raw_item, item in zip(
        catalog_data["data"],
        app.state.relay_client.catalog.data,
        strict=True,
    ):
        raw_item["capabilities"] = item.capabilities.contract_dump()
    catalog_item = catalog_data["data"][0]
    catalog_item["published_route_revision"] = "sha256:" + "6" * 64
    catalog_data["published_route_revision"] = "sha256:" + "7" * 64
    catalog_data["catalog_revision"] = relay_sha256_revision(
        {
            "models": [
                {
                    "id": row["id"],
                    "capability_revision": row["capability_revision"],
                    "lifecycle": row["lifecycle"],
                    "managed_route": row["managed_route"],
                    "customer_callable": row["customer_callable"],
                    "published_route_revision": row[
                        "published_route_revision"
                    ],
                }
                for row in catalog_data["data"]
            ],
            "published_route_revision": catalog_data[
                "published_route_revision"
            ],
        }
    )
    app.state.relay_client.catalog = RelayModelCatalog.model_validate(
        catalog_data
    )
    data = app.state.relay_client.evidence.model_dump()
    data["catalog_revision"] = catalog_data["catalog_revision"]
    data["published_route_revision"] = catalog_data[
        "published_route_revision"
    ]
    item = data["models"][0]
    item.update(status="ready", accepted_route_count=1, fresh_test_count=1,
                latest_successful_test_at=data["generated_at"],
                model_release_revision="release-revision-2",
                published_route_revision="sha256:" + "6" * 64)
    route = item["routes"][0]
    route.update(accepted=True, fresh=True, fresh_test_modes=route["required_test_modes"],
                 latest_successful_test_at=data["generated_at"],
                 fresh_until=data["generated_at"] + timedelta(hours=1),
                 provider_credential_set_version="22222222-2222-4222-8222-222222222222",
                 provider_key_fingerprint_prefix="b" * 12,
                 route_binding_sha256="sha256:" + "3" * 64)
    item["routing_release_sha256"] = "sha256:" + "4" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(data)


def _approve(client, headers, model, body):
    return client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers, json=body,
    )


def test_route_rotation_can_be_explicitly_reapproved_without_rewriting_history(app, client):
    headers = _admin_headers(client, "revision-roundtrip")
    model = _prepare_video_draft(app, client, headers, evidence_status="blocked")
    original_body = _commercial_body(model=model)
    first = _approve(client, headers, model, original_body)
    assert first.status_code == 200, first.text
    first_data = first.json()
    _rotate_and_accept(app)
    blocked = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["items"][0]["last_blocker_code"] == (
        "published_route_revision_drift"
    )

    body = deepcopy(original_body)
    current = client.get(
        f"/api/v1/platform-admin/models/{model['id']}", headers=headers
    ).json()
    body["expected_capability_version"] = current["capability_version"]
    body["expected_candidate_revision"] = current[
        "relay_capability_candidate_revision"
    ]
    body["expected_catalog_revision"] = current[
        "relay_capability_candidate_catalog_revision"
    ]
    body["expected_routing_release_sha256"] = "sha256:" + "4" * 64
    body["idempotency_key"] += ":revision-2"
    implicit = _approve(client, headers, model, body)
    assert implicit.status_code == 409
    body["supersedes_plan_id"] = first_data["id"]
    replacement = _approve(client, headers, model, body)
    assert replacement.status_code == 200, replacement.text
    replacement_data = replacement.json()
    assert replacement_data["revision"] == 2
    assert replacement_data["supersedes_plan_id"] == first_data["id"]
    assert replacement_data["approved_route_identity_sha256"] != first_data["approved_route_identity_sha256"]
    assert replacement_data["approved_by_user_id"] == first_data["approved_by_user_id"]

    released = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert released.status_code == 200, released.text
    assert released.json()["released_count"] == 1
    by_id = {item["id"]: item for item in released.json()["items"]}
    assert by_id[first_data["id"]]["state"] == "superseded"
    assert by_id[first_data["id"]]["content_sha256"] == first_data["content_sha256"]
    assert by_id[first_data["id"]]["approved_route_identity"] == first_data["approved_route_identity"]
    assert by_id[replacement_data["id"]]["state"] == "released"
    assert by_id[replacement_data["id"]]["publication_receipt"]["plan_id"] == replacement_data["id"]
    # Exact retries identify original receipts even after route drift/release.
    old_retry = _approve(client, headers, model, original_body)
    new_retry = _approve(client, headers, model, body)
    assert old_retry.status_code == new_retry.status_code == 200
    assert old_retry.json()["id"] == first_data["id"]
    assert new_retry.json()["id"] == replacement_data["id"]
    assert old_retry.json()["state"] == "superseded"
    assert new_retry.json()["state"] == "released"
    body["approval_reason"] = "Changed request must not replay"
    assert _approve(client, headers, model, body).status_code == 409
    body["idempotency_key"] += ":after-release"
    body["supersedes_plan_id"] = replacement_data["id"]
    successor = _approve(client, headers, model, body)
    assert successor.status_code == 200, successor.text
    assert successor.json()["revision"] == 3
    assert successor.json()["state"] == "approved"
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(ModelCommercialReleasePlan)))) == 3


def test_stale_predecessor_cannot_create_a_second_successor(app, client):
    headers = _admin_headers(client, "revision-cas")
    model = _prepare_video_draft(app, client, headers, evidence_status="blocked")
    body = _commercial_body(model=model)
    first = _approve(client, headers, model, body).json()
    body["supersedes_plan_id"] = "00000000-0000-4000-8000-000000000000"
    body["idempotency_key"] += ":wrong"
    assert _approve(client, headers, model, body).status_code == 409
    body["supersedes_plan_id"] = first["id"]
    body["idempotency_key"] += ":winner"
    winner = _approve(client, headers, model, body)
    assert winner.status_code == 200, winner.text
    body["idempotency_key"] += ":loser"
    assert _approve(client, headers, model, body).status_code == 409
    with app.state.session_factory() as session:
        assert len(list(session.scalars(select(ModelCommercialReleasePlan)))) == 2


@pytest.mark.parametrize("operation", ["approve", "reconcile"])
def test_commercial_http_lock_order_is_company_model_execution(app, client, operation):
    headers = _admin_headers(client, "lock-order-" + operation)
    model = _prepare_video_draft(app, client, headers, evidence_status="blocked")
    body = _commercial_body(model=model)
    if operation == "reconcile":
        assert _approve(client, headers, model, body).status_code == 200
    locked_sql = []

    def capture(orm_state):
        statement = orm_state.statement
        if getattr(statement, "_for_update_arg", None) is not None:
            locked_sql.append(str(statement.compile(dialect=postgresql.dialect())))

    event.listen(Session, "do_orm_execute", capture)
    try:
        response = (
            _approve(client, headers, model, body) if operation == "approve" else
            client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
        )
        assert response.status_code == 200, response.text
    finally:
        event.remove(Session, "do_orm_execute", capture)
    company = next(i for i, sql in enumerate(locked_sql) if "FROM companies" in sql)
    model_lock = next(i for i, sql in enumerate(locked_sql) if "FROM model_definitions" in sql)
    assert company < model_lock, locked_sql
    if operation == "reconcile":
        execution = next(i for i, sql in enumerate(locked_sql) if "FOR UPDATE OF model_commercial_release_executions" in sql)
        assert model_lock < execution
    assert all("JOIN model_definitions" not in sql or "FOR UPDATE OF model_commercial_release_executions" in sql for sql in locked_sql)
