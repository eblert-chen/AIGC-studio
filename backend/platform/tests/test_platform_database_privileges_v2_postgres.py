from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

from platform_api import database_privileges_behavior_v1 as behavior_v1
from platform_api import database_privileges_behavior_v4 as behavior_v4
from platform_api import database_privileges_v1 as policy_v1
from platform_api import database_privileges_v4 as policy_v4
from platform_api import platform_database_release_proof as release_proof
from platform_api.platform_secret_receipt import PlatformSecretIsolationContext


TEST_URL_ENV = "PLATFORM_DATABASE_V2_ACL_TEST_URL"
ROLE_ADMIN_TEST_URL_ENV = "PLATFORM_DATABASE_V2_ROLE_ADMIN_TEST_URL"

_RUNTIME_PASSWORD_BY_PROCESS = MappingProxyType(
    {
        "platform-api": "test-api-password",
        "dispatcher": "test-dispatcher-password",
        "relay-sync": "test-relay-password",
        "timeout-worker": "test-timeout-password",
        "publishing-worker": "test-publishing-password",
        "download-gateway-registration-worker": "test-download-password",
    }
)


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0037_production_auth_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location(
        "production_auth_lifecycle_0037_postgres_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database_role_migration_module():
    path = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0036_platform_database_roles.py"
    )
    spec = importlib.util.spec_from_file_location(
        "platform_database_roles_0036_postgres_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _role_url(database_url: str, username: str, password: str) -> str:
    return make_url(database_url).set(
        username=username,
        password=password,
    ).render_as_string(hide_password=False)


@pytest.mark.skipif(
    not os.getenv(TEST_URL_ENV) or not os.getenv(ROLE_ADMIN_TEST_URL_ENV),
    reason="requires one dedicated PostgreSQL 16 role-admin cluster",
)
def test_same_cluster_consecutive_fresh_databases_have_stable_v1_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove 0035/0036 catalog hashes do not depend on generated object OIDs."""

    migration_template = make_url(os.environ[TEST_URL_ENV])
    maintenance_url = make_url(os.environ[ROLE_ADMIN_TEST_URL_ENV]).set(
        database="postgres"
    )
    suffix = uuid4().hex[:12]
    database_names = tuple(
        f"platform_hash_{index}_{suffix}" for index in range(2)
    )
    config_path = Path(__file__).parents[1] / "alembic.ini"
    migration_0036 = _database_role_migration_module()
    maintenance_engine = create_engine(
        maintenance_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    created: list[str] = []
    observed: list[tuple[str, str, str]] = []
    try:
        for database_name in database_names:
            assert database_name.replace("_", "").isalnum()
            with maintenance_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'CREATE DATABASE "{database_name}" '
                    "OWNER platform_migration TEMPLATE template0"
                )
            created.append(database_name)
            database_url = migration_template.set(database=database_name).render_as_string(
                hide_password=False
            )
            monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "false")
            monkeypatch.setenv("DATABASE_URL", database_url)
            command.upgrade(Config(str(config_path)), "0035_operations_evidence")

            # The protected migration principal is deliberately limited to one
            # connection. Never leave an idle pooled session while Alembic
            # opens its independently attested migration connection.
            engine = create_engine(database_url, poolclass=NullPool)
            try:
                with engine.connect() as connection:
                    source_hash = behavior_v1.platform_catalog_sha256(connection)

                # An unprotected invocation advances the historical head but
                # deliberately skips the protected ACL mutation. Apply that
                # exact frozen mutation through Alembic's Operations surface
                # so this regression can qualify only catalog determinism.
                command.upgrade(Config(str(config_path)), "0036_platform_db_roles")
                with engine.begin() as connection:
                    migration_0036.op = Operations(
                        MigrationContext.configure(connection)
                    )
                    migration_0036._apply_runtime_acl()
                with engine.connect() as connection:
                    head = str(
                        connection.scalar(text("SELECT version_num FROM alembic_version"))
                    )
                    catalog_hash = behavior_v1.platform_catalog_sha256(connection)
                observed.append((source_hash, head, catalog_hash))
            finally:
                engine.dispose()
    finally:
        with maintenance_engine.connect() as connection:
            for database_name in reversed(created):
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:database_name AND pid<>pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        maintenance_engine.dispose()

    assert observed == [
        (
            policy_v1.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
                "0035_operations_evidence"
            ],
            policy_v1.ALEMBIC_HEAD,
            policy_v1.CATALOG_SHA256,
        ),
        (
            policy_v1.MIGRATION_SOURCE_CATALOG_SHA256_BY_HEAD[
                "0035_operations_evidence"
            ],
            policy_v1.ALEMBIC_HEAD,
            policy_v1.CATALOG_SHA256,
        ),
    ]


@pytest.mark.skipif(
    not os.getenv(TEST_URL_ENV),
    reason="requires a dedicated PostgreSQL 16 v4 ACL database",
)
def test_postgres16_v4_acl_and_auth_history_guards_are_exact() -> None:
    database_url = os.environ[TEST_URL_ENV]
    database_name = make_url(database_url).database or ""
    if not any(marker in database_name for marker in ("canary", "acl_exact")):
        pytest.skip("v4 ACL mutation requires an explicit canary database")

    migration_engine = create_engine(database_url)
    migration = _migration_module()
    try:
        with migration_engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration._apply_runtime_acl()

        with migration_engine.connect() as connection:
            evidence = behavior_v4.collect_platform_database_evidence(
                connection,
                policy=policy_v4,
            )
            behavior_v4.validate_platform_database_acl_evidence(
                evidence,
                require_head=True,
                policy=policy_v4,
            )
            constraints = {
                str(row[0]): (bool(row[1]), str(row[2]))
                for row in connection.execute(
                    text(
                        "SELECT con.conname,con.convalidated,"
                        "pg_get_constraintdef(con.oid,true) "
                        "FROM pg_constraint con JOIN pg_class c "
                        "ON c.oid=con.conrelid WHERE c.relname IN "
                        "('download_completions','download_records',"
                        "'personal_download_records')"
                    )
                )
            }
            assert "ck_download_completion_new_rows_verified" not in constraints
            assert constraints["ck_download_completion_verified_source"][0] is False
            assert "verification_version IS NOT NULL" in constraints[
                "ck_download_completion_verified_source"
            ][1]
            for constraint_name in (
                "ck_download_source_url_sha256_hex",
                "ck_download_gateway_ticket_url_sha256_hex",
                "ck_personal_download_source_url_sha_hex",
            ):
                assert constraints[constraint_name][0] is True
                assert "[0-9a-f]{64}" in constraints[constraint_name][1]
            assert (
                "ck_personal_download_source_url_sha_shape" not in constraints
            )

        api_engine = create_engine(
            _role_url(database_url, "platform_api", "test-api-password")
        )
        try:
            with api_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id,email,display_name,is_platform_admin,status,auth_version,"
                        "created_at,updated_at) VALUES "
                        "('acl-user','acl-user@example.com','ACL User',false,'ACTIVE',1,"
                        "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO companies (id,name,status,created_at,updated_at) "
                        "VALUES ('acl-company','ACL Company','ACTIVE',"
                        "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO external_identities "
                        "(id,user_id,issuer,subject,email_at_link,created_at,updated_at) "
                        "VALUES ('acl-identity','acl-user','https://idp.example/','subject-1',"
                        "'acl-user@example.com',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO account_security_events "
                        "(id,user_id,event_type,outcome,request_id,issuer,subject_hash,"
                        "user_agent,details,created_at) VALUES "
                        "('acl-event','acl-user','login','SUCCEEDED','request-acl',"
                        "'https://idp.example/',:subject_hash,'ua','{}',CURRENT_TIMESTAMP)"
                    ),
                    {"subject_hash": "a" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO company_invitations "
                        "(id,token_digest,company_id,email,display_name,primary_role,status,"
                        "expires_at,created_by_user_id,idempotency_key,request_fingerprint,"
                        "created_at,updated_at) VALUES "
                        "('acl-invite',:invite_hash,'acl-company','invite@example.com',"
                        "'Invite','operator','PENDING',CURRENT_TIMESTAMP + interval '1 day',"
                        "'acl-user','acl-key',:fingerprint,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                    ),
                    {"invite_hash": "b" * 64, "fingerprint": "c" * 64},
                )

            with pytest.raises(IntegrityError):
                with api_engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO auth_sessions "
                            "(id,token_digest,csrf_digest,user_id,external_identity_id,"
                            "auth_version,amr,auth_time,created_at,last_seen_at,expires_at,"
                            "user_agent) VALUES ('acl-null-session',:token,:csrf,'acl-user',"
                            "NULL,1,'[]',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP + interval '1 day','ua')"
                        ),
                        {"token": "d" * 64, "csrf": "e" * 64},
                    )

            with api_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO auth_sessions "
                        "(id,token_digest,csrf_digest,user_id,external_identity_id,"
                        "auth_version,amr,auth_time,created_at,last_seen_at,expires_at,"
                        "user_agent) VALUES ('acl-session',:token,:csrf,'acl-user',"
                        "'acl-identity',1,'[\"webauthn\"]',NULL,"
                        "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                        "CURRENT_TIMESTAMP + interval '1 day','ua')"
                    ),
                    {"token": "f" * 64, "csrf": "0" * 64},
                )
                connection.execute(
                    text(
                        "INSERT INTO oidc_login_transactions "
                        "(id,state_digest,nonce,code_verifier,return_to,created_at,expires_at,"
                        "ip_hash) VALUES ('acl-login',:state,'nonce','verifier','/',"
                        "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP + interval '5 minutes',:ip)"
                    ),
                    {"state": "1" * 64, "ip": "2" * 64},
                )
                connection.execute(
                    text(
                        "UPDATE company_invitations SET status='REVOKED',"
                        "revoked_at=CURRENT_TIMESTAMP WHERE id='acl-invite'"
                    )
                )
                connection.execute(
                    text("DELETE FROM oidc_login_transactions WHERE id='acl-login'")
                )

            with migration_engine.connect() as connection:
                for table_name in (
                    "account_security_events",
                    "auth_sessions",
                    "company_invitations",
                    "external_identities",
                    "users",
                ):
                    assert not connection.scalar(
                        text(
                            "SELECT has_table_privilege("
                            "'platform_api', :table_name, 'DELETE')"
                        ),
                        {"table_name": table_name},
                    )
                assert connection.scalar(
                    text(
                        "SELECT has_table_privilege("
                        "'platform_api', 'oidc_login_transactions', 'DELETE')"
                    )
                )

            for statement in (
                "UPDATE account_security_events SET event_type='changed' "
                "WHERE id='acl-event'",
                "DELETE FROM account_security_events WHERE id='acl-event'",
                "DELETE FROM auth_sessions WHERE id='missing-session'",
                "DELETE FROM company_invitations WHERE id='acl-invite'",
                "DELETE FROM external_identities WHERE id='missing-identity'",
                "DELETE FROM users WHERE id='missing-user'",
            ):
                with pytest.raises(DBAPIError):
                    with api_engine.begin() as connection:
                        connection.execute(text(statement))
        finally:
            api_engine.dispose()

        worker_passwords = {
            policy_v4.DATABASE_ROLE_BY_PROCESS[process_role]: password
            for process_role, password in _RUNTIME_PASSWORD_BY_PROCESS.items()
            if process_role != "platform-api"
        }
        for username, password in worker_passwords.items():
            worker_engine = create_engine(_role_url(database_url, username, password))
            try:
                for table_name in (
                    "account_security_events",
                    "auth_sessions",
                    "company_invitations",
                    "external_identities",
                    "oidc_login_transactions",
                ):
                    with pytest.raises(DBAPIError):
                        with worker_engine.connect() as connection:
                            connection.execute(
                                text(f'SELECT count(*) FROM "{table_name}"')
                            )
            finally:
                worker_engine.dispose()

        for table_name in (
            "account_security_events",
            "company_invitations",
        ):
            with pytest.raises(DBAPIError):
                with migration_engine.begin() as connection:
                    connection.execute(text(f'TRUNCATE TABLE "{table_name}"'))
    finally:
        migration_engine.dispose()


@pytest.mark.skipif(
    not os.getenv(TEST_URL_ENV) or not os.getenv(ROLE_ADMIN_TEST_URL_ENV),
    reason="requires PostgreSQL 16 runtime and role-admin TLS URLs",
)
def test_postgres16_each_process_proves_identity_tls_pgaudit_acl_and_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_url = os.environ[TEST_URL_ENV]
    role_admin_url = os.environ[ROLE_ADMIN_TEST_URL_ENV]
    database_name = make_url(migration_url).database or ""
    if not any(marker in database_name for marker in ("canary", "acl_exact")):
        pytest.skip("full attestation requires an explicit canary database")

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    admin_engine = create_engine(role_admin_url)
    try:
        with admin_engine.connect() as connection:
            evidence = behavior_v4.collect_platform_database_evidence(
                connection,
                policy=policy_v4,
            )
            endpoint_sha256 = (
                release_proof.platform_database_connection_endpoint_sha256(
                    connection
                )
            )
            proof = release_proof.build_platform_database_release_proof(
                connection,
                environment="production",
                isolation=PlatformSecretIsolationContext(
                    run_id="a" * 64,
                    generation="root-proof-present",
                    root_proof_id="b" * 64,
                    platform_image=(
                        "registry.example.invalid/platform@sha256:" + "c" * 64
                    ),
                    platform_source_revision="d" * 40,
                    platform_source_snapshot_sha256="sha256:" + "e" * 64,
                ),
                database_endpoint_sha256=endpoint_sha256,
                evidence=evidence,
            )
    finally:
        admin_engine.dispose()

    with release_proof._installed_lock:
        previous_proof = release_proof._installed_proof
        release_proof._installed_proof = proof
    try:
        role_urls = {
            "migration": migration_url,
            **{
                process_role: _role_url(
                    migration_url,
                    policy_v4.DATABASE_ROLE_BY_PROCESS[process_role],
                    password,
                )
                for process_role, password in _RUNTIME_PASSWORD_BY_PROCESS.items()
            },
        }
        observed: dict[str, tuple[str, str, bool, str, str]] = {}
        for process_role, database_url in role_urls.items():
            engine = create_engine(database_url)
            try:
                with engine.connect() as connection:
                    behavior_v4.attest_platform_database_connection(
                        connection,
                        process_role,
                        require_runtime_acl=process_role != "migration",
                        policy=policy_v4,
                    )
                    evidence = behavior_v4.collect_platform_database_evidence(
                        connection,
                        policy=policy_v4,
                    )
                    observed[process_role] = (
                        evidence.current_user,
                        evidence.session_user,
                        evidence.ssl_active,
                        evidence.system_semantic_sha256,
                        evidence.catalog_sha256,
                    )
            finally:
                engine.dispose()
    finally:
        with release_proof._installed_lock:
            release_proof._installed_proof = previous_proof

    assert set(observed) == set(policy_v4.DATABASE_ROLE_BY_PROCESS)
    for process_role, values in observed.items():
        assert values == (
            policy_v4.DATABASE_ROLE_BY_PROCESS[process_role],
            policy_v4.DATABASE_ROLE_BY_PROCESS[process_role],
            True,
            "sha256:f97e2f23386ec637defd1cf62f84def8cd76198bfd9e784a1646d1942215b12a",
            policy_v4.CATALOG_SHA256,
        )
