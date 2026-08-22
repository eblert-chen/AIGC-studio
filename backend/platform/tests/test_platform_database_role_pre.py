from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from urllib.parse import urlencode

import pytest

from platform_api import database_role_pre, process_secrets
from platform_api.database_role_pre import (
    PlatformDatabaseRolePreError,
    generate_scram_sha256_verifier,
    load_platform_database_role_pre_sources,
)
from platform_api.platform_secret_receipt import PlatformSecretIsolationContext


CA_PATH = "/run/secrets/platform-database-ca.pem"


def _password(label: str) -> str:
    return hashlib.sha512(("platform-role-pre:" + label).encode()).hexdigest()


def _valid_until() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=180)
    ).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _clean_protected_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(process_secrets.os.environ):
        if (
            name.casefold().startswith("pg")
            or name.upper() in process_secrets.RAW_PLATFORM_SECRET_ENVIRONMENTS
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_DATABASE_NAME", "platform")
    monkeypatch.setenv("PLATFORM_DATABASE_ROLE_VALID_UNTIL", _valid_until())
    monkeypatch.setenv("PLATFORM_DATABASE_CA_FILE", CA_PATH)
    monkeypatch.setenv(
        "PLATFORM_PROCESS_ROLE",
        database_role_pre.PLATFORM_DATABASE_ROLE_PRE_PROCESS_ROLE,
    )
    monkeypatch.setenv(
        "PLATFORM_DATABASE_RELEASE_PROOF_FILE",
        database_role_pre.PLATFORM_DATABASE_RELEASE_PROOF_PATH,
    )


def _role_admin_dsn() -> bytes:
    return (
        "postgresql+psycopg://platform_role_admin:"
        + _password("role-admin")
        + "@postgres.platform.invalid:5432/postgres?"
        + urlencode(
            (
                ("sslmode", "verify-full"),
                ("sslrootcert", CA_PATH),
            )
        )
    ).encode()


def test_role_pre_snapshots_nine_sources_then_verifies_one_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = {
        database_role_pre.PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE_ENV: (
            _role_admin_dsn()
        ),
    }
    for source in database_role_pre._PASSWORD_SOURCES:
        source_raw[source.environment] = _password(source.process_role).encode()
    verified: list[dict[str, object]] = []
    monkeypatch.setattr(
        database_role_pre,
        "_read_protected_platform_source",
        lambda *, environment, **_: source_raw[environment],
    )
    monkeypatch.setattr(
        database_role_pre,
        "read_protected_platform_database_ca_file",
        lambda: b"synthetic-canonical-ca\n",
    )
    isolation = PlatformSecretIsolationContext(
        run_id="a" * 64,
        generation="root-proof-present",
        root_proof_id="b" * 64,
        platform_image=(
            "registry.example.invalid/platform@sha256:" + "c" * 64
        ),
        platform_source_revision="d" * 40,
        platform_source_snapshot_sha256="sha256:" + "e" * 64,
    )
    monkeypatch.setattr(
        database_role_pre,
        "verify_platform_secret_isolation_receipt_sources",
        lambda **values: verified.append(values) or isolation,
    )
    monkeypatch.setattr(
        database_role_pre,
        "materialize_verified_platform_database_ca",
        lambda _: "/proc/self/fd/71",
    )

    loaded = load_platform_database_role_pre_sources()

    assert loaded.target_database_name == "platform"
    assert loaded.role_admin_database_url.endswith(
        "sslmode=verify-full&sslrootcert=%2Fproc%2Fself%2Ffd%2F71"
    )
    assert set(loaded.passwords) == set(
        process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS
    )
    assert len(verified) == 1
    assert verified[0]["consumer"] == "platform-db-role-pre"
    files = verified[0]["files"]
    assert set(files) == {
        "platform_role_admin_dsn",
        "platform_database_ca",
        *(source.file_id for source in database_role_pre._PASSWORD_SOURCES),
    }
    semantic_ids = [item["id"] for item in verified[0]["semantics"]]
    assert semantic_ids == sorted(semantic_ids)
    assert semantic_ids == sorted(
        {
            "platform.role_admin.database.password",
            "platform.role_admin.database.target",
            "platform.role_admin.database.endpoint",
            *(source.semantic_id for source in database_role_pre._PASSWORD_SOURCES),
        }
    )
    assert loaded.isolation_context == isolation
    assert loaded.database_endpoint_sha256 == next(
        item["sha256"]
        for item in verified[0]["semantics"]
        if item["id"] == "platform.role_admin.database.endpoint"
    )


@pytest.mark.parametrize(
    "environment",
    [
        "PLATFORM_DATABASE_ROLE_ADMIN_DSN",
        "platform_api_database_password",
        "PGPASSWORD",
        "pGsErViCe",
        "HTTPS_PROXY",
        "ssl_cert_file",
    ],
)
def test_role_pre_rejects_ambient_or_raw_credentials_before_source_read(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    reads: list[str] = []
    monkeypatch.setenv(environment, "")
    monkeypatch.setattr(
        database_role_pre,
        "_read_protected_platform_source",
        lambda **_: reads.append("read") or b"unreachable",
    )
    with pytest.raises((PlatformDatabaseRolePreError, process_secrets.PlatformProcessSecretError)):
        load_platform_database_role_pre_sources()
    assert reads == []


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "x" * 129,
        "valid_length_but_contains_padding==",
        "valid_length_but_contains_space_x ",
        "Ab3_def-" * 4,
        "A" * 64,
        "replace_with_database_password_material",
    ],
)
def test_database_password_contract_is_canonical_base64url(password: str) -> None:
    with pytest.raises(process_secrets.PlatformProcessSecretError):
        process_secrets._database_password(password)


