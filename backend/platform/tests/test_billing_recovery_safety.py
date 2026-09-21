from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import func, select

from platform_api.models import (
    BillingUnit, Company, CompanyPointLedgerEntry, CompanyPointWalletAccount,
    GenerationTask, LedgerEntry, LedgerKind, ModelDefinition, PersonalLedgerEntry,
    PersonalWalletAccount, PersonalWorkspace, PointLedgerKind, PointLotSourceKind,
    RelayOutboxStatus, RelaySubmissionOutbox, TaskStatus, User, UserAccountType,
    WalletAccount, utcnow,
)
from platform_api.relay_client import RelayPermanentError, RelayTemporaryError
from platform_api.services.billing import WalletService
from platform_api.services.company_points_billing import CompanyPointBillingService
from platform_api.services.personal_billing import PersonalWalletService
from platform_api.services.relay_outbox import RelayOutboxDispatcher, RelayOutboxService
from platform_api.services.task_cancellation import GenerationCancellationService

from .test_relay_boundary import REVISION, ScriptedRelayClient, accepted_response
from .test_relay_callbacks import callback_body, configure_callback, signed_callback


def seed_reserved_submission(factory, scope: str):
    """Create real wallet/lot/ledger reservations independently of pricing APIs."""
    suffix = uuid.uuid4().hex
    with factory.begin() as session:
        user = User(
            email=f"recovery-{suffix}@example.test", display_name="Recovery test",
            account_type=(UserAccountType.PERSONAL if scope == "personal_points"
                          else UserAccountType.COMPANY),
        )
        model = ModelDefinition(
            slug=f"recovery-{suffix}", display_name="Recovery model",
            provider_key="recovery-test", billing_mode="per_item",
        )
        session.add_all([user, model])
        session.flush()
        company_id = workspace_id = None
        if scope == "personal_points":
            workspace = PersonalWorkspace(user_id=user.id, active=True)
            session.add(workspace)
            session.flush()
            workspace_id = workspace.id
            session.add(PersonalWalletAccount(workspace_id=workspace_id))
            session.flush()
            PersonalWalletService.credit(
                session, workspace_id=workspace_id, amount_points=100,
                idempotency_key="opening", note="test-only promotional credit",
            )
        else:
            company = Company(name=f"Recovery {suffix}", billing_version=1)
            session.add(company)
            session.flush()
            company_id = company.id
            if scope == "company_points":
                session.add(WalletAccount(company_id=company_id))
                session.flush()
                CompanyPointBillingService.migrate(
                    session, company_id=company_id, expected_available_cents=0,
                    idempotency_key="migrate",
                )
                CompanyPointBillingService.credit(
                    session, company_id=company_id, amount_points=100,
                    source_kind=PointLotSourceKind.PROMOTIONAL,
                    cash_basis_cents=0, subsidy_cents=1000,
                    idempotency_key="opening",
                )
            else:
                session.add(WalletAccount(company_id=company_id))
                session.flush()
                WalletService.recharge(
                    session, company_id=company_id, amount_cents=100,
                    idempotency_key="opening",
                )
        points = scope != "company_cents"
        task = GenerationTask(
            company_id=company_id, personal_workspace_id=workspace_id,
            user_id=user.id, model_id=model.id, idempotency_key="task",
            request_fingerprint="a" * 64, status=TaskStatus.DRAFT,
            request_payload={"prompt": "recovery test", "output_count": 1},
            billing_unit=BillingUnit.POINT if points else BillingUnit.CNY_CENT,
            billing_version=2 if points else 1,
            quote_points=40 if points else None,
            quote_cents=None if points else 40,
            capability_snapshot={"relay_capability_revision": REVISION},
            pricing_snapshot={"charge_policy": "FIXED_QUOTE_ON_SUCCESS"},
        )
        session.add(task)
        session.flush()
        if scope == "personal_points":
            PersonalWalletService.reserve(
                session, workspace_id=workspace_id, task_id=task.id,
                amount_points=40, idempotency_key="reserve",
            )
        else:
            WalletService.reserve(
                session, company_id=company_id, task_id=task.id,
                amount_points=40 if points else None,
                amount_cents=None if points else 40, idempotency_key="reserve",
            )
        outbox = RelayOutboxService.enqueue(session, task=task, model=model)
        return task.id, outbox.id, company_id, workspace_id


def assert_reservation(factory, identity, scope, *, held):
    task_id, _, company_id, workspace_id = identity
    with factory() as session:
        task = session.get(GenerationTask, task_id)
        if scope == "company_cents":
            wallet = session.get(WalletAccount, company_id)
            available, reserved = wallet.available_cents, wallet.reserved_cents
            ledger, kind = LedgerEntry, LedgerKind.RELEASE
            assert task.reserved_cents == (40 if held else 0)
        elif scope == "company_points":
            wallet = session.get(CompanyPointWalletAccount, company_id)
            available, reserved = wallet.available_points, wallet.reserved_points
            ledger, kind = CompanyPointLedgerEntry, PointLedgerKind.RELEASE
            assert task.reserved_points == (40 if held else 0)
        else:
            wallet = session.get(PersonalWalletAccount, workspace_id)
            available, reserved = wallet.available_points, wallet.reserved_points
            ledger, kind = PersonalLedgerEntry, LedgerKind.RELEASE
            assert task.reserved_points == (40 if held else 0)
        assert (available, reserved) == ((60, 40) if held else (100, 0))
        assert session.scalar(select(func.count(ledger.id)).where(
            ledger.task_id == task_id, ledger.kind == kind,
        )) == (0 if held else 1)


