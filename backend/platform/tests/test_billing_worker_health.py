import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from platform_api.billing_worker import BillingCycleResult, BillingWorkerBoundaryError, run_loop
from platform_api.billing_worker_health import is_healthy, write_health


def test_billing_health_is_local_liveness_not_a_payment_verdict(tmp_path):
    path = tmp_path / "health.json"
    assert not is_healthy(path, now=100)
    write_health(path, healthy=True, failed_jobs=(), now=100)
    assert is_healthy(path, now=101)
    assert not is_healthy(path, now=401)
    assert not is_healthy(path, now=99)
    write_health(path, healthy=False, failed_jobs=("payments",), now=101)
    assert not is_healthy(path, now=102)
    assert sorted(json.loads(path.read_text()).keys()) == ["checked_at", "failed_jobs", "healthy", "schema_version"]
    assert list(tmp_path.glob(".billing-health-*")) == []


def test_billing_health_rejects_invalid_or_relative_files(tmp_path):
    with pytest.raises(ValueError):
        write_health(Path("relative.json"), healthy=True, failed_jobs=())
    path = tmp_path / "health.json"
    for data in ("[]", "null", "broken", '{"healthy":true}', "x" * 4097):
        path.write_text(data)
        assert not is_healthy(path)


def test_boundary_failure_clears_previous_healthy_state_before_exiting(tmp_path):
    path = tmp_path / "health.json"
    write_health(path, healthy=True, failed_jobs=())
    def fail():
        raise BillingWorkerBoundaryError("rejected")
    with pytest.raises(BillingWorkerBoundaryError):
        run_loop(SimpleNamespace(run_once=fail), stop_event=Event(), once=True, health_file=path)
    assert not is_healthy(path)
    run_loop(SimpleNamespace(run_once=lambda: BillingCycleResult((), ())), stop_event=Event(), once=True, health_file=path)
    assert is_healthy(path)


def test_compose_worker_is_opt_in_secret_file_only_and_has_no_database_authority():
    root = Path(__file__).resolve().parents[3]
    config = (root / "deploy/compose.billing-worker.yml").read_text()
    assert "profiles: [billing]" in config and "read_only: true" in config
    assert "platform_api.billing_worker_health" in config
    assert "pull_policy: never" in config and "external: true" in config
    assert "DATABASE_URL" not in config and "PSP_API_KEY" not in config
    assert "--allow-loopback-http" not in config
