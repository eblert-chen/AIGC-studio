from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import time
import threading
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - Windows collection gate
    fcntl = None  # type: ignore[assignment]

from platform_api import (
    database as platform_database,
    database_privileges,
    database_privileges_behavior_v1,
    database_role_pre,
    platform_database_release_proof,
    process_secrets,
)
from platform_api.platform_secret_receipt import PlatformSecretIsolationContext


def _canary_password(label: str) -> str:
    return hashlib.sha512(
        ("synthetic-platform-" + label + "-canary-2026").encode()
    ).hexdigest()


def _valid_until(days: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


@pytest.mark.skipif(
    fcntl is None or not os.environ.get("PLATFORM_ROLE_PRE_POSTGRES_REJECT_HOST"),
    reason="requires Linux and a deliberately unqualified PostgreSQL canary",
)
def test_production_rejects_unqualified_pgaudit_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    host = os.environ["PLATFORM_ROLE_PRE_POSTGRES_REJECT_HOST"]
    database_url = (
        "postgresql+psycopg://platform_role_admin:"
        + _canary_password("role-admin")
        + f"@{host}:5432/postgres?"
        + urlencode(
            (
                ("sslmode", "verify-full"),
                ("sslrootcert", "/certs/ca.crt"),
            )
        )
    )
    engine = database_role_pre._engine(database_url)
    try:
        with engine.connect() as connection:
            with pytest.raises(database_role_pre.PlatformDatabaseRolePreError):
                database_role_pre._attest_database_system_surface(
                    connection,
                    require_empty_public=True,
                )
    finally:
        engine.dispose()


@pytest.mark.skipif(
    fcntl is None
    or not os.environ.get("PLATFORM_ROLE_PRE_POSTGRES_SESSION_PRELOAD_HOST"),
    reason="requires the PostgreSQL session-preload substitution canary",
)
def test_pgaudit_rejects_session_preload_as_a_shared_preload_substitute() -> None:
    host = os.environ["PLATFORM_ROLE_PRE_POSTGRES_SESSION_PRELOAD_HOST"]
    database_url = (
        "postgresql+psycopg://postgres:"
        + _canary_password("root")
        + f"@{host}:5432/postgres?"
        + urlencode(
            (
                ("sslmode", "verify-full"),
                ("sslrootcert", "/certs/ca.crt"),
            )
        )
    )
    engine = database_role_pre._engine(database_url)
    try:
        with pytest.raises(
            Exception,
            match="pgaudit must be loaded via shared_preload_libraries",
        ):
            with engine.connect():
                pytest.fail("session-preloaded pgAudit unexpectedly accepted a session")
    finally:
        engine.dispose()


def _restart_phase_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    database_role_pre.PlatformDatabaseRolePreSources,
    PlatformSecretIsolationContext,
    str,
]:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    host = os.environ["PLATFORM_ROLE_PRE_POSTGRES_TEST_HOST"]
    ca_path = "/certs/ca.crt"
    monkeypatch.setenv(process_secrets.PLATFORM_DATABASE_CA_FILE_ENV, ca_path)
    trusted_ca = Path(ca_path).read_bytes()
    process_secrets.validate_platform_database_ca(trusted_ca)
    source_url = (
        "postgresql+psycopg://platform_role_admin:"
        + _canary_password("role-admin")
        + f"@{host}:5432/postgres?"
        + urlencode(
            (("sslmode", "verify-full"), ("sslrootcert", ca_path))
        )
    )
    snapshot_path = process_secrets.materialize_verified_platform_database_ca(
        trusted_ca
    )
    actual_url = process_secrets.rewrite_platform_database_url_ca_path(
        source_url,
        snapshot_path=snapshot_path,
    )
    context = PlatformSecretIsolationContext(
        run_id="9" * 64,
        generation="root-proof-present",
        root_proof_id="8" * 64,
        platform_image=(
            "registry.example.invalid/ai-video/platform@sha256:" + "7" * 64
        ),
        platform_source_revision="6" * 40,
        platform_source_snapshot_sha256="sha256:" + "5" * 64,
    )
    endpoint = process_secrets._database_endpoint(
        source_url,
        database_override="platform_canary",
    )
    passwords = {
        process_role: _canary_password(process_role)
        for process_role in process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS
    }
    sources = database_role_pre.PlatformDatabaseRolePreSources(
        role_admin_database_url=actual_url,
        target_database_name="platform_canary",
        valid_until=_valid_until(120),
        legacy_owner=None,
        passwords=passwords,
        isolation_context=context,
        database_endpoint_sha256=hashlib.sha256(
            endpoint.encode("ascii")
        ).hexdigest(),
    )
    migration_url = database_role_pre._replace_principal(
        actual_url,
        database_name="platform_canary",
        username=process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS["migration"],
        password=passwords["migration"],
    )
    return sources, context, migration_url


