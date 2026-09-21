from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import event, func, select

from platform_api.models import (
    AuditLog,
    Company,
    CompanyModelGrant,
    CompanyPointPriceVersion,
    ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan,
    ModelDefinition,
    PersonalRetailModelGrant,
)
from platform_api.relay_catalog_sync_worker import RelayCatalogSyncWorker
from platform_api.services.provider_route_identity import (
    canonical_sha256,
    commercial_route_release_snapshot,
)

from .test_model_capability_v1_contract import _admin_headers
from .test_model_commercial_release import (
    _commercial_body,
    _prepare_video_draft,
)


INTERNAL_HEADERS = {"X-Internal-Service-Token": "test-internal-token"}


def _current_status_body(app, *, request_key: str = "account-a:model-a") -> dict:
    evidence_item = app.state.relay_client.evidence.models[0]
    route_identity, route_identity_sha256 = commercial_route_release_snapshot(
        evidence_item
    )
    route = route_identity["routes"][0]
    return {
        "schema_version": 1,
        "items": [
            {
                "request_key": request_key,
                "provider_name": route["provider_name"],
                "provider_account_id": route["provider_account_id"],
                "provider_channel_id": route["channel_id"],
                "route_identity": route_identity,
                "route_identity_sha256": route_identity_sha256,
            }
        ],
    }


def _commercial_row_counts(app) -> dict[str, int]:
    models = (
        ModelDefinition,
        ModelCommercialReleasePlan,
        ModelCommercialReleaseExecution,
        PersonalRetailModelGrant,
        CompanyModelGrant,
        CompanyPointPriceVersion,
        AuditLog,
    )
    with app.state.session_factory() as session:
        return {
            model.__tablename__: session.scalar(select(func.count(model.id)))
            or 0
            for model in models
        }


def _expected_request_identity(body: dict) -> str:
    items = sorted(body["items"], key=lambda item: item["request_key"])
    return canonical_sha256(
        {
            "schema_version": 1,
            "items": [
                {
                    "provider_account_id": item["provider_account_id"],
                    "provider_channel_id": item["provider_channel_id"],
                    "provider_name": item["provider_name"],
                    "request_key": item["request_key"],
                    "route_identity_sha256": item["route_identity_sha256"],
                }
                for item in items
            ],
        }
    )


def _release_current_model(
    app,
    client,
    *,
    suffix: str,
    company_ids: tuple[str, ...] = (),
) -> tuple[dict, dict]:
    headers = _admin_headers(client, suffix)
    model = _prepare_video_draft(
        app,
        client,
        headers,
        provider_cost_rate_set=True,
    )
    with app.state.session_factory.begin() as session:
        for index, company_id in enumerate(company_ids, start=1):
            session.add(
                Company(
                    id=company_id,
                    name=f"Provider status company {index}",
                    billing_version=2,
                )
            )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text
    released = RelayCatalogSyncWorker(
        app.state.session_factory,
        app.state.relay_client,
    ).run_once()
    assert released.commercial_release is not None
    assert released.commercial_release.released_count == 1
    return model, approved.json()


def test_projection_requires_internal_service_and_valid_exact_digest(
    app, client
) -> None:
    headers = _admin_headers(client, "provider-status-auth")
    _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    body = _current_status_body(app)

    missing_auth = client.post(
        "/internal/relay/provider-onboarding-status",
        json=body,
    )
    assert missing_auth.status_code == 401

    invalid_digest = deepcopy(body)
    invalid_digest["items"][0]["route_identity_sha256"] = "0" * 64
    rejected = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=invalid_digest,
    )
    assert rejected.status_code == 422

    wrong_account = deepcopy(body)
    wrong_account["items"][0]["provider_account_id"] = "other-account"
    rejected_account = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=wrong_account,
    )
    assert rejected_account.status_code == 422


def test_projection_reports_pending_without_mutating_platform_state(
    app, client
) -> None:
    headers = _admin_headers(client, "provider-status-pending")
    _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    body = _current_status_body(app)
    before = _commercial_row_counts(app)
    relay_evidence_calls = app.state.relay_client.evidence_calls
    write_statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        operation = statement.lstrip().split(None, 1)[0].upper()
        if operation in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            write_statements.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", capture_statement)
    try:
        response = client.post(
            "/internal/relay/provider-onboarding-status",
            headers=INTERNAL_HEADERS,
            json=body,
        )
    finally:
        event.remove(
            app.state.engine,
            "before_cursor_execute",
            capture_statement,
        )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["observed_at"].endswith(("Z", "+00:00"))
    expected_request_identity = _expected_request_identity(body)
    assert payload["request_identity_sha256"] == expected_request_identity
    item = payload["items"][0]
    assert item["publication_status"] == "commercial_approval_pending"
    assert item["personal"] == {
        "price_status": "not_configured",
        "grant_status": "not_granted",
        "price_points": None,
        "grant_id": None,
    }
    assert item["company"]["price_status"] == "not_applicable"
    assert item["company"]["grant_status"] == "not_applicable"
    assert app.state.relay_client.evidence_calls == relay_evidence_calls
    assert write_statements == []
    assert _commercial_row_counts(app) == before


