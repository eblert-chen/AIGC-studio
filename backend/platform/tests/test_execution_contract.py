"""Software-only execution pin tests; no real provider or payment calls."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from platform_api.execution_contract import ExecutionContract
from platform_api.models import ChannelCostEntry, GenerationTask, RelaySubmissionOutbox, TaskStatus
from platform_api.relay_client import RelayAsyncErrorCode, RelayErrorEnvelope, RelayGenerationRequest
from platform_api.services.errors import ConflictError
from platform_api.services.execution_contracts import require_execution_cost, require_execution_digest
from platform_api.services.relay_outbox import RelayOutboxDispatcher
from platform_api.services.relay_status import RelayStatusService
from platform_api.services.provider_route_identity import bind_task_provider_route_identity

from .test_commercial_task_admission import _task_fixture, _counts
from .test_relay_boundary import ScriptedRelayClient, accepted_response
from .test_channel_cost_relay_contract import _bound_provider_identity, _post_signed, _relay_cost_payload, _raw


def wire():
    return {
        "schema_version": 1, "routing_release_sha256": "sha256:" + "a" * 64,
        "provider_cost_readiness_sha256": "sha256:" + "b" * 64,
        "routes": [{
            "route_id": "route-a", "channel_id": 17, "provider_account_id": "account-a",
            "provider_credential_set_version": "11111111-1111-4111-8111-111111111111",
            "route_binding_sha256": "sha256:" + "c" * 64,
            "mode": "text_to_video", "resolution": "720p", "cost_kind": "contract_rate",
            "cost_id": "22222222-2222-4222-8222-222222222222", "cost_sha256": "sha256:" + "d" * 64,
        }],
    }


def test_execution_contract_mismatch_is_a_synchronous_create_error_not_a_new_async_state():
    envelope = RelayErrorEnvelope.model_validate({
        "api_version": "v1", "schema_version": 1,
        "error": {"code": "EXECUTION_CONTRACT_MISMATCH", "message": "Execution contract changed",
                  "retryable": False, "details": {}, "request_id": "test-contract-rejection"},
    })
    assert envelope.error.code == "EXECUTION_CONTRACT_MISMATCH"
    assert envelope.error.code not in get_args(RelayAsyncErrorCode)


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "unsorted", "boolean_channel", "cost_id", "extra", "whitespace", "rectangle"])
def test_execution_contract_rejects_ambiguous_authority(mutation):
    value = wire()
    if mutation == "empty":
        value["routes"] = []
    elif mutation == "duplicate":
        value["routes"] *= 2
    elif mutation == "unsorted":
        value["routes"] = [dict(value["routes"][0], route_id="z"), value["routes"][0]]
    elif mutation == "boolean_channel":
        value["routes"][0]["channel_id"] = True
    elif mutation == "cost_id":
        value["routes"][0]["cost_id"] = "latest"
    elif mutation == "extra":
        value["routes"][0]["api_key"] = "not-a-real-secret"
    elif mutation == "whitespace":
        value["routes"][0]["provider_account_id"] = " account-a"
    else:
        value["routes"].append(dict(value["routes"][0], route_id="route-b", resolution="1080p"))
    with pytest.raises(ValidationError):
        ExecutionContract.model_validate(value)


def test_execution_hash_is_key_order_independent_and_sensitive_to_cost_and_credential():
    value = wire()
    go_fixture = deepcopy(value)
    go_fixture["routes"][0]["channel_id"] = 7
    assert ExecutionContract.model_validate(go_fixture).content_sha256() == (
        "sha256:0ec49558c2c5ed3417ab0b2ba7820dfb27c82250823439810229c4683fd981a6"
    )
    original = ExecutionContract.model_validate(value).content_sha256()
    assert ExecutionContract.model_validate(dict(reversed(list(value.items())))).content_sha256() == original
    for field in ("provider_credential_set_version", "cost_sha256"):
        changed = deepcopy(value)
        changed["routes"][0][field] = "sha256:" + "e" * 64
        assert ExecutionContract.model_validate(changed).content_sha256() != original


def test_legacy_request_round_trip_does_not_add_null_pin():
    value = {
        "client_reference_id": "legacy", "model": "model", "expected_capability_revision": "sha256:" + "a" * 64,
        "mode": "text_to_video", "inputs": {"prompt": "legacy", "assets": []},
        "output": {"duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p", "count": 1, "face_enabled": False},
        "metadata": {}, "callback": None,
    }
    normalized = RelayGenerationRequest.model_validate(value).model_dump(mode="json")
    assert "execution_contract" not in normalized
    assert RelayGenerationRequest.model_validate(normalized).model_dump(mode="json") == normalized
    value["execution_contract"] = wire()
    value["output"]["resolution"] = "1080p"
    with pytest.raises(ValidationError):
        RelayGenerationRequest.model_validate(value)


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_new_commercial_task_persists_private_contract_and_only_public_digest(app, client, tenant, scope):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        outbox = session.scalar(select(RelaySubmissionOutbox).where(RelaySubmissionOutbox.task_id == task_id))
        contract = ExecutionContract.model_validate(outbox.relay_payload["execution_contract"])
        assert task.pricing_snapshot["execution_contract_sha256"] == contract.content_sha256()
        assert len(contract.routes) == 1 and contract.routes[0].resolution == "2k"
        assert "provider_account_id" not in response.text
        assert "cost_sha256" not in response.text
    before = _counts(app, wallet_type, wallet_id)
    app.state.relay_client = None
    replay = client.post(path, headers=headers, json=body)
    assert replay.status_code == 201 and replay.json()["id"] == task_id
    assert _counts(app, wallet_type, wallet_id) == before


@pytest.mark.parametrize("scope", ["company", "personal"])
def test_missing_cost_revision_fails_before_reserving_points(app, client, tenant, scope):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    relay = app.state.relay_client
    # Readiness itself may remain a historical projection; commercial execution
    # is not permitted to infer a full rate digest from its source document.
    for model in relay.evidence.models:
        for route in model.routes:
            for rectangle in route.provider_cost_rectangles:
                rectangle.cost_revision_sha256 = None
    before = _counts(app, wallet_type, wallet_id)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert _counts(app, wallet_type, wallet_id) == before


@pytest.mark.parametrize("echo", [None, "sha256:" + "0" * 64])
def test_accepted_without_original_execution_echo_holds_unknown_reservation(app, client, tenant, echo):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, "company")
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 201, response.text
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, response.json()["id"])
        revision = task.capability_snapshot["relay_capability_revision"]
    accepted = accepted_response(str(uuid4())).model_copy(update={
        "execution_contract_sha256": echo, "expected_capability_revision": revision, "capability_revision": revision,
    })
    result = RelayOutboxDispatcher(app.state.session_factory, ScriptedRelayClient(accepted)).dispatch_once()
    assert result.status == "reconciliation_required"
    assert _counts(app, wallet_type, wallet_id) == (940, 60, 1, 1)


def test_terminal_without_pinned_echo_cannot_release_or_settle():
    task = SimpleNamespace(pricing_snapshot={"execution_contract_sha256": ExecutionContract.model_validate(wire()).content_sha256()})
    for status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        with pytest.raises(ConflictError, match="Relay"):
            RelayStatusService.apply_to_locked_task(None, task=task, company_id="company", task_id="task",
                                                   relay_job_id="job", target_status=status)
    with pytest.raises(ConflictError):
        require_execution_digest(SimpleNamespace(pricing_snapshot={}), "sha256:" + "a" * 64)


@pytest.mark.parametrize("field", [None, "cost", "execution", "provider_account_id", "provider_channel_id", "provider_credential_version", "route_key", "routing_release_sha256"])
def test_actual_cost_must_match_the_approved_account_route_and_rate(field):
    contract = ExecutionContract.model_validate(wire())
    task = SimpleNamespace(id="task", pricing_snapshot={"execution_contract_sha256": contract.content_sha256()})
    outbox = SimpleNamespace(relay_payload={"execution_contract": wire()})
    session = SimpleNamespace(scalar=lambda _: outbox)
    identity = {
        "provider_account_id": "account-a", "provider_channel_id": 17,
        "provider_credential_version": wire()["routes"][0]["provider_credential_set_version"],
        "route_key": "route-a", "routing_release_sha256": contract.routing_release_sha256,
    }
    digest, cost = contract.content_sha256(), contract.routes[0].cost_sha256
    if field == "execution":
        digest = "sha256:" + "0" * 64
    elif field == "cost":
        cost = "sha256:" + "0" * 64
    elif field:
        identity[field] = "different"
    if field is None:
        require_execution_cost(session, task=task, identity=identity, execution_digest=digest, cost_digest=cost)
    else:
        with pytest.raises(ConflictError):
            require_execution_cost(session, task=task, identity=identity, execution_digest=digest, cost_digest=cost)


def test_modified_outbox_contract_is_rejected_without_damaging_the_original(app, client, tenant):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, "company")
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        revision = task.capability_snapshot["relay_capability_revision"]
        outbox = session.scalar(select(RelaySubmissionOutbox).where(RelaySubmissionOutbox.task_id == task_id))
        original = deepcopy(outbox.relay_payload)
    with pytest.raises(RuntimeError, match="execution contract"):
        with app.state.session_factory.begin() as session:
            outbox = session.scalar(select(RelaySubmissionOutbox).where(RelaySubmissionOutbox.task_id == task_id))
            changed = deepcopy(outbox.relay_payload)
            changed["execution_contract"]["routes"][0]["cost_sha256"] = "sha256:" + "0" * 64
            outbox.relay_payload = changed
    with app.state.session_factory() as session:
        outbox = session.scalar(select(RelaySubmissionOutbox).where(RelaySubmissionOutbox.task_id == task_id))
        assert outbox.relay_payload == original
    assert _counts(app, wallet_type, wallet_id) == (940, 60, 1, 1)
    accepted = accepted_response(str(uuid4())).model_copy(update={
        "execution_contract_sha256": ExecutionContract.model_validate(original["execution_contract"]).content_sha256(),
        "expected_capability_revision": revision, "capability_revision": revision,
    })
    result = RelayOutboxDispatcher(app.state.session_factory, ScriptedRelayClient(accepted)).dispatch_once()
    assert result.status == "sent"
    assert _counts(app, wallet_type, wallet_id) == (940, 60, 1, 1)


@pytest.mark.parametrize("scope", ["company", "personal"])
@pytest.mark.parametrize("stage_first", [False, True])
def test_signed_cost_receipt_binds_both_versions_once_without_changing_points(app, client, tenant, scope, stage_first):
    _, _, headers, path, body, wallet_type, wallet_id = _task_fixture(app, client, tenant, scope)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 201, response.text
    task_id, job_id = response.json()["id"], str(uuid4())
    with app.state.session_factory() as session:
        task = session.get(GenerationTask, task_id)
        outbox = session.scalar(select(RelaySubmissionOutbox).where(RelaySubmissionOutbox.task_id == task_id))
        contract = ExecutionContract.model_validate(outbox.relay_payload["execution_contract"])
        capability = task.capability_snapshot["relay_capability_revision"]
        company_id, workspace_id = task.company_id, task.personal_workspace_id
    accepted = accepted_response(job_id).model_copy(update={
        "execution_contract_sha256": contract.content_sha256(),
        "expected_capability_revision": capability, "capability_revision": capability,
    })
    result = RelayOutboxDispatcher(app.state.session_factory, ScriptedRelayClient(accepted)).dispatch_once()
    assert result.status == "sent"
    before = _counts(app, wallet_type, wallet_id)
    route = contract.routes[0]
    payload = _relay_cost_payload(suffix="pinned-cost", amount_cents=35,
                                 channel_key=route.route_id, channel_type="official",
                                 company_id=company_id, task_id=task_id, relay_job_id=job_id)
    if workspace_id:
        payload["personal_workspace_id"] = workspace_id
    payload.update(_bound_provider_identity(route_id=17, route_key=route.route_id))
    payload.update(provider_account_id=route.provider_account_id, provider_channel_id=route.channel_id,
                   provider_name="test-provider",
                   provider_credential_version=route.provider_credential_set_version,
                   routing_release_sha256=contract.routing_release_sha256,
                   execution_contract_sha256=contract.content_sha256(), provider_cost_revision_sha256=route.cost_sha256)
    if stage_first:
        with app.state.session_factory.begin() as session:
            bind_task_provider_route_identity(session.get(GenerationTask, task_id), payload)
    for field in ("execution_contract_sha256", "provider_cost_revision_sha256", "routing_release_sha256"):
        bad = {**payload, field: "sha256:" + "0" * 64}
        rejected = _post_signed(client, bad, event_id=str(uuid4()))
        assert rejected.status_code == 409, rejected.text
    event_id = str(uuid4())
    first = _post_signed(client, payload, event_id=event_id)
    assert first.status_code == 201, first.text
    assert first.json()["relay_payload_sha256"] == hashlib.sha256(_raw(payload)).hexdigest()
    repeated = _post_signed(client, payload, event_id=event_id)
    assert repeated.status_code == 201 and repeated.json()["id"] == first.json()["id"]
    assert _counts(app, wallet_type, wallet_id) == before
    with app.state.session_factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        original_evidence = deepcopy(task.provider_route_evidence)
        bind_task_provider_route_identity(task, payload)
        assert task.provider_route_evidence == original_evidence
        assert task.pricing_snapshot["execution_contract_sha256"] == contract.content_sha256()
        assert len(session.scalars(select(ChannelCostEntry)).all()) == 1
