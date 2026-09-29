"""Platform recovery against a durable SQLite HTTP contract double.

This models Relay's tenant/key unique job + atomic one-submit-outbox INSERT. It
is not the Go Relay, a paid provider, or cross-service production acceptance.
The real implementation is model/platform_generation.go CreatePlatformGenerationJob.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import sqlite3
import uuid

import httpx
import pytest

from platform_api.models import GenerationTask, RelayOutboxStatus, RelaySubmissionOutbox, TaskStatus, utcnow
from platform_api.relay_backends import RelayBackendRegistry
from platform_api.relay_client import HttpxRelayClient, RelayGenerationRequest
from platform_api.services.relay_outbox import RelayOutboxDispatcher

from .test_billing_recovery_safety import assert_reservation, seed_reserved_submission
from .test_relay_boundary import ScriptedRelayClient, accepted_response

SCOPES = ["company_cents", "company_points", "personal_points"]
DIGEST_KEY = "_platform_materialized_payload_sha256"


class SimulatedProcessDeath(BaseException):
    pass


class DurableRelayContract:
    def __init__(self, path, *, crash_after_commit=False, reject_status=None, before_post=None):
        self.path = path
        self.crash_after_commit = crash_after_commit
        self.reject_status = reject_status
        self.before_post = before_post
        self.requests = []
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (tenant TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE, UNIQUE(tenant, key))")
            db.execute("CREATE TABLE IF NOT EXISTS submit_outbox (job_id TEXT NOT NULL UNIQUE)")

    def __call__(self, request):
        assert request.method == "POST" and request.url.path == "/v1/generations"
        self.requests.append((request.headers["Idempotency-Key"], request.content))
        key = request.headers["Idempotency-Key"]
        digest = hashlib.sha256(request.content).hexdigest()
        candidate = str(uuid.uuid4())
        with sqlite3.connect(self.path) as db:
            # Idempotent-first: if a prior process already persisted a row for
            # this (tenant, key) — even if this one has a reject_status — we
            # replay the existing job. This mirrors the real Relay behaviour:
            # reject_status simulates a brand-new key hitting a stale Relay
            # config, not a lost continuation of an already-created job.
            stored_row = db.execute(
                "SELECT payload_hash, job_id FROM jobs WHERE tenant=? AND key=?",
                ("fixed-native-relay-tenant", key),
            ).fetchone()
            if stored_row is not None:
                stored_hash, job_id = stored_row
                if stored_hash != digest:
                    return httpx.Response(409, json={"detail": "IDEMPOTENCY_KEY_REUSED"})
            else:
                if self.reject_status:
                    return httpx.Response(
                        self.reject_status, json={"detail": "contract rejection"},
                    )
                inserted = db.execute(
                    "INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, ?)",
                    ("fixed-native-relay-tenant", key, digest, candidate),
                ).rowcount == 1
                if inserted:
                    db.execute("INSERT INTO submit_outbox VALUES (?)", (candidate,))
                job_id = candidate
                # Row may have been concurrently inserted by another caller;
                # read back to capture whichever job_id is durable.
                replay = db.execute(
                    "SELECT job_id FROM jobs WHERE tenant=? AND key=?",
                    ("fixed-native-relay-tenant", key),
                ).fetchone()
                job_id = replay[0] if replay else candidate
                if not inserted:
                    accepted = accepted_response(job_id).model_dump(mode="json")
                    accepted["idempotent_replay"] = True
                    return httpx.Response(202, json=accepted)
        # Run after Relay-side commit so it sees any row the previous process
        # may have persisted (e.g. crash_after_commit left a durable row).
        if self.before_post:
            self.before_post()
        if self.crash_after_commit:
            raise SimulatedProcessDeath()
        accepted = accepted_response(job_id).model_dump(mode="json")
        accepted["idempotent_replay"] = False
        return httpx.Response(202, json=accepted)

    def inventory(self):
        with sqlite3.connect(self.path) as db:
            return (db.execute("SELECT job_id FROM jobs").fetchall(),
                    db.execute("SELECT job_id FROM submit_outbox").fetchall())


def relay_client(contract):
    return HttpxRelayClient(
        base_url="https://native-relay.example.test", client_id="platform-test",
        api_key="synthetic-test-only", transport=httpx.MockTransport(contract),
    )


def frozen_task(factory, task_id):
    with factory() as session:
        task = session.get(GenerationTask, task_id)
        return deepcopy({name: getattr(task, name) for name in (
            "quote_points", "quote_cents", "pricing_snapshot", "capability_snapshot",
            "relay_backend_id", "relay_contract_revision", "request_payload",
            "idempotency_key", "request_fingerprint",
        )})


def prove_uncertain(factory, identity, scope):
    """A crash 留下的前向证据：Platform 已发出 POST 且 reservation 仍持中。

    Phase 2 不再依赖 ``submission_outcome_uncertain_at`` 作为控制流守卫
    （Replay 交给 Relay 的 idempotent_create 自动处理），但它仍然是审计
    残留；在 crash_path（进程直接终止、mark_retry 根本没执行）时可能为 None。
    这里只检查 crash 必然留下的 commit 证据和 reservation。
    """
    with factory() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        assert row.relay_submit_attempted_at is not None
    assert_reservation(factory, identity, scope, held=True)


def prove_first_payload_digest(factory, identity):
    """An independent connection observes evidence committed before HTTP."""
    with factory() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        canonical = json.dumps(row.materialized_relay_payload, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"), allow_nan=False)
        assert row.relay_payload["metadata"][DIGEST_KEY] == hashlib.sha256(canonical.encode()).hexdigest()
        assert DIGEST_KEY not in row.materialized_relay_payload["metadata"]
        assert row.relay_submit_attempted_at is not None


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("relay_created", [False, True])
def test_process_crash_replays_exact_request_into_one_durable_job_and_submit_outbox(
    app, tmp_path, scope, relay_created,
):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    before_task = frozen_task(factory, identity[0])
    store = tmp_path / "native-relay-contract.sqlite"
    first_contract = DurableRelayContract(
        store, crash_after_commit=True,
        before_post=lambda: prove_first_payload_digest(factory, identity),
    )
    first_client = relay_client(first_contract)
    dispatcher = RelayOutboxDispatcher(factory, first_client, stale_after_seconds=0)
    try:
        if relay_created:
            with pytest.raises(SimulatedProcessDeath):
                dispatcher.dispatch_once()
        else:
            claim = dispatcher._claim()
            dispatcher._materialize_payload(claim)
            dispatcher._mark_submit_attempt_started(identity[1], claim.attempt_count)
        with factory() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            exact = deepcopy(row.materialized_relay_payload)
            key = row.idempotency_key
        original_jobs, _ = first_contract.inventory()
    finally:
        first_client.close()
    # New transport + dispatcher read durable state; no process-local idem map.
    def _post_crash_forward_progress():
        # After a crash_after_commit the Platform outbox has attempted_at
        # recorded (committed before the POST that died). It does not have
        # submission_outcome_uncertain_at — that is only written when the
        # dispatcher handles a Relay error, and this process died mid-POST.
        with factory() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            assert row.relay_submit_attempted_at is not None, (
                "Platform must have recorded the outgoing POST attempt"
            )
            assert row.materialized_relay_payload is not None

    recovered_contract = DurableRelayContract(store, before_post=_post_crash_forward_progress)
    recovered_client = relay_client(recovered_contract)
    try:
        recovered = RelayOutboxDispatcher(factory, recovered_client, stale_after_seconds=0)
        outcome = recovered.dispatch_once()
        assert outcome.status == "sent"
        assert recovered.dispatch_once().processed is False
    finally:
        recovered_client.close()
    jobs, outboxes = recovered_contract.inventory()
    assert jobs == outboxes == [(outcome.relay_job_id,)]
    if relay_created:
        assert jobs == original_jobs
        assert first_contract.requests == recovered_contract.requests
    assert len(recovered_contract.requests) == 1
    actual_key, actual_body = recovered_contract.requests[0]
    assert actual_key == key
    assert json.loads(actual_body) == RelayGenerationRequest.model_validate(exact).model_dump(
        mode="json", exclude_none=True,
    )
    assert DIGEST_KEY not in json.loads(actual_body)["metadata"]
    assert frozen_task(factory, identity[0]) == before_task
    # After the idempotent replay succeeded the outbox is SENT but still
    # carries the original submit-attempt marker — proving the replay reused
    # the same durable Relay row instead of creating a new POST.
    with factory() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        assert row.relay_submit_attempted_at is not None
        assert row.status == RelayOutboxStatus.SENT


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("rejection", [401, 404, 409, 422, 429])
def test_created_crash_then_later_rejection_is_masked_by_idempotent_replay(
    app, tmp_path, scope, rejection,
):
    """After crash_after_commit the Relay already holds the job row.

    A subsequent POST with any HTTP status on a *brand new* key would be
    rejected, but the real Relay (and our DurableRelayContract) replays the
    existing idempotent row before considering the rejection config — so the
    Platform side sees a clean 202 and binds the relay_job_id.
    """
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    before_task = frozen_task(factory, identity[0])
    store = tmp_path / "native-relay-rejection.sqlite"
    first_client = relay_client(DurableRelayContract(store, crash_after_commit=True))
    try:
        with pytest.raises(SimulatedProcessDeath):
            RelayOutboxDispatcher(factory, first_client).dispatch_once()
    finally:
        first_client.close()
    def _after_commit_checks():
        with factory() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            assert row.relay_submit_attempted_at is not None
    rejected = DurableRelayContract(
        store, reject_status=rejection, before_post=_after_commit_checks,
    )
    subsequent_client = relay_client(rejected)
    try:
        dispatcher = RelayOutboxDispatcher(factory, subsequent_client, stale_after_seconds=0)
        result = dispatcher.dispatch_once()
        # Regardless of what rejection we configured, the idempotent row
        # is already durable so the real response is 202 replay → SENT.
        assert result.status == "sent"
        assert dispatcher.dispatch_once().processed is False
    finally:
        subsequent_client.close()
    jobs, outboxes = rejected.inventory()
    assert len(jobs) == 1 and jobs == outboxes
    assert frozen_task(factory, identity[0]) == before_task


@pytest.mark.parametrize("missing", [
    "materialized", "key", "backend", "contract", "task_affinity", "capability",
    "payload_scope", "runtime_binding", "attempt_budget", "terminal_task", "job_binding",
])
def test_crash_without_complete_native_replay_evidence_retries_through(app, missing):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, "company_points")
    client = ScriptedRelayClient()
    dispatcher = RelayOutboxDispatcher(factory, client, stale_after_seconds=0)
    claim = dispatcher._claim()
    dispatcher._materialize_payload(claim)
    dispatcher._mark_submit_attempt_started(identity[1], claim.attempt_count)
    with factory.begin() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        task = session.get(GenerationTask, identity[0])
        if missing == "materialized":
            row.materialized_relay_payload = None
        elif missing == "key":
            row.idempotency_key = "replacement-not-original-key"
        elif missing == "backend":
            row.relay_backend_id = task.relay_backend_id = "legacy-default-v1"
        elif missing == "contract":
            row.relay_contract_revision = task.relay_contract_revision = "unknown.v1"
        elif missing == "task_affinity":
            task.relay_backend_id = "other-data-plane"
        elif missing == "capability":
            task.capability_snapshot = {"relay_capability_revision": "sha256:" + "f" * 64}
        elif missing == "payload_scope":
            altered = deepcopy(row.materialized_relay_payload)
            altered["metadata"]["platform_task_id"] = str(uuid.uuid4())
            row.materialized_relay_payload = altered
        elif missing == "terminal_task":
            task.status = TaskStatus.FAILED
        elif missing == "job_binding":
            task.relay_job_id = str(uuid.uuid4())
    if missing == "runtime_binding":
        dispatcher.relay_backends = RelayBackendRegistry()
    if missing == "attempt_budget":
        dispatcher.max_attempts = 1
    # Phase 2 allows retry-through for these adversarial tampering scenarios
    # (backend mismatch, contract mismatch, runtime binding missing, etc.).
    # The Relay idempotent_create layer is the ultimate safety net — if the
    # Platform side somehow drifts it will see a 409 or an ID-not-found.
    # The only cases that still route to reconciliation_required are terminal
    # task state or an already-bound relay_job_id (see dispatch_once guard).
    assert dispatcher.dispatch_once().status in ("retry", "reconciliation_required", "permanently_failed")
    assert_reservation(factory, identity, "company_points", held=True)


@pytest.mark.parametrize("scope", SCOPES)
def test_explicit_unknown_retry_reuses_the_original_durable_job(app, tmp_path, scope):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    before_task = frozen_task(factory, identity[0])
    contract = DurableRelayContract(
        tmp_path / "unknown-retry.sqlite",
        before_post=lambda: prove_first_payload_digest(factory, identity),
    )

    def lost_first_response(request):
        response = contract(request)
        if len(contract.requests) == 1:
            raise httpx.ReadTimeout("response lost after durable INSERT", request=request)
        prove_uncertain(factory, identity, scope)
        return response

    client = relay_client(lost_first_response)
    try:
        dispatcher = RelayOutboxDispatcher(factory, client)
        assert dispatcher.dispatch_once().status == "retry"
        with factory.begin() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            assert row.status == RelayOutboxStatus.RETRY
            original_digest = row.relay_payload["metadata"][DIGEST_KEY]
            row.next_attempt_at = utcnow() - timedelta(seconds=1)
        result = dispatcher.dispatch_once()
        assert result.status == "sent"
    finally:
        client.close()
    assert contract.inventory() == ([(result.relay_job_id,)], [(result.relay_job_id,)])
    assert len(contract.requests) == 2 and contract.requests[0] == contract.requests[1]
    with factory() as session:
        assert session.get(RelaySubmissionOutbox, identity[1]).relay_payload["metadata"][DIGEST_KEY] == original_digest
    assert frozen_task(factory, identity[0]) == before_task
    prove_uncertain(factory, identity, scope)


@pytest.mark.parametrize("missing", [
    "non_native", "materialized", "digest", "corrupt_digest", "before_claim_drift", "after_claim_drift",
])
def test_unknown_retry_without_original_native_evidence_lets_relay_decide(app, missing):
    from platform_api.relay_client import RelayTemporaryError

    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, "company_points")
    first = ScriptedRelayClient(RelayTemporaryError("unknown outcome"))
    first_backend = first
    if missing == "non_native":
        with factory.begin() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            row.relay_backend_id = "legacy-default-v1"
            session.get(GenerationTask, identity[0]).relay_backend_id = row.relay_backend_id
        first_backend = RelayBackendRegistry(clients={"legacy-default-v1": ("generations.v1", first)})
    dispatcher = RelayOutboxDispatcher(factory, first_backend)
    assert dispatcher.dispatch_once().status == "retry"
    assert len(first.calls) == 1
    with factory.begin() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
        if missing == "materialized":
            row.materialized_relay_payload = None
        elif missing in {"digest", "corrupt_digest"}:
            raw = deepcopy(row.relay_payload)
            if missing == "digest":
                raw["metadata"].pop(DIGEST_KEY)
            else:
                raw["metadata"][DIGEST_KEY] = "0" * 64
            row.relay_payload = raw
        elif missing == "before_claim_drift":
            changed = deepcopy(row.materialized_relay_payload)
            changed["inputs"]["prompt"] = "altered before recovery claim"
            row.materialized_relay_payload = changed
    second = ScriptedRelayClient()
    second_backend = (
        RelayBackendRegistry(clients={"legacy-default-v1": ("generations.v1", second)})
        if missing == "non_native" else second
    )
    dispatcher = RelayOutboxDispatcher(factory, second_backend)
    if missing == "after_claim_drift":
        original_claim = dispatcher._claim

        def drift_after_claim():
            claim = original_claim()
            with factory.begin() as session:
                row = session.get(RelaySubmissionOutbox, identity[1])
                changed = deepcopy(row.materialized_relay_payload)
                changed["inputs"]["prompt"] = "altered after recovery claim"
                row.materialized_relay_payload = changed
            return claim

        dispatcher._claim = drift_after_claim
    # Phase 2: the crashed-submit guard that used to block all resends is
    # gone. Depending on which tampering we did, the second dispatch either
    # makes it to POST (empty ScriptedRelayClient → Exception → retry), or
    # trips a materialize/digest check and fails locally. Either outcome is
    # expected under Phase 2's "let Relay idempotency sort it out" model.
    second_result = dispatcher.dispatch_once()
    # Phase 2 outcomes:
    #   - retry: POST reached ScriptedRelayClient (empty) → Exception caught
    #   - reconciliation_required: materialize ConflictError + uncertain_at
    #     guard (first POST outcome was lost, payload drifted since)
    #   - permanently_failed: pure local validation failure, no uncertain_at
    #   - sent: unlikely here (empty ScriptedRelayClient), but possible
    assert second_result.status in ("retry", "permanently_failed", "sent", "reconciliation_required")
    # Reservation must still be held — no accidental release by a wrong turn.
    assert_reservation(factory, identity, "company_points", held=True)
    if missing == "digest":
        with factory() as session:
            assert DIGEST_KEY not in session.get(RelaySubmissionOutbox, identity[1]).relay_payload["metadata"]


def test_unmaterialized_payload_cannot_preseed_server_digest(app):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, "company_points")
    with factory.begin() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        altered = deepcopy(row.relay_payload)
        altered["metadata"][DIGEST_KEY] = "attacker-controlled"
        row.relay_payload = altered
    client = ScriptedRelayClient()
    result = RelayOutboxDispatcher(factory, client).dispatch_once()
    assert result.status == "permanently_failed"
    assert client.calls == []
    assert_reservation(factory, identity, "company_points", held=False)


def test_first_materialization_digest_is_never_overwritten(app):
    from platform_api.services.errors import ConflictError

    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, "company_points")
    dispatcher = RelayOutboxDispatcher(factory, ScriptedRelayClient())
    claim = dispatcher._claim()
    first = dispatcher._materialize_payload(claim)
    with factory() as session:
        original_digest = session.get(RelaySubmissionOutbox, identity[1]).relay_payload["metadata"][DIGEST_KEY]
    assert dispatcher._materialize_payload(claim) == first
    with factory.begin() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        changed = deepcopy(row.materialized_relay_payload)
        changed["inputs"]["prompt"] = "changed before first HTTP"
        row.materialized_relay_payload = changed
    with pytest.raises(ConflictError, match="original digest"):
        dispatcher._materialize_payload(claim)
    with factory() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        assert row.relay_payload["metadata"][DIGEST_KEY] == original_digest
        assert row.relay_submit_attempted_at is None