def test_projection_matches_released_route_and_point_authority_exactly(
    app, client
) -> None:
    headers = _admin_headers(client, "provider-status-released")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        provider_cost_rate_set=True,
    )
    with app.state.session_factory.begin() as session:
        session.add(
            Company(
                id="00000000-0000-4000-8000-000000000571",
                name="Provider status company",
                billing_version=2,
            )
        )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text
    released = RelayCatalogSyncWorker(
        app.state.session_factory,
        app.state.relay_client,
    ).run_once()
    assert released.commercial_release is not None
    assert released.commercial_release.released_count == 1

    body = _current_status_body(app)
    before = _commercial_row_counts(app)
    response = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["publication_status"] == "released"
    assert item["blocker_code"] is None
    assert item["plan_id"] == approved.json()["id"]
    assert item["execution_id"]
    assert item["publication_receipt_sha256"]
    assert item["personal"]["price_status"] == "active"
    assert item["personal"]["grant_status"] == "active"
    assert item["personal"]["price_points"] == 12
    assert item["company"] == {
        "price_status": "active",
        "grant_status": "active",
        "grant_count": 1,
        "enabled_grant_count": 1,
        "active_grant_count": 1,
        "active_price_count": 1,
    }
    assert "route_identity" not in item
    assert "provider_key_fingerprint_prefix" not in response.text
    assert "provider_credential_set_version" not in response.text
    assert _commercial_row_counts(app) == before

    drifted_body = deepcopy(body)
    drifted_body["items"][0]["route_identity"]["routes"][0][
        "provider_credential_set_version"
    ] = "22222222-2222-4222-8222-222222222222"
    drifted_body["items"][0]["route_identity_sha256"] = canonical_sha256(
        drifted_body["items"][0]["route_identity"]
    )
    drifted = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=drifted_body,
    )
    assert drifted.status_code == 200, drifted.text
    drifted_item = drifted.json()["items"][0]
    assert drifted_item["publication_status"] == "drifted"
    assert drifted_item["blocker_code"] == "provider_route_identity_drift"
    assert drifted_item["personal"]["price_status"] == "drifted"
    assert drifted_item["personal"]["grant_status"] == "drifted"
    assert drifted_item["company"]["price_status"] == "drifted"
    assert drifted_item["company"]["grant_status"] == "drifted"


def test_projection_never_matches_a_partial_or_extra_multi_account_identity(
    app, client
) -> None:
    headers = _admin_headers(client, "provider-status-multi-account")
    model = _prepare_video_draft(
        app,
        client,
        headers,
        provider_cost_rate_set=True,
    )
    approved = client.put(
        f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan",
        headers=headers,
        json=_commercial_body(model=model),
    )
    assert approved.status_code == 200, approved.text
    released = RelayCatalogSyncWorker(
        app.state.session_factory,
        app.state.relay_client,
    ).run_once()
    assert released.commercial_release is not None
    assert released.commercial_release.released_count == 1

    body = _current_status_body(app)
    extra_route = deepcopy(body["items"][0]["route_identity"]["routes"][0])
    extra_route.update(
        route_id="route-second-account",
        channel_id=18,
        provider_account_id="test-account-b",
        provider_key_fingerprint_prefix="b" * 12,
        provider_credential_set_version=(
            "22222222-2222-4222-8222-222222222222"
        ),
        route_binding_sha256="sha256:" + "9" * 64,
    )
    body["items"][0]["route_identity"]["routes"].append(extra_route)
    body["items"][0]["route_identity"]["routes"].sort(
        key=lambda route: (route["route_id"], route["channel_id"])
    )
    body["items"][0]["route_identity_sha256"] = canonical_sha256(
        body["items"][0]["route_identity"]
    )
    response = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["publication_status"] == "drifted"
    assert item["blocker_code"] == "provider_route_identity_drift"


