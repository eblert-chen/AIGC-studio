"""Tests for the ReconciliationRecovery worker.

Seeds state via raw SQL (bypassing REST/ORM layer) so the fixture is
independent of API surface changes.  The Recovery worker's own session
_factory + SQLAlchemy model loaders are what we actually want to exercise.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import text

from platform_api.models import (
    Company,
    CompanyBillingAccount,
    CompanyStatus,
    GenerationTask,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    utcnow,
)
from platform_api.relay_client import (
    RelayPermanentError,
    RelayTemporaryError,
)
from platform_api.relay_backends import RelayBackendRegistry
from platform_api.relay_reconciliation_recovery import (
    _JOB_LOST_WINDOW_HOURS,
    _MAX_RECOVERY_ATTEMPTS_NO_JOB_ID,
    _MAX_RECOVERY_ATTEMPTS_WITH_JOB_ID,
    ReconciliationRecovery,
)


TEST_BACKEND = "new-api-v1"
TEST_CONTRACT = "contract-sha256-2222"
TEST_CAP_REV = "sha256:" + ("1" * 64)


@pytest.fixture(autouse=True)
def _patch_billing_and_status(monkeypatch):
    """Recovery 测试跳过账务层和 RelayStatusService 的校验逻辑。

    这些下游组件已在 relay_callbacks / relay_dispatcher 测试中覆盖，
    recovery worker 单测只需验证状态转换和调用时机。
    """
    from platform_api.services import relay_status as rs_mod
    from platform_api.models import TaskStatus

    def _fake_apply(session, *, task, company_id, task_id, relay_job_id,
                    target_status, outputs=None, failure_reason="",
                    error_snapshot=None, reservation_action=None,
                    personal_workspace_id=None, execution_contract_sha256=None):
        task.status = target_status
        task.relay_job_id = relay_job_id
        task.relay_error_snapshot = error_snapshot or ({"message": failure_reason} if failure_reason else None)
        if target_status == TaskStatus.SUCCEEDED and outputs:
            task.output_artifacts = [
                {
                    "asset_id": getattr(o, "asset_id", "mock-asset"),
                    "media_type": getattr(o, "media_type", "video"),
                    "content_type": getattr(o, "content_type", "video/mp4"),
                    "size_bytes": getattr(o, "size_bytes", 1024),
                    "sha256": getattr(o, "sha256", "a" * 64),
                }
                for o in outputs
            ]
        elif target_status == TaskStatus.FAILED:
            task.output_artifacts = []
        return task

    monkeypatch.setattr(rs_mod.RelayStatusService, "apply_to_locked_task", _fake_apply)

    # 延迟 import 的 billing 模块（release_failure 在 _recover_no_job_id 路径用）
    import platform_api.services.billing as billing_mod
    import platform_api.services.personal_billing as pbilling_mod

    monkeypatch.setattr(billing_mod.WalletService, "release_failure", lambda *a, **kw: None)
    monkeypatch.setattr(billing_mod.WalletService, "settle_success", lambda *a, **kw: None)
    monkeypatch.setattr(pbilling_mod.PersonalWalletService, "release_failure", lambda *a, **kw: None)
    monkeypatch.setattr(pbilling_mod.PersonalWalletService, "settle_success", lambda *a, **kw: None)


# ---------- helpers ----------


class _StubRelayClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.get_calls: list[str] = []

    def get(self, relay_job_id, *, request_id=None):
        self.get_calls.append(relay_job_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def submit(self, payload, *, idempotency_key, request_id=None):  # pragma: no cover
        raise AssertionError("submit should never be called from recovery worker")


class _ScriptedRegistry(RelayBackendRegistry):
    """Registry that ignores backend_id/contract_revision and always returns its one client."""

    def __init__(self, client):
        self._client = client
        super().__init__(
            default_backend_id=TEST_BACKEND,
            default_contract_revision=TEST_CONTRACT,
        )

    def resolve(self, *, backend_id, contract_revision):
        return self._client


def _seed_reconciliation(app, *, job_id: str | None, submission_uncertain: bool = False,
                          recovery_attempt_count: int = 0,
                          next_recovery_at: object = None,
                          old_submission: bool = False,
                          task_relay_job_id: object = "__same_as_outbox__"):
    """Raw-insert company → wallet → task → outbox, outbox marked RECONCILIATION_REQUIRED.

    ``task_relay_job_id`` 默认与 outbox 的 ``job_id`` 一致（两侧已绑定的
    正常场景）；显式传 None 可模拟「outbox 有 job_id 但 task 未绑定」的
    遗留/异常状态。
    """
    if task_relay_job_id == "__same_as_outbox__":
        task_relay_job_id = job_id
    now = utcnow()
    with app.state.session_factory.begin() as s:
        # Company + billing (ORM, minimal columns).
        company = Company(name="Recovery Co", status=CompanyStatus.ACTIVE, billing_version=2)
        s.add(company)
        s.flush()
        billing = CompanyBillingAccount(
            company_id=company.id,
            active_contract_version_id=str(uuid.uuid4()),
        )
        s.add(billing)
        s.flush()

        # Wallet (raw SQL — ORM migration fields are NOT NULL).
        s.execute(text(
            "INSERT INTO company_point_wallet_accounts "
            "(company_id, available_points, reserved_points, reversal_reserved_points, "
            "debt_points, migration_idempotency_key, migrated_from_available_cents, "
            "migration_remainder_cents, migration_rounding_grant_points, created_at, updated_at) "
            "VALUES (:cid, 10000, 100, 0, 0, :mk, 0, 0, 0, :now, :now)"
        ), {"cid": company.id, "mk": f"seed-{uuid.uuid4()}", "now": now})

        # Task (raw SQL — full compact row, billing_version=2 POINT scope).
        task_id = str(uuid.uuid4())
        submit_time = now - timedelta(hours=_JOB_LOST_WINDOW_HOURS + 2) if old_submission else now
        s.execute(text(
            "INSERT INTO generation_tasks "
            "(id, company_id, user_id, model_id, idempotency_key, request_fingerprint, "
            "status, relay_backend_id, relay_contract_revision, relay_job_id, "
            "capability_snapshot, pricing_snapshot, quote_points, reserved_points, "
            "reserved_cents, request_payload, output_artifacts, "
            "billing_unit, billing_version, created_at, updated_at) "
            "VALUES (:tid, :cid, 'user-1', :mid, :ikey, :finger, "
            "'PROCESSING', :backend, :contract, :tjid, "
            ":cap, :price, NULL, 100, 0, :payload, '[]', "
            "'POINT', 2, :now, :now)"
        ), {
            "tid": task_id, "cid": company.id,
            "mid": f"model-{uuid.uuid4()}",
            "ikey": f"task-ikey-{uuid.uuid4()}",
            "finger": f"fp-{uuid.uuid4().hex[:16]}",
            "backend": TEST_BACKEND, "contract": TEST_CONTRACT,
            "tjid": task_relay_job_id,
            "cap": f'{{"relay_capability_revision": "{TEST_CAP_REV}"}}',
            "price": "{}",
            "payload": '{"prompt":"test","mode":"text_to_video","duration_seconds":5,"aspect_ratio":"16:9","resolution":"720p","output_count":1}',
            "now": now,
        })

        # Outbox (raw SQL — full row).
        outbox_id = str(uuid.uuid4())
        relay_payload = (
            '{"client_reference_id":"' + task_id + '",'
            '"metadata":{"_platform_materialized_payload_sha256":"abc123"},'
            '"model":"test-model","mode":"text_to_video",'
            '"expected_capability_revision":"' + TEST_CAP_REV + '"}'
        )
        s.execute(text(
            "INSERT INTO relay_submission_outbox "
            "(id, company_id, task_id, status, idempotency_key, relay_backend_id, relay_contract_revision, "
            "relay_payload, materialized_relay_payload, relay_job_id, relay_submit_attempted_at, "
            "submission_outcome_uncertain_at, attempt_count, next_attempt_at, last_error, "
            "recovery_attempt_count, next_recovery_at, created_at, updated_at) "
            "VALUES (:oid, :cid, :tid, 'RECONCILIATION_REQUIRED', :ikey, :backend, :contract, "
            ":payload, :mpayload, :jid, :submit_at, :uncertain_at, 1, :now, 'seed', "
            ":rac, :nra, :now, :now)"
        ), {
            "oid": outbox_id, "cid": company.id, "tid": task_id,
            "ikey": f"platform-task-{task_id}", "backend": TEST_BACKEND,
            "contract": TEST_CONTRACT, "payload": relay_payload, "mpayload": relay_payload,
            "jid": job_id, "submit_at": submit_time,
            "uncertain_at": submit_time if submission_uncertain else None,
            "rac": recovery_attempt_count, "nra": next_recovery_at, "now": now,
        })
        return outbox_id, task_id


def _load(app, outbox_id, task_id):
    with app.state.session_factory() as s:
        ob = s.get(RelaySubmissionOutbox, outbox_id)
        task = s.get(GenerationTask, task_id)
    return ob, task


# ---------- Case A: succeeded → auto-bind + settle ----------


def test_recovery_binds_outbox_and_settles_on_relay_success(app):
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id)

    snapshot = MagicMock()
    snapshot.id = job_id
    snapshot.status = "succeeded"
    snapshot.outputs = [MagicMock()]
    snapshot.outputs[0].asset_id = str(uuid.uuid4())
    snapshot.outputs[0].object_key = f"relay/{job_id}.mp4"
    snapshot.outputs[0].media_type = "video"
    snapshot.outputs[0].content_type = "video/mp4"
    snapshot.outputs[0].size_bytes = 1024
    snapshot.outputs[0].sha256 = "a" * 64
    snapshot.outputs[0].safe_metadata.return_value = {
        "asset_id": snapshot.outputs[0].asset_id,
        "media_type": "video",
        "content_type": "video/mp4",
        "size_bytes": 1024,
        "sha256": "a" * 64,
    }
    snapshot.error = None
    snapshot.reservation_action = "settle"
    snapshot.execution_contract_sha256 = None
    stub = _StubRelayClient([snapshot])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    touched = recovery.run_once()
    assert touched == 1
    assert stub.get_calls == [job_id]

    ob, task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.SENT
    assert task.status == TaskStatus.SUCCEEDED


# ---------- Case B: 404 old submission → RETRY ----------


def test_recovery_resets_outbox_to_retry_on_relay_404(app):
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id, old_submission=True)

    stub = _StubRelayClient([RelayPermanentError("not found", response_status=404)])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    recovery.run_once()

    ob, _task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.RETRY
    assert "Relay returned 404" in (ob.last_error or "")


def test_recovery_404_recent_job_backs_off(app):
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id)  # recent submission

    stub = _StubRelayClient([RelayPermanentError("not found", response_status=404)])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    recovery.run_once()

    ob, _task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.RECONCILIATION_REQUIRED  # unchanged
    assert ob.recovery_attempt_count == 1
    assert ob.next_recovery_at is not None


def test_recovery_exhausts_with_job_id_after_cap(app):
    """Known relay_job_id but probes never resolve → PERMANENTLY_FAILED at the cap."""
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(
        app, job_id=job_id,
        recovery_attempt_count=_MAX_RECOVERY_ATTEMPTS_WITH_JOB_ID - 1,
    )

    stub = _StubRelayClient([])  # cap is checked before probing
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    touched = recovery.run_once()

    assert touched == 1
    assert stub.get_calls == []
    ob, task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.PERMANENTLY_FAILED
    assert ob.next_recovery_at is None
    assert str(_MAX_RECOVERY_ATTEMPTS_WITH_JOB_ID) in (ob.last_error or "")
    assert task.status == TaskStatus.FAILED


def test_recovery_binds_task_job_id_when_task_not_yet_bound(app, monkeypatch):
    """outbox 有 relay_job_id 但 task 未绑定时，_bind_and_apply 必须先补绑再 apply。

    真实 apply_to_locked_task 硬校验 task.relay_job_id == relay_job_id，
    这里用带同样校验的替身防回归（autouse fixture 的 fake 不校验）。
    """
    from platform_api.services import relay_status as rs_mod

    def _checked_apply(session, *, task, relay_job_id, target_status, **kw):
        assert task.relay_job_id == relay_job_id, (
            "apply_to_locked_task 要求 task.relay_job_id 已绑定后才调用"
        )
        task.status = target_status
        return task

    monkeypatch.setattr(
        rs_mod.RelayStatusService, "apply_to_locked_task", _checked_apply
    )

    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(
        app, job_id=job_id, task_relay_job_id=None,
    )

    snapshot = MagicMock()
    snapshot.id = job_id
    snapshot.status = "succeeded"
    snapshot.outputs = []
    snapshot.error = None
    snapshot.reservation_action = "settle"
    snapshot.execution_contract_sha256 = None
    stub = _StubRelayClient([snapshot])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    touched = recovery.run_once()

    assert touched == 1
    ob, task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.SENT
    assert task.relay_job_id == job_id
    assert task.status == TaskStatus.SUCCEEDED


def test_recovery_snapshot_id_mismatch_backs_off(app):
    """Relay 返回的 snapshot.id 与探测的 relay_job_id 不一致 → 锁内退避。

    防回归点：退避必须写在外层持锁事务内（_apply_backoff_in_tx），
    不得嵌套新事务更新同一行（Postgres 下会挂起至 lock timeout）。
    """
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id)

    snapshot = MagicMock()
    snapshot.id = str(uuid.uuid4())  # 与探测的 job_id 不同
    snapshot.status = "succeeded"
    snapshot.outputs = []
    snapshot.error = None
    snapshot.reservation_action = "settle"
    snapshot.execution_contract_sha256 = None
    stub = _StubRelayClient([snapshot])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    touched = recovery.run_once()

    assert touched == 0
    ob, _task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.RECONCILIATION_REQUIRED
    assert ob.recovery_attempt_count == 1
    assert ob.next_recovery_at is not None


# ---------- Case C: no relay_job_id → exhaustion ----------


def test_recovery_exhausts_unknown_outbox(app):
    outbox_id, task_id = _seed_reconciliation(
        app, job_id=None, submission_uncertain=True,
        recovery_attempt_count=_MAX_RECOVERY_ATTEMPTS_NO_JOB_ID - 1,
    )

    recovery = ReconciliationRecovery(
        app.state.session_factory,
        _ScriptedRegistry(_StubRelayClient([])),
        batch_size=10,
    )
    touched = recovery.run_once()
    assert touched == 1

    ob, task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.PERMANENTLY_FAILED
    assert task.status == TaskStatus.FAILED


def test_recovery_backs_off_before_exhausting_unknown_outbox(app):
    outbox_id, task_id = _seed_reconciliation(
        app, job_id=None, submission_uncertain=True, recovery_attempt_count=0,
    )

    recovery = ReconciliationRecovery(
        app.state.session_factory,
        _ScriptedRegistry(_StubRelayClient([])),
        batch_size=10,
    )
    recovery.run_once()

    ob, _task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.RECONCILIATION_REQUIRED
    assert ob.recovery_attempt_count == 1
    assert ob.next_recovery_at is not None


# ---------- Case D: Relay processing → back off ----------


def test_recovery_backs_off_when_relay_still_processing(app):
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id)

    proc = MagicMock()
    proc.id = job_id
    proc.status = "processing"
    proc.outputs = []
    proc.error = None
    proc.reservation_action = "hold"
    proc.execution_contract_sha256 = None
    stub = _StubRelayClient([proc])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    recovery.run_once()

    ob, _task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.RECONCILIATION_REQUIRED
    assert ob.recovery_attempt_count == 1
    assert ob.next_recovery_at is not None


# ---------- Case E: failed → bind + release ----------


def test_recovery_binds_outbox_and_releases_on_relay_failure(app):
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id)

    snapshot = MagicMock()
    snapshot.id = job_id
    snapshot.status = "failed"
    snapshot.outputs = []
    snapshot.error = MagicMock()
    snapshot.error.code = "UPSTREAM_FAILED"
    snapshot.error.message = "provider exploded"
    snapshot.error.retryable = False
    snapshot.reservation_action = "release"
    snapshot.execution_contract_sha256 = None
    stub = _StubRelayClient([snapshot])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    touched = recovery.run_once()
    assert touched == 1

    ob, task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.SENT
    assert task.status == TaskStatus.FAILED


# ---------- Case F: temp error → back off ----------


def test_recovery_backs_off_on_relay_temp_error(app):
    job_id = str(uuid.uuid4())
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id)

    stub = _StubRelayClient([RelayTemporaryError("upstream 503", response_status=503)])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    recovery.run_once()

    ob, _task = _load(app, outbox_id, task_id)
    assert ob.status == RelayOutboxStatus.RECONCILIATION_REQUIRED
    assert ob.recovery_attempt_count == 1


# ---------- Case G: future next_recovery_at → skip ----------


def test_recovery_batch_skips_rows_not_yet_due(app):
    job_id = str(uuid.uuid4())
    future = utcnow() + timedelta(hours=1)
    outbox_id, task_id = _seed_reconciliation(app, job_id=job_id, next_recovery_at=future)

    stub = _StubRelayClient([RelayPermanentError("should not be called")])
    recovery = ReconciliationRecovery(
        app.state.session_factory, _ScriptedRegistry(stub), batch_size=10
    )
    touched = recovery.run_once()
    assert touched == 0
    assert not stub.get_calls
