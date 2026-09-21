from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from platform_api.models import CompanyPointWalletAccount, GenerationTask, ModelCommercialReleaseExecution, PersonalWalletAccount, PointLotSourceKind, RelaySubmissionOutbox
from platform_api.relay_client import RelayModelReleaseEvidence
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.personal_billing import PersonalWalletService

from .test_commercial_plan_revisions import _approve, _rotate_and_accept
from .test_commercial_pricing_policy import _released
from .test_model_commercial_release import _commercial_body
from .test_personal_workspace import _personal_user


def _task_fixture(app, client, tenant, scope):
    with app.state.session_factory.begin() as session:
        CompanyPointBillingService.migrate(session, company_id=tenant["company_id"], expected_available_cents=0, idempotency_key="task-policy-migrate")
        CompanyPointBillingService.credit(session, company_id=tenant["company_id"], amount_points=1000,
                                         source_kind=PointLotSourceKind.PROMOTIONAL,
                                         cash_basis_cents=0, subsidy_cents=10000, idempotency_key="task-policy-funding")
    admin, model, _, _ = _released(app, client)
    if scope == "company":
        headers = {"X-User-Id": tenant["user_id"], "X-Company-Id": tenant["company_id"]}
        path = f"/api/v1/companies/{tenant['company_id']}/tasks"
        models_path = f"/api/v1/companies/{tenant['company_id']}/models"
        wallet_type, wallet_id = CompanyPointWalletAccount, tenant["company_id"]
    else:
        user_id = _personal_user(app, "commercial-task")
        headers = {"X-User-Id": user_id}
        workspace_id = client.get("/api/v1/personal/me", headers=headers).json()["workspace_id"]
        with app.state.session_factory.begin() as session:
            PersonalWalletService.credit(session, workspace_id=workspace_id, amount_points=1000,
                                         idempotency_key="task-policy-funding", note="local promotional fixture")
        path, models_path = "/api/v1/personal/tasks", "/api/v1/personal/models"
        wallet_type, wallet_id = PersonalWalletAccount, workspace_id
    listed = client.get(models_path, headers=headers)
    assert listed.status_code == 200, listed.text
    current = next(row for row in listed.json() if row["id"] == model["id"])
    body = {"model_id": model["id"], "idempotency_key": "commercial-task-001",
            "expected_capability_version": current["capability_version"],
            "expected_quote_revision": current["quote_revision"],
            "request_payload": {"mode": "text_to_video", "prompt": "Local price admission test",
                                "assets": [], "duration_seconds": 5, "aspect_ratio": "16:9",
                                "resolution": "2k", "output_count": 1, "face_enabled": False}}
    return admin, model, headers, path, body, wallet_type, wallet_id


def _counts(app, wallet_type, wallet_id):
    with app.state.session_factory() as session:
        wallet = session.get(wallet_type, wallet_id)
        return (wallet.available_points, wallet.reserved_points,
                session.scalar(select(func.count(GenerationTask.id))),
                session.scalar(select(func.count(RelaySubmissionOutbox.id))))