def test_scram_verifier_matches_the_cross_language_known_vector() -> None:
    assert generate_scram_sha256_verifier(
        "correct-horse-battery-staple-2026",
        salt=bytes(range(16)),
    ) == (
        "SCRAM-SHA-256$4096:AAECAwQFBgcICQoLDA0ODw==$"
        "5XplHSQgDky+8MUJDknWhdKms/Zoyud6lt/oRVtBcJQ=:"
        "JHoUcwr02H3NZ1AZ5ZPeTV3hZdsYOVTITi5mEPo1GG0="
    )


def test_provision_orders_read_only_preflight_before_every_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    sources = database_role_pre.PlatformDatabaseRolePreSources(
        role_admin_database_url="postgresql+psycopg://unreachable",
        target_database_name="platform",
        valid_until=_valid_until(),
        legacy_owner=None,
        passwords={},
    )
    state = database_role_pre._PreflightState(
        protected_principal_count=7,
        target_exists=True,
        target_head="0035_operations_evidence",
        target_owner="platform_migration",
    )
    @contextmanager
    def held_lock(_database_url: str):
        events.append("lock")
        yield object()
        events.append("unlock")

    monkeypatch.setattr(database_role_pre, "_held_provision_lock", held_lock)
    monkeypatch.setattr(
        database_role_pre,
        "_preflight",
        lambda *_: events.append("preflight") or state,
    )
    monkeypatch.setattr(
        database_role_pre,
        "_provision_principals",
        lambda *_: events.append("principals"),
    )
    monkeypatch.setattr(
        database_role_pre,
        "_handoff_target_ownership",
        lambda *_: events.append("ownership"),
    )
    monkeypatch.setattr(
        database_role_pre,
        "_normalize_cluster_database_acl",
        lambda *_: events.append("acl"),
    )
    monkeypatch.setattr(
        database_role_pre,
        "_verify_role_logins",
        lambda *_: events.append("login"),
    )
    monkeypatch.setattr(
        database_role_pre,
        "_publish_platform_database_release_proof",
        lambda *_: events.append("proof"),
    )
    database_role_pre.provision_platform_database_roles(sources)
    assert events == [
        "lock",
        "preflight",
        "principals",
        "ownership",
        "acl",
        "login",
        "proof",
        "unlock",
    ]


def test_failure_output_is_fixed_and_never_contains_source_canaries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = _password("must-not-be-logged")
    events: list[str] = []
    monkeypatch.setattr(
        database_role_pre,
        "invalidate_platform_database_release_proof",
        lambda: events.append("invalidate"),
    )
    monkeypatch.setattr(
        database_role_pre,
        "load_platform_database_role_pre_sources",
        lambda: events.append("load")
        or (_ for _ in ()).throw(PlatformDatabaseRolePreError(canary)),
    )
    assert database_role_pre.main() == 1
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err
    assert captured.out == ""
    assert captured.err == "protected Platform database role predecessor failed\n"
    assert events == ["invalidate", "load"]


@pytest.mark.parametrize(
    ("environment", "value"),
    [
        ("ENVIRONMENT", "development"),
        ("PLATFORM_PROTECTED_RUNTIME", "false"),
        ("PLATFORM_PROCESS_ROLE", "migration"),
        (
            "PLATFORM_DATABASE_RELEASE_PROOF_FILE",
            "/tmp/attestation.json",
        ),
    ],
)
def test_invalid_invocation_cannot_delete_a_release_proof(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: str,
    value: str,
) -> None:
    events: list[str] = []
    monkeypatch.setenv(environment, value)
    monkeypatch.setattr(
        database_role_pre,
        "invalidate_platform_database_release_proof",
        lambda: events.append("deleted"),
    )
    monkeypatch.setattr(
        database_role_pre,
        "load_platform_database_role_pre_sources",
        lambda: events.append("source-read"),
    )
    assert database_role_pre.main() == 1
    assert events == []
    assert capsys.readouterr().err == (
        "protected Platform database role predecessor failed\n"
    )


def test_role_pre_module_does_not_read_deploy_secret_inventory() -> None:
    source = Path(database_role_pre.__file__).read_text(encoding="utf-8")
    assert "deploy/secrets" not in source.replace("\\", "/")
