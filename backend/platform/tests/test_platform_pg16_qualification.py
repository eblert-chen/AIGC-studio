"""Opt-in PG16 reference evidence, explicitly not protected release authority.

The runner owns and destroys the entire labelled Docker cluster. These tests
never repair policy hashes, monkeypatch a catalog validator, or publish proof.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from platform_api import database_privileges_v22 as previous_policy
from platform_api import database_privileges_v23 as policy
from platform_api import database_privileges_behavior_v23 as behavior
from platform_api import database_role_pre
from platform_api import platform_database_release_proof as proof
from platform_api.database_system_semantic_v1 import (
    PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST,
    POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256,
)
from platform_api.platform_secret_receipt import PlatformSecretIsolationContext


@pytest.fixture
def runner():
    source = Path(__file__).resolve().parents[3] / "scripts" / "qualify-platform-pg16-local.py"
    spec = importlib.util.spec_from_file_location("platform_pg16_reference_runner_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_errors_do_not_echo_command_credentials(runner, monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="test-secret", stderr="password=test-secret"))
    with pytest.raises(RuntimeError) as caught:
        runner.command(["db-client", "password=test-secret"])
    assert "test-secret" not in str(caught.value)


def test_runner_cleanup_rejects_wrong_resource_owner(runner, monkeypatch):
    monkeypatch.setattr(runner, "command", lambda *a, **k: json.dumps([
        {"Config": {"Labels": {runner.LABEL: "another-run"}}}]))
    with pytest.raises(RuntimeError, match="unowned Docker resource"):
        runner.inspect_owned("container", "not-ours", "this-run")


def test_runner_failed_image_preflight_removes_ephemeral_credentials(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "create_tls", lambda path: "a" * 64)
    def command(arguments, **kwargs):
        if arguments[:3] == ["docker", "image", "inspect"]:
            raise RuntimeError("test image unavailable")
        assert arguments[1:3] in (["container", "ls"], ["volume", "ls"])
        return ""
    monkeypatch.setattr(runner, "command", command)
    evidence = {}
    with pytest.raises(RuntimeError, match="test image unavailable"):
        with runner.isolated_cluster(tmp_path, "f" * 32, evidence):
            pytest.fail("missing image must not produce a database URL")
    assert not (tmp_path / "ephemeral-secrets").exists()
    assert evidence["cleanup"]["ephemeral_credentials_removed"]


def test_reference_runner_targets_v23_without_requalifying_v22_or_widening_acl(runner):
    assert runner.EXPECTED_HEAD == policy.ALEMBIC_HEAD == "0058_personal_limits"
    assert runner.PREVIOUS_HEAD == previous_policy.ALEMBIC_HEAD == "0057_execution_integrity"
    assert "test_personal_retail_limits_migration.py" in runner.TESTS
    assert policy.CATALOG_SHA256 == previous_policy.CATALOG_SHA256 == "0" * 64
    assert policy.TABLES == previous_policy.TABLES
    assert policy.PRIVILEGES_BY_PROCESS == previous_policy.PRIVILEGES_BY_PROCESS


@pytest.fixture(scope="module")
def reference():
    raw = os.environ.get("PLATFORM_PG16_REFERENCE_URL")
    if not raw:
        pytest.skip("requires the isolated qualify-platform-pg16-local.py runner")
    url = make_url(raw)
    assert url.host == "127.0.0.1"
    assert url.database == "platform_pg16_reference"
    assert url.query["sslmode"] == "verify-full"
    passwords = json.loads(os.environ["PLATFORM_PG16_REFERENCE_PASSWORDS"])
    assert set(passwords) == set(policy.DATABASE_ROLE_BY_PROCESS.values())
    return url, passwords


def _engine(reference, process="migration"):
    url, passwords = reference
    role = policy.DATABASE_ROLE_BY_PROCESS[process]
    return create_engine(url.set(username=role, password=passwords[role]),
                         poolclass=NullPool, hide_parameters=True)


def _evidence(reference):
    engine = _engine(reference)
    try:
        with engine.connect() as connection:
            return behavior.collect_platform_database_evidence(connection)
    finally:
        engine.dispose()


def test_reference_pg16_verified_tls_pgaudit_and_real_system_semantics(reference, monkeypatch):
    engine = create_engine(reference[0], poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert 160000 <= int(connection.scalar(text("SHOW server_version_num"))) < 170000
            assert connection.scalar(text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"))
            assert connection.scalar(text("SHOW shared_preload_libraries")) == PLATFORM_POSTGRES16_PRODUCTION_SHARED_PRELOAD_MANIFEST
            # Invoke the real role-pre's read-only PG16/system/TLS check. It is
            # only a component check: no protected secret loading or role writes.
            monkeypatch.setenv("ENVIRONMENT", "production")
            connection.exec_driver_sql("SET lock_timeout='5s'")
            database_role_pre._attest_database_system_surface(connection, require_empty_public=False)
            database_role_pre._attest_logging_boundary(connection)
    finally:
        engine.dispose()
    evidence = _evidence(reference)
    assert evidence.system_semantic_sha256 == POSTGRES16_DEBIAN_PGAUDIT_SYSTEM_SEMANTIC_SHA256
    assert evidence.system_extension_surface_exact
    assert evidence.pgaudit_preloaded and evidence.pgaudit_log_class_coverage
    assert evidence.credential_logging_policy_exact
    assert evidence.system_acl_sha256 == policy.POSTGRES16_SYSTEM_ACL_BY_SYSTEM_SEMANTIC_SHA256[evidence.system_semantic_sha256]


@pytest.mark.parametrize("attack", ["plaintext", "wrong_hostname", "wrong_password"])
def test_reference_rejects_plaintext_bad_certificate_identity_and_bad_password(reference, attack):
    url, _ = reference
    if attack == "plaintext":
        url = url.update_query_dict({"sslmode": "disable"})
        match = "no pg_hba.conf entry"
    elif attack == "wrong_hostname":
        url = url.set(host="wrong-hostname.invalid").update_query_dict({"hostaddr": "127.0.0.1"})
        match = "does not match host name"
    else:
        url = url.set(password="definitely-not-the-generated-credential")
        match = "password authentication failed"
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match=match):
            with engine.connect():
                pytest.fail("invalid transport/identity must never connect")
    finally:
        engine.dispose()


def test_reference_current_manifest_acl_exact_without_qualifying_catalog(reference):
    evidence = _evidence(reference)
    assert evidence.alembic_heads == (policy.ALEMBIC_HEAD,)
    assert evidence.table_names == policy.TABLES | {"alembic_version"}
    assert frozenset((r, p) for r, p, _, _ in evidence.database_acl) == policy.EXPECTED_DATABASE_ACL
    assert frozenset((r, p) for r, p, _, _ in evidence.schema_acl) == policy.EXPECTED_SCHEMA_ACL
    assert frozenset((t, r, p) for t, r, p, _, _ in evidence.table_acl) == policy.EXPECTED_TABLE_ACL
    assert evidence.default_acl == policy.EXPECTED_DEFAULT_ACL
    assert not evidence.sequence_acl and not evidence.routine_acl
    assert not evidence.membership_count and not evidence.role_setting_count
    assert not evidence.parameter_acl_count and not evidence.column_acl_count
    assert not evidence.foreign_owned_object_count and not evidence.public_unsafe_object_count
    for _, _, grantor, grantable in evidence.database_acl:
        assert grantor == policy.MIGRATION_DATABASE_ROLE and not grantable
    for _, _, _, grantor, grantable in evidence.table_acl:
        assert grantor == policy.MIGRATION_DATABASE_ROLE and not grantable
    principals = {item.role_name: item for item in evidence.principals}
    assert set(principals) == set(policy.DATABASE_ROLE_BY_PROCESS.values())
    for process, name in policy.DATABASE_ROLE_BY_PROCESS.items():
        principal = principals[name]
        assert principal.can_login and principal.credential_validity_ok
        assert principal.role_comment == policy.DATABASE_ROLE_COMMENT_BY_PROCESS[process]
        assert principal.connection_limit == policy.DATABASE_ROLE_CONNECTION_LIMIT_BY_PROCESS[process]
        assert not any((principal.is_superuser, principal.inherits, principal.can_create_role,
                        principal.can_create_database, principal.can_replicate, principal.bypasses_rls))


@pytest.mark.parametrize("process", sorted(set(policy.DATABASE_ROLE_BY_PROCESS) - {"migration"}))
def test_each_real_runtime_login_has_only_manifest_grants_and_cannot_mutate_schema(reference, process):
    engine = _engine(reference, process)
    role = policy.DATABASE_ROLE_BY_PROCESS[process]
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_user")) == role
            assert connection.scalar(text("SELECT session_user")) == role
            assert connection.scalar(text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"))
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == policy.ALEMBIC_HEAD
            # Query effective permissions, not just the explicit GRANT list:
            # PUBLIC grants or role inheritance would also be caught here.
            grants = connection.execute(text(
                "SELECT c.relname,p.privilege,has_table_privilege(current_user,c.oid,p.privilege) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),"
                "('TRUNCATE'),('REFERENCES'),('TRIGGER')) AS p(privilege) "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p')")).all()
            actual = frozenset((table, role, privilege) for table, privilege, allowed in grants if allowed)
            assert actual == frozenset(item for item in policy.EXPECTED_TABLE_ACL if item[1] == role)
        for statement in ("CREATE TABLE public.forbidden_runtime_ddl (id int)",
                          "ALTER TABLE public.alembic_version ADD COLUMN forbidden int",
                          "SET ROLE platform_migration",
                          "SELECT rolpassword FROM pg_authid"):
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement)
            assert getattr(caught.value.orig, "sqlstate", None) == "42501"
    finally:
        engine.dispose()


def test_observed_catalog_cannot_bypass_v23_unqualified_or_source_gate(reference):
    evidence = _evidence(reference)
    assert policy.CATALOG_SHA256 == policy.UNQUALIFIED_CATALOG_SHA256 == "0" * 64
    assert evidence.catalog_sha256 != policy.CATALOG_SHA256
    with pytest.raises(behavior.PlatformDatabaseAttestationError, match="v23 catalog is UNQUALIFIED"):
        behavior.validate_platform_database_acl_evidence(evidence, require_head=True)
    with pytest.raises(behavior.PlatformDatabaseAttestationError, match="v23 catalog is UNQUALIFIED"):
        behavior.validate_platform_database_evidence(evidence, "migration", require_runtime_acl=True, require_head=True)
    engine = _engine(reference)
    try:
        with engine.connect() as connection:
            with pytest.raises(behavior.PlatformDatabaseAttestationError, match="migration source catalog"):
                behavior.validate_platform_migration_source_state(connection)
    finally:
        engine.dispose()


def test_release_proof_component_rejects_missing_wrong_database_and_stale_clock(reference, monkeypatch):
    # Synthetic context is confined to memory, never published or described as
    # a real root receipt. This proves same-connection rejection boundaries.
    context = PlatformSecretIsolationContext(
        run_id="a" * 64, generation="root-proof-present", root_proof_id="b" * 64,
        platform_image="reference.invalid/platform@sha256:" + "c" * 64,
        platform_source_revision="d" * 40, platform_source_snapshot_sha256="sha256:" + "e" * 64)
    engine = create_engine(reference[0], poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            evidence = behavior.collect_platform_database_evidence(connection)
            endpoint = proof.platform_database_connection_endpoint_sha256(connection)
            local_proof = proof.build_platform_database_release_proof(
                connection, environment="production", isolation=context,
                database_endpoint_sha256=endpoint, evidence=evidence)
            monkeypatch.setattr(proof, "_installed_proof", None)
            with pytest.raises(proof.PlatformDatabaseReleaseProofError):
                proof.attest_platform_database_release_proof(connection, evidence)
            monkeypatch.setattr(proof, "_installed_proof", local_proof)
            proof.attest_platform_database_release_proof(connection, evidence)
            for mutation in (replace(local_proof, database_endpoint_sha256="f" * 64),
                             replace(local_proof, postmaster_start_time="2000-01-01T00:00:00.000000Z"),
                             replace(local_proof, config_load_time="2000-01-01T00:00:00.000000Z")):
                monkeypatch.setattr(proof, "_installed_proof", mutation)
                with pytest.raises(proof.PlatformDatabaseReleaseProofError):
                    proof.attest_platform_database_release_proof(connection, evidence)
    finally:
        engine.dispose()
