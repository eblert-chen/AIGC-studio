from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import os
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from platform_api.models import OidcLoginTransaction, utcnow
from platform_api.services.authentication import OidcService, request_ip_hash
from platform_api.services.errors import DomainError


TEST_URL_ENV = "PLATFORM_AUTH_POSTGRES_TEST_URL"


@pytest.mark.skipif(
    not os.getenv(TEST_URL_ENV),
    reason="requires a dedicated PostgreSQL 16 auth concurrency database",
)
def test_postgres_oidc_ip_limit_serializes_the_last_remaining_slot() -> None:
    database_url = os.environ[TEST_URL_ENV]
    database_name = make_url(database_url).database or ""
    if "auth_concurrency" not in database_name:
        pytest.skip("auth concurrency mutation requires an explicit canary database")

    engine = create_engine(database_url, pool_size=4, max_overflow=0)
    settings = SimpleNamespace(
        oidc_enabled=True,
        jwt_signing_secret="postgres-auth-rate-limit-test-pepper",
        oidc_login_ip_window_seconds=600,
        oidc_login_ip_max_attempts=3,
        oidc_login_transaction_ttl_seconds=60,
        oidc_client_id="postgres-concurrency-client",
        oidc_redirect_uri="https://platform.example.test/api/v1/auth/callback",
        oidc_authorization_endpoint="https://identity.example.test/authorize",
    )
    ip_address = "198.51.100.27"
    ip_hash = request_ip_hash(
        ip_address,
        pepper=settings.jwt_signing_secret,
    )
    start_barrier = Barrier(3)
    both_lock_attempts = Event()
    winner_ready = Event()
    release_winner = Event()
    observation_lock = Lock()
    observed_lock_attempts = 0

    def observe_advisory_lock(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal observed_lock_attempts
        if "pg_advisory_xact_lock" not in statement:
            return
        with observation_lock:
            observed_lock_attempts += 1
            if observed_lock_attempts == 2:
                both_lock_attempts.set()

    event.listen(engine, "before_cursor_execute", observe_advisory_lock)
    try:
        OidcLoginTransaction.__table__.drop(engine, checkfirst=True)
        OidcLoginTransaction.__table__.create(engine)
        now = utcnow()
        with Session(engine) as session:
            for index in range(settings.oidc_login_ip_max_attempts - 1):
                session.add(
                    OidcLoginTransaction(
                        state_digest=hashlib.sha256(
                            f"existing-state-{index}".encode()
                        ).hexdigest(),
                        nonce=f"existing-nonce-{index}",
                        code_verifier=f"existing-verifier-{index}",
                        return_to="/",
                        created_at=now,
                        expires_at=now + timedelta(seconds=60),
                        ip_hash=ip_hash,
                    )
                )
            session.commit()

        def attempt() -> str:
            with Session(engine) as session:
                start_barrier.wait(timeout=10)
                try:
                    OidcService.start_login(
                        session,
                        settings=settings,
                        return_to="/",
                        prompt=None,
                        ip_address=ip_address,
                    )
                except DomainError as exc:
                    session.rollback()
                    return exc.code
                winner_ready.set()
                if not release_winner.wait(timeout=10):
                    session.rollback()
                    return "winner_release_timeout"
                session.commit()
                return "allowed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt) for _ in range(2)]
            start_barrier.wait(timeout=10)
            try:
                assert both_lock_attempts.wait(timeout=10)
                assert winner_ready.wait(timeout=10)
                assert not any(future.done() for future in futures)
            finally:
                release_winner.set()
            results = [future.result(timeout=10) for future in futures]

        assert sorted(results) == ["allowed", "auth_rate_limited"]
        with Session(engine) as session:
            assert session.scalar(select(func.count(OidcLoginTransaction.id))) == 3
    finally:
        release_winner.set()
        event.remove(engine, "before_cursor_execute", observe_advisory_lock)
        OidcLoginTransaction.__table__.drop(engine, checkfirst=True)
        engine.dispose()