@pytest.mark.parametrize("scope", ["company_cents", "company_points", "personal_points"])
@pytest.mark.parametrize("earlier_uncertain", [False, True])
def test_crashed_submit_replay_rejection_preserves_scope_reservation(
    app, scope, earlier_uncertain,
):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, outbox_id, _, _ = identity
    relay = ScriptedRelayClient(RelayPermanentError("later rejection cannot disprove creation"))
    dispatcher = RelayOutboxDispatcher(factory, relay, stale_after_seconds=0)
    claim = dispatcher._claim()
    exact_payload = dispatcher._materialize_payload(claim).model_dump(mode="json")
    assert dispatcher._mark_submit_attempt_started(outbox_id, claim.attempt_count)
    with factory.begin() as session:
        row = session.get(RelaySubmissionOutbox, outbox_id)
        if earlier_uncertain:
            row.submission_outcome_uncertain_at = utcnow() - timedelta(minutes=1)
        before = (row.idempotency_key, row.relay_backend_id,
                  row.relay_contract_revision, deepcopy(row.materialized_relay_payload))

    result = dispatcher.dispatch_once()
    assert result.status == "reconciliation_required"
    assert len(relay.calls) == 1
    assert dispatcher.dispatch_once().processed is False
    assert_reservation(factory, identity, scope, held=True)
    with factory() as session:
        row = session.get(RelaySubmissionOutbox, outbox_id)
        task = session.get(GenerationTask, task_id)
        assert row.submission_outcome_uncertain_at is not None
        assert (row.idempotency_key, row.relay_backend_id, row.relay_contract_revision,
                row.materialized_relay_payload) == before
        assert row.materialized_relay_payload == exact_payload
        assert task.status == TaskStatus.PROCESSING
        assert task.failure_reason is None


@pytest.mark.parametrize("scope", ["company_cents", "company_points", "personal_points"])
@pytest.mark.parametrize("after_known_noncreation", [False, True])
def test_explicit_rejection_without_unknown_predecessor_releases_once(
    app, scope, after_known_noncreation,
):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    outcomes = [RelayPermanentError("definitive rejection")]
    if after_known_noncreation:
        outcomes.insert(0, RelayTemporaryError("not accepted", submission_outcome_unknown=False))
    relay = ScriptedRelayClient(*outcomes)
    dispatcher = RelayOutboxDispatcher(factory, relay)
    if after_known_noncreation:
        assert dispatcher.dispatch_once().status == "retry"
        with factory.begin() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            assert row.relay_submit_attempted_at is None
            assert row.submission_outcome_uncertain_at is None
            row.next_attempt_at = utcnow() - timedelta(seconds=1)
    assert dispatcher.dispatch_once().status == "permanently_failed"
    assert dispatcher.dispatch_once().processed is False
    assert len(relay.calls) == (2 if after_known_noncreation else 1)
    assert_reservation(factory, identity, scope, held=False)


@pytest.mark.parametrize("scope", ["company_cents", "company_points", "personal_points"])
def test_known_noncreation_does_not_erase_prior_uncertainty(app, scope):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    relay = ScriptedRelayClient(
        RelayTemporaryError("reply lost"),
        RelayTemporaryError("not accepted this time", submission_outcome_unknown=False),
        RelayPermanentError("definitive rejection this time"),
    )
    dispatcher = RelayOutboxDispatcher(factory, relay)
    for _ in range(2):
        assert dispatcher.dispatch_once().status == "retry"
        with factory.begin() as session:
            row = session.get(RelaySubmissionOutbox, identity[1])
            assert row.submission_outcome_uncertain_at is not None
            assert row.relay_submit_attempted_at is not None
            row.next_attempt_at = utcnow() - timedelta(seconds=1)
    assert dispatcher.dispatch_once().status == "reconciliation_required"
    assert_reservation(factory, identity, scope, held=True)
    assert len({call[1] for call in relay.calls}) == 1
    assert all(call[0] == relay.calls[0][0] for call in relay.calls)