@pytest.mark.skipif(
    fcntl is None
    or not os.environ.get("PLATFORM_ROLE_PRE_POSTGRES_TEST_HOST")
    or os.environ.get("PLATFORM_ROLE_PRE_POSTGRES_RESTART_PHASE")
    not in {"prepare", "reject", "refresh", "accept"},
    reason="requires the four-phase PostgreSQL restart proof canary",
)
def test_release_proof_rejects_postmaster_restart_until_role_pre_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase = os.environ["PLATFORM_ROLE_PRE_POSTGRES_RESTART_PHASE"]
    sources, context, migration_url = _restart_phase_sources(monkeypatch)
    if phase in {"prepare", "refresh"}:
        platform_database_release_proof.invalidate_platform_database_release_proof()
        database_role_pre.provision_platform_database_roles(sources)
        proof = platform_database_release_proof.parse_platform_database_release_proof(
            Path(
                os.environ[
                    platform_database_release_proof.PLATFORM_DATABASE_RELEASE_PROOF_ENV
                ]
            ).read_bytes()
        )
        assert proof.database_endpoint_sha256 == sources.database_endpoint_sha256
        return

    platform_database_release_proof.load_and_install_platform_database_release_proof(
        isolation=context,
        database_endpoint_sha256=sources.database_endpoint_sha256 or "",
    )
    engine = database_role_pre._engine(migration_url)
    try:
        with engine.connect() as connection:
            if phase == "reject":
                with pytest.raises(
                    database_privileges_behavior_v1.PlatformDatabaseAttestationError
                ):
                    database_privileges_behavior_v1.attest_platform_database_connection(
                        connection,
                        "migration",
                        require_runtime_acl=False,
                        require_head=False,
                    )
            else:
                database_privileges_behavior_v1.attest_platform_database_connection(
                    connection,
                    "migration",
                    require_runtime_acl=False,
                    require_head=False,
                )
    finally:
        engine.dispose()