@pytest.mark.parametrize(
    "receipt_failure",
    [
        "wrong_hash",
        "stale_identity",
        "stale_receipt_metadata",
        "stale_release_evidence",
    ],
)
def test_projection_fails_closed_for_invalid_or_stale_publication_receipt(
    app,
    client,
    receipt_failure,
) -> None:
    _release_current_model(
        app,
        client,
        suffix=f"provider-status-receipt-{receipt_failure}",
    )
    body = _current_status_body(app)
    with app.state.session_factory.begin() as session:
        execution = session.scalar(select(ModelCommercialReleaseExecution))
        assert execution is not None
        receipt = deepcopy(execution.publication_receipt)
        assert receipt is not None
        if receipt_failure == "wrong_hash":
            receipt["plan_id"] = "wrong-plan-id"
        elif receipt_failure == "stale_identity":
            stale_identity = receipt["relay_route_release"]["route_identity"]
            stale_identity["published_route_revision"] = "sha256:" + "9" * 64
            receipt["relay_route_release"][
                "route_identity_sha256"
            ] = canonical_sha256(stale_identity)
        elif receipt_failure == "stale_receipt_metadata":
            receipt["route_identity_sha256"] = "8" * 64
        else:
            stale_evidence = deepcopy(execution.route_release_evidence)
            assert stale_evidence is not None
            stale_evidence["routes"][0]["upstream_model"] = "stale-upstream"
            execution.route_release_evidence = stale_evidence
        execution.publication_receipt = receipt
        if receipt_failure in {"stale_identity", "stale_receipt_metadata"}:
            execution.publication_receipt_sha256 = canonical_sha256(receipt)

    response = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["publication_status"] == "unavailable"
    assert item["blocker_code"] in {
        "publication_receipt_invalid",
        "publication_receipt_identity_invalid",
        "publication_release_evidence_invalid",
    }
    assert item["publication_receipt_sha256"] is None
    assert item["personal"]["price_status"] == "unavailable"
    assert item["personal"]["grant_status"] == "unavailable"
    assert item["company"]["price_status"] == "unavailable"
    assert item["company"]["grant_status"] == "unavailable"


def test_projection_reports_disabled_and_partial_point_authority(
    app,
    client,
) -> None:
    model, _ = _release_current_model(
        app,
        client,
        suffix="provider-status-partial",
        company_ids=(
            "00000000-0000-4000-8000-000000000581",
            "00000000-0000-4000-8000-000000000582",
        ),
    )
    body = _current_status_body(app)
    with app.state.session_factory.begin() as session:
        stored_model = session.get(ModelDefinition, model["id"])
        assert stored_model is not None
        stored_model.active = False
        personal = session.scalar(
            select(PersonalRetailModelGrant).where(
                PersonalRetailModelGrant.model_id == model["id"]
            )
        )
        assert personal is not None
        personal.enabled = False
        grants = session.scalars(
            select(CompanyModelGrant)
            .where(CompanyModelGrant.model_id == model["id"])
            .order_by(CompanyModelGrant.company_id)
        ).all()
        assert len(grants) == 2
        grants[0].enabled = False
        grants[1].point_price_active_version_id = None

    response = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["publication_status"] == "disabled"
    assert item["blocker_code"] == "platform_model_disabled"
    assert item["personal"]["price_status"] == "active"
    assert item["personal"]["grant_status"] == "disabled"
    assert item["company"] == {
        "price_status": "partial",
        "grant_status": "partial",
        "grant_count": 2,
        "enabled_grant_count": 1,
        "active_grant_count": 1,
        "active_price_count": 1,
    }


def test_batch_digest_is_order_stable_and_response_omits_route_identity(
    app,
    client,
) -> None:
    headers = _admin_headers(client, "provider-status-batch")
    _prepare_video_draft(app, client, headers, provider_cost_rate_set=True)
    body = _current_status_body(app, request_key="z-account:model")
    second = deepcopy(body["items"][0])
    second["request_key"] = "a-account:model"
    body["items"].append(second)

    response = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["request_identity_sha256"] == _expected_request_identity(body)
    assert [item["request_key"] for item in payload["items"]] == [
        "z-account:model",
        "a-account:model",
    ]

    reversed_body = {"schema_version": 1, "items": list(reversed(body["items"]))}
    reversed_response = client.post(
        "/internal/relay/provider-onboarding-status",
        headers=INTERNAL_HEADERS,
        json=reversed_body,
    )
    assert reversed_response.status_code == 200, reversed_response.text
    assert (
        reversed_response.json()["request_identity_sha256"]
        == payload["request_identity_sha256"]
    )

    route = body["items"][0]["route_identity"]["routes"][0]
    for sensitive in (
        body["items"][0]["provider_name"],
        body["items"][0]["provider_account_id"],
        route["route_id"],
        route["provider_key_fingerprint_prefix"],
        route["provider_credential_set_version"],
        route["route_binding_sha256"],
        route["upstream_model"],
        route["adapter_profile_id"],
        route["adapter_profile_revision"],
    ):
        assert sensitive not in response.text
    assert set(payload["items"][0]) == {
        "request_key",
        "public_model_id",
        "route_identity_sha256",
        "publication_status",
        "blocker_code",
        "plan_id",
        "execution_id",
        "publication_receipt_sha256",
        "personal",
        "company",
    }