def test_crash_before_submit_after_known_noncreation_can_retry(app):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, "company_points")
    relay = ScriptedRelayClient(
        RelayTemporaryError("not accepted", submission_outcome_unknown=False),
        accepted_response(str(uuid.uuid4())),
    )
    dispatcher = RelayOutboxDispatcher(factory, relay, stale_after_seconds=0)
    assert dispatcher.dispatch_once().status == "retry"
    with factory.begin() as session:
        row = session.get(RelaySubmissionOutbox, identity[1])
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
    abandoned = dispatcher._claim()
    assert abandoned.relay_submit_attempted_at is None
    assert dispatcher.dispatch_once().status == "sent"
    assert_reservation(factory, identity, "company_points", held=True)


@pytest.mark.parametrize("scope", ["company_cents", "company_points", "personal_points"])
@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_submit_process_death_recovers_through_signed_callback_once(
    app, client, scope, terminal,
):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, outbox_id, company_id, workspace_id = identity
    job_id = str(uuid.uuid4())

    class SimulatedProcessDeath(BaseException):
        pass

    class AcceptedThenCrashed:
        calls = []

        def submit(self, payload, *, idempotency_key, request_id=None):
            self.calls.append((payload.model_dump(mode="json"), idempotency_key))
            # The Relay accepted this request. The Platform process dies before
            # the accepted response is persisted, outside ordinary error catches.
            if len(self.calls) == 1:
                raise SimulatedProcessDeath()
            return accepted_response(job_id)

    relay = AcceptedThenCrashed()
    dispatcher = RelayOutboxDispatcher(factory, relay, stale_after_seconds=0)
    with pytest.raises(SimulatedProcessDeath):
        dispatcher.dispatch_once()
    assert dispatcher.dispatch_once().status == "sent"
    assert len(relay.calls) == 2
    assert_reservation(factory, identity, scope, held=True)

    configure_callback(app)
    body = callback_body(
        task_id=task_id, relay_job_id=job_id, status=terminal,
        progress=100,
        error={"code": "CONTENT_POLICY_REJECTED", "message": "authoritative failure",
               "retryable": False}
        if terminal == "failed" else None,
        outputs=[{
            "asset_id": str(uuid.uuid4()), "object_key": f"outputs/{job_id}/result",
            "media_type": "video", "content_type": "video/mp4",
            "size_bytes": 1234, "sha256": "c" * 64,
        }] if terminal == "succeeded" else [],
    )
    for _ in range(2):
        response = signed_callback(client, body)
        assert response.status_code == 204, response.text
    with factory() as session:
        task = session.get(GenerationTask, task_id)
        outbox = session.get(RelaySubmissionOutbox, outbox_id)
        assert task.status.value == terminal
        assert task.relay_job_id == outbox.relay_job_id == job_id
        assert outbox.status == RelayOutboxStatus.SENT
        if scope == "company_cents":
            wallet = session.get(WalletAccount, company_id)
            available, reserved = wallet.available_cents, wallet.reserved_cents
            ledger, settle, release = LedgerEntry, LedgerKind.SETTLE, LedgerKind.RELEASE
        elif scope == "company_points":
            wallet = session.get(CompanyPointWalletAccount, company_id)
            available, reserved = wallet.available_points, wallet.reserved_points
            ledger, settle, release = (CompanyPointLedgerEntry, PointLedgerKind.SETTLE,
                                       PointLedgerKind.RELEASE)
        else:
            wallet = session.get(PersonalWalletAccount, workspace_id)
            available, reserved = wallet.available_points, wallet.reserved_points
            ledger, settle, release = (PersonalLedgerEntry, LedgerKind.SETTLE,
                                       LedgerKind.RELEASE)
        assert (available, reserved) == ((60, 0) if terminal == "succeeded" else (100, 0))
        assert session.scalar(select(func.count(ledger.id)).where(
            ledger.task_id == task_id, ledger.kind.in_([settle, release]),
        )) == 1


@pytest.mark.parametrize("scope", ["company_cents", "company_points"])
def test_confirmed_noncreation_cancellation_keeps_attempt_audit_and_replays_once(app, scope):
    factory = app.state.session_factory
    identity = seed_reserved_submission(factory, scope)
    task_id, outbox_id, company_id, _ = identity
    relay = ScriptedRelayClient(
        RelayTemporaryError("rate limited before acceptance", submission_outcome_unknown=False),
    )
    assert RelayOutboxDispatcher(factory, relay).dispatch_once().status == "retry"
    assert len(relay.calls) == 1
    with factory.begin() as session:
        task = session.get(GenerationTask, task_id)
        result = GenerationCancellationService.cancel_unsubmitted(
            session, company_id=company_id, task_id=task_id, actor_user_id=task.user_id,
        )
        assert result.before_summary["dispatch_attempt_count"] == 1
        assert result.before_summary["dispatch_attempted"] is True
        assert result.before_summary["relay_submit_attempted"] is not False
        assert result.before_summary["relay_submission_outstanding"] is False
        assert result.task.failure_reason == "cancelled by creator after confirming no Relay job was created"
        replay = GenerationCancellationService.cancel_unsubmitted(
            session, company_id=company_id, task_id=task_id, actor_user_id=task.user_id,
        )
        assert replay.replayed is True
    assert_reservation(factory, identity, scope, held=False)