@pytest.mark.skipif(
    fcntl is None or not os.environ.get("PLATFORM_ROLE_PRE_POSTGRES_TEST_HOST"),
    reason="requires Linux and the isolated synthetic PostgreSQL 16 TLS canary",
)
def test_role_pre_fresh_replay_renewal_and_sealed_ca_actual_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_release_environment = os.environ.get(
        "PLATFORM_ROLE_PRE_POSTGRES_TEST_ENVIRONMENT",
        "staging",
    )
    assert database_release_environment in {"staging", "production"}
    monkeypatch.setenv("ENVIRONMENT", database_release_environment)
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    host = os.environ["PLATFORM_ROLE_PRE_POSTGRES_TEST_HOST"]
    trusted_ca = Path("/certs/ca.crt").read_bytes()
    process_secrets.validate_platform_database_ca(trusted_ca)
    source_path = tmp_path / "source-ca.pem"
    source_path.write_bytes(trusted_ca)
    monkeypatch.setenv(
        process_secrets.PLATFORM_DATABASE_CA_FILE_ENV,
        str(source_path),
    )
    source_url = (
        "postgresql+psycopg://platform_role_admin:"
        + _canary_password("role-admin")
        + f"@{host}:5432/postgres?"
        + urlencode(
            (
                ("sslmode", "verify-full"),
                ("sslrootcert", str(source_path)),
            )
        )
    )
    snapshot_path = process_secrets.materialize_verified_platform_database_ca(
        trusted_ca
    )
    descriptor = int(snapshot_path.rsplit("/", 1)[1])
    expected_seals = (
        fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    )
    assert fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & expected_seals == expected_seals
    with pytest.raises(OSError):
        os.write(descriptor, b"must-not-change")

    actual_url = process_secrets.rewrite_platform_database_url_ca_path(
        source_url,
        snapshot_path=snapshot_path,
    )
    # Deterministic TOCTOU probe: the original source becomes unusable after
    # verification, while libpq continues over TLS using the sealed bytes.
    source_path.write_bytes(b"synthetic-invalid-replacement\n")
    rejected_engine = database_role_pre._engine(source_url)
    try:
        with pytest.raises(Exception):
            with rejected_engine.connect():
                pass
    finally:
        rejected_engine.dispose()
    actual_engine = database_role_pre._engine(actual_url)
    try:
        with actual_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT current_user,"
                    "COALESCE((SELECT ssl FROM pg_stat_ssl "
                    "WHERE pid=pg_backend_pid()),false)"
                )
            ).one()
            assert tuple(row) == ("platform_role_admin", True)
    finally:
        actual_engine.dispose()

    passwords = {
        process_role: _canary_password(process_role)
        for process_role in process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS
    }
    proof_directory = tmp_path / "release-proof"
    proof_directory.mkdir(mode=0o700)
    os.chmod(proof_directory, 0o700)
    proof_path = proof_directory / "attestation.json"
    monkeypatch.setenv(
        platform_database_release_proof.PLATFORM_DATABASE_RELEASE_PROOF_ENV,
        str(proof_path),
    )
    isolation_context = PlatformSecretIsolationContext(
        run_id="a" * 64,
        generation="root-proof-present",
        root_proof_id="b" * 64,
        platform_image=(
            "registry.example.invalid/ai-video/platform@sha256:" + "c" * 64
        ),
        platform_source_revision="d" * 40,
        platform_source_snapshot_sha256="sha256:" + "e" * 64,
    )
    endpoint = process_secrets._database_endpoint(
        source_url,
        database_override="platform_canary",
    )
    sources = database_role_pre.PlatformDatabaseRolePreSources(
        role_admin_database_url=actual_url,
        target_database_name="platform_canary",
        valid_until=_valid_until(30),
        legacy_owner=None,
        passwords=passwords,
        isolation_context=isolation_context,
        database_endpoint_sha256=hashlib.sha256(
            endpoint.encode("ascii")
        ).hexdigest(),
    )
    root_url = database_role_pre._replace_principal(
        actual_url,
        database_name="postgres",
        username="postgres",
        password=_canary_password("root"),
    )
    observed_activity: list[str] = []
    monitor_failures: list[str] = []
    monitor_stop = threading.Event()

    def monitor_role_admin_activity() -> None:
        monitor = database_role_pre._engine(root_url)
        try:
            with monitor.connect() as connection:
                while not monitor_stop.is_set():
                    observed_activity.extend(
                        str(query)
                        for query in connection.scalars(
                            text(
                                "SELECT query FROM pg_stat_activity "
                                "WHERE usename='platform_role_admin' "
                                "AND pid<>pg_backend_pid()"
                            )
                        )
                    )
                    connection.commit()
                    time.sleep(0.001)
        except Exception:
            monitor_failures.append("monitor failed")
        finally:
            monitor.dispose()

    monitor_thread = threading.Thread(target=monitor_role_admin_activity, daemon=True)
    monitor_thread.start()
    try:
        platform_database_release_proof.invalidate_platform_database_release_proof()
        database_role_pre.provision_platform_database_roles(sources)
        # Exact replay is supported, including password re-installation.
        platform_database_release_proof.invalidate_platform_database_release_proof()
        database_role_pre.provision_platform_database_roles(sources)
        renewed = replace(sources, valid_until=_valid_until(60))
        platform_database_release_proof.invalidate_platform_database_release_proof()
        database_role_pre.provision_platform_database_roles(renewed)
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=5)
    assert monitor_failures == []
    assert observed_activity
    assert not any("SCRAM-SHA-256$4096:" in query for query in observed_activity)
    assert not any(
        password in query
        for query in observed_activity
        for password in passwords.values()
    )
    platform_database_release_proof.invalidate_platform_database_release_proof()
    with pytest.raises(database_role_pre.PlatformDatabaseRolePreError):
        database_role_pre.provision_platform_database_roles(
            replace(renewed, valid_until=_valid_until(45))
        )
    assert not proof_path.exists()
    database_role_pre.provision_platform_database_roles(renewed)
    proof_raw = proof_path.read_bytes()
    committed_proof = (
        platform_database_release_proof.parse_platform_database_release_proof(
            proof_raw
        )
    )
    assert committed_proof.run_id == isolation_context.run_id
    assert (
        committed_proof.database_endpoint_sha256
        == sources.database_endpoint_sha256
    )
    monkeypatch.setattr(
        platform_database_release_proof,
        "read_protected_platform_database_release_proof",
        lambda: proof_raw,
    )
    platform_database_release_proof.load_and_install_platform_database_release_proof(
        isolation=isolation_context,
        database_endpoint_sha256=sources.database_endpoint_sha256 or "",
    )

    # Database-wide settings use pg_db_role_setting.setrole=0 and used to
    # evade the role-specific inventory. A new physical migration connection
    # must reject unsafe bind logging, then recover only after the exact reset.
    migration_url = database_role_pre._replace_principal(
        actual_url,
        database_name=sources.target_database_name,
        username=process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS["migration"],
        password=passwords["migration"],
    )
    root_engine = database_role_pre._engine(root_url)
    try:
        for setting, unsafe_value in (
            ("log_parameter_max_length", "64"),
            ("log_parameter_max_length_on_error", "64"),
            ("auto_explain.log_parameter_max_length", "64"),
            ("pgaudit.log_parameter", "on"),
        ):
            with root_engine.begin() as root:
                root.execute(
                    text(
                        f"ALTER DATABASE platform_canary SET {setting}="
                        f"'{unsafe_value}'"
                    )
                )
            try:
                drifted_engine = database_role_pre._engine(migration_url)
                try:
                    with drifted_engine.connect() as connection:
                        evidence = database_privileges_behavior_v1.collect_platform_database_evidence(
                            connection
                        )
                        assert evidence.credential_logging_policy_exact is False
                        with pytest.raises(
                            database_privileges_behavior_v1.PlatformDatabaseAttestationError
                        ):
                            database_privileges_behavior_v1.validate_platform_database_evidence(
                                evidence,
                                "migration",
                                require_runtime_acl=False,
                                require_head=False,
                            )
                finally:
                    drifted_engine.dispose()
            finally:
                with root_engine.begin() as root:
                    root.execute(
                        text(f"ALTER DATABASE platform_canary RESET {setting}")
                    )
            restored_engine = database_role_pre._engine(migration_url)
            try:
                with restored_engine.connect() as connection:
                    evidence = database_privileges_behavior_v1.collect_platform_database_evidence(
                        connection
                    )
                    assert evidence.credential_logging_policy_exact is True
                    database_privileges_behavior_v1.validate_platform_database_evidence(
                        evidence,
                        "migration",
                        require_runtime_acl=False,
                        require_head=False,
                    )
            finally:
                restored_engine.dispose()
    finally:
        root_engine.dispose()

    # The committed proof is checked on the very same physical migration
    # connection as the runtime principal/catalog policy. A database-level
    # session preload of a different loadable module is rejected even though
    # the privileged shared_preload setting is hidden from this role.
    migration_engine = database_role_pre._engine(migration_url)
    try:
        with migration_engine.connect() as connection:
            database_privileges_behavior_v1.attest_platform_database_connection(
                connection,
                "migration",
                require_runtime_acl=False,
                require_head=False,
            )
    finally:
        migration_engine.dispose()
    root_engine = database_role_pre._engine(root_url)
    try:
        with root_engine.begin() as root:
            root.execute(
                text(
                    "ALTER DATABASE platform_canary SET "
                    "session_preload_libraries='auto_explain'"
                )
            )
        drifted_engine = database_role_pre._engine(migration_url)
        try:
            with drifted_engine.connect() as connection:
                evidence = (
                    database_privileges_behavior_v1.collect_platform_database_evidence(
                        connection
                    )
                )
                assert evidence.role_setting_count > 0
                with pytest.raises(
                    database_privileges_behavior_v1.PlatformDatabaseAttestationError
                ):
                    database_privileges_behavior_v1.attest_platform_database_connection(
                        connection,
                        "migration",
                        require_runtime_acl=False,
                        require_head=False,
                    )
        finally:
            drifted_engine.dispose()
        with root_engine.begin() as root:
            root.execute(
                text(
                    "ALTER DATABASE platform_canary RESET "
                    "session_preload_libraries"
                )
            )
        restored_engine = database_role_pre._engine(migration_url)
        try:
            with restored_engine.connect() as connection:
                database_privileges_behavior_v1.attest_platform_database_connection(
                    connection,
                    "migration",
                    require_runtime_acl=False,
                    require_head=False,
                )
        finally:
            restored_engine.dispose()
    finally:
        root_engine.dispose()

    # A server-level session preload change and SIGHUP changes the privileged
    # epoch. The old proof fails, and role-pre itself refuses to sign the new
    # epoch while the extra module is active.
    monkeypatch.setenv(process_secrets.PLATFORM_PROCESS_ROLE_ENV, "migration")
    runtime_engine = platform_database.build_engine(migration_url)
    with runtime_engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    runtime_engine.dispose()

    def alter_system_and_reload(statement: str) -> None:
        root_engine = database_role_pre._engine(root_url)
        try:
            with root_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as root:
                root.exec_driver_sql(statement)
                assert root.scalar(text("SELECT pg_reload_conf()")) is True
        finally:
            root_engine.dispose()

    alter_system_and_reload(
        "ALTER SYSTEM SET session_preload_libraries='auto_explain'"
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        clock_engine = database_role_pre._engine(migration_url)
        try:
            with clock_engine.connect() as connection:
                _, config_load_time = (
                    platform_database_release_proof.platform_database_clock_identity(
                        connection
                    )
                )
            if config_load_time != committed_proof.config_load_time:
                break
        finally:
            clock_engine.dispose()
        time.sleep(0.05)
    assert config_load_time != committed_proof.config_load_time
    runtime_engine.dispose()
    business_sql: list[str] = []
    with pytest.raises(database_privileges.PlatformDatabaseAttestationError):
        with runtime_engine.connect() as connection:
            business_sql.append("reached")
            connection.execute(text("SELECT 1"))
    assert business_sql == []
    runtime_engine.dispose()
    drifted_engine = database_role_pre._engine(migration_url)
    try:
        with drifted_engine.connect() as connection:
            with pytest.raises(
                database_privileges_behavior_v1.PlatformDatabaseAttestationError
            ):
                database_privileges_behavior_v1.attest_platform_database_connection(
                    connection,
                    "migration",
                    require_runtime_acl=False,
                    require_head=False,
                )
    finally:
        drifted_engine.dispose()
    platform_database_release_proof.invalidate_platform_database_release_proof()
    with pytest.raises(database_role_pre.PlatformDatabaseRolePreError):
        database_role_pre.provision_platform_database_roles(renewed)
    assert not proof_path.exists()

    alter_system_and_reload("ALTER SYSTEM RESET session_preload_libraries")
    reset_deadline = time.monotonic() + 5
    while time.monotonic() < reset_deadline:
        clock_engine = database_role_pre._engine(migration_url)
        try:
            with clock_engine.connect() as connection:
                _, reset_config_load_time = (
                    platform_database_release_proof.platform_database_clock_identity(
                        connection
                    )
                )
            if reset_config_load_time != config_load_time:
                break
        finally:
            clock_engine.dispose()
        time.sleep(0.05)
    assert reset_config_load_time != config_load_time
    database_role_pre.provision_platform_database_roles(renewed)
    refreshed_raw = proof_path.read_bytes()
    # Re-signing the DB proof does not mutate an old process's installed
    # immutable proof. Its next forced physical checkout remains closed.
    runtime_engine.dispose()
    with pytest.raises(database_privileges.PlatformDatabaseAttestationError):
        with runtime_engine.connect():
            pytest.fail("old process accepted a refreshed proof implicitly")
    runtime_engine.dispose()
    monkeypatch.setattr(
        platform_database_release_proof,
        "read_protected_platform_database_release_proof",
        lambda: refreshed_raw,
    )
    monkeypatch.setattr(platform_database_release_proof, "_installed_proof", None)
    platform_database_release_proof.load_and_install_platform_database_release_proof(
        isolation=isolation_context,
        database_endpoint_sha256=sources.database_endpoint_sha256 or "",
    )
    refreshed_engine = platform_database.build_engine(migration_url)
    try:
        with refreshed_engine.connect() as connection:
            database_privileges_behavior_v1.attest_platform_database_connection(
                connection,
                "migration",
                require_runtime_acl=False,
                require_head=False,
            )
    finally:
        refreshed_engine.dispose()
        runtime_engine.dispose()

    # A second deploy cannot pass the session-wide gate or perform any
    # preflight/mutation while the first generation holds it.
    with database_role_pre._held_provision_lock(actual_url):
        started = time.monotonic()
        with pytest.raises(database_role_pre.PlatformDatabaseRolePreError):
            with database_role_pre._held_provision_lock(actual_url):
                pytest.fail("concurrent role-pre unexpectedly acquired the lock")
        assert time.monotonic() - started < 5

    # Reproduce a non-cooperating DBA mutation exactly between the initial
    # preflight and the first role DDL.  The adjacent same-transaction gate
    # must reject it before the event trigger observes any DDL.
    with database_role_pre._held_provision_lock(actual_url) as lock_connection:
        state = database_role_pre._preflight(renewed, lock_connection)
        root_engine = database_role_pre._engine(root_url)
        try:
            with root_engine.begin() as root:
                root.execute(text("CREATE SCHEMA synthetic_role_pre_attack"))
                root.execute(
                    text(
                        "CREATE TABLE synthetic_role_pre_attack.counter(value integer NOT NULL)"
                    )
                )
                root.execute(
                    text("INSERT INTO synthetic_role_pre_attack.counter VALUES (0)")
                )
                root.execute(
                    text(
                        "CREATE FUNCTION public.synthetic_role_pre_event() "
                        "RETURNS event_trigger LANGUAGE plpgsql AS $$BEGIN "
                        "UPDATE synthetic_role_pre_attack.counter SET value=value+1; "
                        "END$$"
                    )
                )
                root.execute(
                    text(
                        "CREATE EVENT TRIGGER synthetic_role_pre_event "
                        "ON ddl_command_start EXECUTE FUNCTION "
                        "public.synthetic_role_pre_event()"
                    )
                )
            with pytest.raises(database_role_pre.PlatformDatabaseRolePreError):
                database_role_pre._provision_principals(
                    renewed,
                    state,
                    lock_connection,
                )
            with root_engine.connect() as root:
                assert (
                    root.scalar(
                        text("SELECT value FROM synthetic_role_pre_attack.counter")
                    )
                    == 0
                )
                root.commit()
            with root_engine.begin() as root:
                root.execute(text("DROP EVENT TRIGGER synthetic_role_pre_event"))
                root.execute(text("DROP FUNCTION public.synthetic_role_pre_event()"))
                root.execute(text("DROP SCHEMA synthetic_role_pre_attack CASCADE"))
        finally:
            root_engine.dispose()

    # A non-cooperating catalog-row lock has a fixed deadline and cannot leave
    # a partial database-ACL transition behind.  A row lock models the DBA
    # update race without taking an AccessExclusive lock on the shared system
    # catalog itself (which is unsafe fault injection with pgAudit hooks).
    root_engine = database_role_pre._engine(root_url)
    try:
        with database_role_pre._held_provision_lock(actual_url) as lock_connection:
            with root_engine.connect() as root:
                before_acl = tuple(
                    root.execute(
                        text(
                            "SELECT datname,COALESCE(datacl::text,'') FROM pg_database "
                            "ORDER BY datname"
                        )
                    ).all()
                )
                root.commit()
                transaction = root.begin()
                root.execute(
                    text(
                        "SELECT oid FROM pg_database WHERE datname='postgres' "
                        "FOR UPDATE"
                    )
                )
                started = time.monotonic()
                with pytest.raises(database_role_pre.PlatformDatabaseRolePreError):
                    database_role_pre._normalize_cluster_database_acl(
                        renewed,
                        lock_connection,
                    )
                assert time.monotonic() - started < 10
                transaction.rollback()
                after_acl = tuple(
                    root.execute(
                        text(
                            "SELECT datname,COALESCE(datacl::text,'') FROM pg_database "
                            "ORDER BY datname"
                        )
                    ).all()
                )
                assert after_acl == before_acl
    finally:
        root_engine.dispose()

    maintenance = database_role_pre._engine(actual_url)
    try:
        with maintenance.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM pg_roles WHERE rolname="
                    "ANY(CAST(:roles AS text[]))"
                ),
                {
                    "roles": list(
                        process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS.values()
                    )
                },
            ) == len(process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS)
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_database WHERE datname='platform_canary'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT bool_and(rolvaliduntil=CAST(:valid_until AS timestamptz)) "
                        "FROM pg_roles WHERE rolname=ANY(CAST(:roles AS text[]))"
                    ),
                    {
                        "valid_until": renewed.valid_until,
                        "roles": list(
                            process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS.values()
                        ),
                    },
                )
                is True
            )
    finally:
        maintenance.dispose()