@pytest.mark.parametrize("scope", ["company", "personal"])
@pytest.mark.parametrize("failure", ["missing_evidence", "pending_plan", "route_drift"])
def test_new_tasks_fail_before_money_or_outbox_when_commercial_authority_is_missing(app, client, tenant, scope, failure):
    admin, model, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    before = _counts(app, wallet_type, wallet_id)
    if failure == "missing_evidence":
        app.state.relay_client = None
    elif failure == "route_drift":
        _rotate_and_accept(app)
    else:
        with app.state.session_factory() as session:
            first = session.scalar(select(ModelCommercialReleaseExecution))
            plan_id = first.plan_id
        successor = _commercial_body(model=model)
        successor.update(supersedes_plan_id=plan_id, idempotency_key=successor["idempotency_key"] + ":pending")
        assert _approve(client, admin, model, successor).status_code == 200
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == (503 if failure == "missing_evidence" else 409), response.text
    assert _counts(app, wallet_type, wallet_id) == before == (1000, 0, 0, 0)


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_catalog_evidence_snapshot_mismatch_fails_before_money_or_outbox(
    app, client, tenant, scope
):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(
        app, client, tenant, scope
    )
    before = _counts(app, wallet_type, wallet_id)
    evidence = app.state.relay_client.evidence.model_dump()
    evidence["catalog_revision"] = "sha256:" + "9" * 64
    app.state.relay_client.evidence = RelayModelReleaseEvidence.model_validate(
        evidence
    )

    response = client.post(path, headers=headers, json=body)

    assert response.status_code == 502, response.text
    assert _counts(app, wallet_type, wallet_id) == before == (1000, 0, 0, 0)


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_protected_task_admission_rejects_unmanaged_compatibility_routes(
    app, client, tenant, monkeypatch, scope
):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(
        app, client, tenant, scope
    )
    before = _counts(app, wallet_type, wallet_id)
    relay = app.state.relay_client
    relay.catalog = relay.catalog.model_copy(
        update={
            "data": [
                item.model_copy(update={"managed_route": False})
                for item in relay.catalog.data
            ]
        }
    )
    monkeypatch.setattr(
        "platform_api.main.runtime_settings_are_protected",
        lambda _: True,
    )

    response = client.post(path, headers=headers, json=body)

    assert response.status_code == 502, response.text
    assert _counts(app, wallet_type, wallet_id) == before == (1000, 0, 0, 0)


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_released_price_creates_once_and_old_task_replays_without_live_evidence(app, client, tenant, scope):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    created = client.post(path, headers=headers, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["quote_points"] == 60
    task_id = created.json()["id"]
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        pricing = deepcopy(task.pricing_snapshot)
        assert pricing["commercial_price_authority"]["plan_revision"] == 1
        assert "provider_cost" not in str(pricing)
        assert "provider_account" not in str(pricing)
        execution = session.scalar(
            select(ModelCommercialReleaseExecution).where(
                ModelCommercialReleaseExecution.state == "released"
            )
        )
        assert execution is not None
        release = execution.publication_receipt["relay_route_release"]
        assert release["model_release_id"] == (
            "release-minimax-h3-max-commercial"
        )
        assert release["model_release_revision"] == "release-revision-1"
        assert release["published_route_revision"] == (
            "sha256:" + "c" * 64
        )
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task_id
            )
        )
        assert outbox is not None
        execution_contract = outbox.relay_payload["execution_contract"]
        assert execution_contract["routing_release_sha256"] == (
            release["routing_release_sha256"]
        )
        assert pricing["execution_contract_sha256"]
    before = _counts(app, wallet_type, wallet_id)
    assert before == (940, 60, 1, 1)
    app.state.relay_client = None
    repeated = client.post(path, headers=headers, json=body)
    assert repeated.status_code == 201 and repeated.json()["id"] == task_id, repeated.text
    assert _counts(app, wallet_type, wallet_id) == before
    with app.state.session_factory() as session:
        assert session.get(GenerationTask, task_id).pricing_snapshot == pricing
    with app.state.session_factory.begin() as session:
        if scope == "company":
            CompanyPointBillingService.settle_success(
                session,
                company_id=wallet_id,
                task_id=task_id,
                actual_cost_points=60,
                idempotency_key=f"commercial-settle-{scope}",
            )
            CompanyPointBillingService.settle_success(
                session,
                company_id=wallet_id,
                task_id=task_id,
                actual_cost_points=60,
                idempotency_key=f"commercial-settle-{scope}",
            )
        else:
            PersonalWalletService.settle_success(
                session,
                workspace_id=wallet_id,
                task_id=task_id,
                actual_cost_points=60,
                idempotency_key=f"commercial-settle-{scope}",
            )
            PersonalWalletService.settle_success(
                session,
                workspace_id=wallet_id,
                task_id=task_id,
                actual_cost_points=60,
                idempotency_key=f"commercial-settle-{scope}",
            )
    assert _counts(app, wallet_type, wallet_id) == (940, 0, 1, 1)
    with app.state.session_factory() as session:
        settled = session.get(GenerationTask, task_id)
        assert settled.status.value == "succeeded"
        assert settled.actual_cost_points == 60
        assert settled.pricing_snapshot == pricing


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_quote_authority_cannot_change_between_live_evidence_and_task_lock(app, client, tenant, monkeypatch, scope):
    admin, model, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    with app.state.session_factory() as session:
        plan_id = session.scalar(select(ModelCommercialReleaseExecution)).plan_id
    successor = _commercial_body(model=model)
    successor.update(supersedes_plan_id=plan_id, idempotency_key=successor["idempotency_key"] + ":during-read")
    relay = app.state.relay_client
    reader = relay.get_model_release_evidence

    def supersede_during_read(*, request_id):
        evidence = reader(request_id=request_id)
        monkeypatch.setattr(relay, "get_model_release_evidence", reader)
        approved = _approve(client, admin, model, successor)
        assert approved.status_code == 200, approved.text
        return evidence

    monkeypatch.setattr(relay, "get_model_release_evidence", supersede_during_read)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert _counts(app, wallet_type, wallet_id) == (1000, 0, 0, 0)


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_successor_changes_only_new_task_quotes_and_retains_original_task_authority(app, client, tenant, scope):
    admin, model, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 201, first.text
    task_id = first.json()["id"]
    with app.state.session_factory() as session:
        pricing = deepcopy(session.get(GenerationTask, task_id).pricing_snapshot)
    successor = _commercial_body(model=model)
    successor.update(supersedes_plan_id=pricing["commercial_price_authority"]["plan_id"],
                     idempotency_key=successor["idempotency_key"] + ":new-price",
                     personal_price_points=20, enterprise_price_points=20)
    assert _approve(client, admin, model, successor).status_code == 200
    replay = client.post(path, headers=headers, json=body)
    assert replay.status_code == 201 and replay.json()["quote_points"] == 60
    released = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=admin)
    assert released.status_code == 200 and released.json()["released_count"] == 1, released.text
    # Refreshing the server quote is mandatory after the immutable version changed.
    new_body = dict(body, idempotency_key="commercial-task-002")
    assert client.post(path, headers=headers, json=new_body).status_code == 409
    models_path = path.rsplit("/", 1)[0] + "/models"
    quote = next(row for row in client.get(models_path, headers=headers).json() if row["id"] == model["id"])
    new_body["expected_quote_revision"] = quote["quote_revision"]
    second = client.post(path, headers=headers, json=new_body)
    assert second.status_code == 201 and second.json()["quote_points"] == 100, second.text
    assert _counts(app, wallet_type, wallet_id) == (840, 160, 2, 2)
    with app.state.session_factory() as session:
        assert session.get(GenerationTask, task_id).pricing_snapshot == pricing
        new_pricing = session.get(GenerationTask, second.json()["id"]).pricing_snapshot
        assert new_pricing["commercial_price_authority"]["plan_revision"] == 2
