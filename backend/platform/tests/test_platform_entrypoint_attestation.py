from __future__ import annotations

from types import SimpleNamespace
import sys
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from platform_api.config import Settings
from platform_api import dispatcher
from platform_api import download_gateway_registration_worker as gateway_worker
from platform_api import main as platform_main
from platform_api import publishing_worker
from platform_api import relay_sync_worker
from platform_api import timeout_worker
from platform_api.database_privileges import PlatformDatabaseAttestationError


class _AttestationStopped(RuntimeError):
    pass


@pytest.mark.parametrize(
    "module",
    (
        dispatcher,
        relay_sync_worker,
        timeout_worker,
        publishing_worker,
        gateway_worker,
    ),
)
def test_worker_entrypoint_attests_before_logging_or_runtime_factory(
    module, monkeypatch
):
    events: list[str] = []
    settings = SimpleNamespace(database_url="sqlite+pysqlite://")
    monkeypatch.setattr(sys, "argv", [module.__name__])
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda _: events.append("settings") or settings,
    )
    monkeypatch.setattr(
        module,
        "build_engine",
        lambda _: events.append("engine") or object(),
    )

    def stop_at_attestation(*_):
        events.append("attestation")
        raise _AttestationStopped

    monkeypatch.setattr(module, "attest_platform_database", stop_at_attestation)
    monkeypatch.setattr(
        module.logging,
        "basicConfig",
        lambda **_: pytest.fail("logging ran before database attestation"),
    )
    with pytest.raises(_AttestationStopped):
        module.main()
    assert events == ["settings", "engine", "attestation"]


def test_api_factory_attests_before_framework_or_table_io(monkeypatch):
    events: list[str] = []
    settings = SimpleNamespace(database_url="sqlite+pysqlite://")
    monkeypatch.setattr(
        platform_main,
        "build_engine",
        lambda _: events.append("engine") or object(),
    )

    def stop_at_attestation(*_):
        events.append("attestation")
        raise _AttestationStopped

    monkeypatch.setattr(
        platform_main,
        "attest_platform_database",
        stop_at_attestation,
    )
    with pytest.raises(_AttestationStopped):
        platform_main.create_app(settings=settings)
    assert events == ["engine", "attestation"]


@pytest.mark.parametrize(
    "invoke",
    (
        lambda preflight, service: dispatcher.run_loop(
            service,
            stop_event=Event(),
            once=True,
            preflight=preflight,
        ),
        lambda preflight, service: relay_sync_worker.run_loop(
            service,
            stop_event=Event(),
            once=True,
            preflight=preflight,
        ),
        lambda preflight, service: timeout_worker.run_loop(
            service,
            stop_event=Event(),
            interval_seconds=1,
            once=True,
            preflight=preflight,
        ),
        lambda preflight, service: publishing_worker.run_loop(
            service,
            stop_event=Event(),
            once=True,
            preflight=preflight,
        ),
        lambda preflight, service: gateway_worker.run_loop(
            service,
            stop_event=Event(),
            interval_seconds=1,
            batch_size=1,
            once=True,
            preflight=preflight,
        ),
    ),
)
def test_periodic_attestation_failure_prevents_next_worker_side_effect(invoke):
    calls: list[str] = []

    def fail_closed():
        calls.append("attestation")
        raise PlatformDatabaseAttestationError("synthetic drift")

    service = SimpleNamespace(
        dispatch_once=lambda: calls.append("side_effect"),
        poll_once=lambda: calls.append("side_effect"),
        scan_once=lambda: calls.append("side_effect"),
        run_once=lambda: calls.append("side_effect"),
    )
    with pytest.raises(PlatformDatabaseAttestationError):
        invoke(fail_closed, service)
    assert calls == ["attestation"]


def test_ready_fails_closed_when_database_boundary_drifts(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    app = platform_main.create_app(
        settings=Settings(
            database_url="sqlite+pysqlite://",
            auto_create_tables=True,
        ),
        engine=engine,
        input_asset_store=SimpleNamespace(kind="huawei_obs"),
    )

    def fail_closed(*_):
        raise PlatformDatabaseAttestationError("synthetic drift")

    monkeypatch.setattr(
        platform_main,
        "attest_platform_database_connection",
        fail_closed,
    )
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "service": "customer-platform",
            "database": "unavailable",
        }
    finally:
        engine.dispose()
