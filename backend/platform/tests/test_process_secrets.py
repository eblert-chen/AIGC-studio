from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sys
import types
from pathlib import Path
from urllib.parse import urlencode

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from jsonschema import Draft202012Validator
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.engine import make_url

from platform_api import config as platform_config
from platform_api.config import Settings
from platform_api import process_secrets
from platform_api.process_secrets import (
    PLATFORM_PROCESS_ROLES,
    PlatformProcessSecretError,
    load_platform_process_secret_settings,
    parse_platform_process_secret_document,
    platform_process_secret_semantic_commitments,
    read_protected_platform_process_secret_file,
    rewrite_platform_database_url_ca_path,
    validate_platform_database_ca,
)
from platform_api.publishing_adapters import (
    MockPublisherAdapter,
    load_publisher_adapter,
)
from platform_api.publishing_worker import load_publication_media_resolver
from platform_api.services.input_assets import InputAssetService


PLATFORM_DATABASE_CA_PATH = "/run/secrets/platform-database-ca.pem"


@pytest.fixture(autouse=True)
def _platform_database_ca_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        process_secrets.PLATFORM_DATABASE_CA_FILE_ENV,
        PLATFORM_DATABASE_CA_PATH,
    )


def _secret(label: str) -> str:
    return f"v1-{label}-{hashlib.sha256(label.encode()).hexdigest()}-X9!"


def _database_password(label: str) -> str:
    return hashlib.sha512(("platform-db:" + label).encode()).hexdigest()


def _synthetic_database_ca() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "platform-db-ca.invalid")]
    )
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(process_secrets.Encoding.PEM)


def _database_url(role: str) -> str:
    return (
        "postgresql+psycopg://"
        + process_secrets.PLATFORM_DATABASE_ROLE_BY_PROCESS[role]
        + ":"
        + _database_password(f"db-{role}")
        + "@postgres.example.invalid:5432/ai_video?"
        + urlencode(
            (
                ("sslmode", "verify-full"),
                ("sslrootcert", PLATFORM_DATABASE_CA_PATH),
            )
        )
    )


def _relay(role: str) -> dict[str, object]:
    return {
        "database_url": _database_url(role),
        "relay_backends": {
            "new-api-v1": {
                "base_url": "https://relay.example.invalid",
                "client_id": f"platform-{role}",
                "api_key": _secret(f"new-api-{role}"),
                "contract_revision": "generations.v1",
            }
        },
    }


def _document(role: str) -> dict[str, object]:
    if role == "migration":
        secrets: dict[str, object] = {"database_url": _database_url(role)}
    elif role in {"relay-sync", "timeout-worker"}:
        secrets = _relay(role)
    elif role == "dispatcher":
        secrets = {
            **_relay(role),
            "huawei_obs_access_key_id": "AKID-DISPATCHER-9X7Q",
            "huawei_obs_secret_access_key": _secret("dispatcher-obs"),
            "huawei_obs_security_token": None,
        }
    elif role == "publishing-worker":
        secrets = {
            "database_url": _database_url(role),
            "publishing_plugin_credentials": {
                "adapters": {
                    "publishers.tiktok:create": {
                        "CLIENT_SECRET": _secret("publisher-client")
                    }
                },
                "media_resolvers": {
                    "publishers.obs:create_resolver": {
                        "READ_TOKEN": _secret("publisher-media")
                    }
                },
            },
        }
    elif role == "download-gateway-registration-worker":
        secrets = {
            "database_url": _database_url(role),
            "download_gateway_service_token": _secret("gateway-token"),
            "download_gateway_registration_signing_secret": _secret("gateway-hmac"),
            "download_gateway_attempt_encryption_key_base64": base64.b64encode(
                hashlib.sha256(b"gateway-aes-key").digest()
            ).decode(),
        }
    elif role == "platform-api":
        secrets = {
            **_relay(role),
            "relay_tenant_id": "8f5656a0-9f00-4ca4-97ca-b7e38f4cf94d",
            "relay_operations_token": _secret("api-operations"),
            "relay_reconciliation_approval_key_id": "approval-v1",
            "relay_reconciliation_approval_secret": _secret("api-approval"),
            "relay_callback_signing_secrets": {
                "new-api-v1": _secret("api-new-callback")
            },
            "internal_service_token": _secret("api-internal"),
            "download_edge_completion_service_token": _secret("api-edge-bearer"),
            "channel_cost_signing_secret": _secret("api-cost"),
            "relay_telemetry_signing_secret": _secret("api-telemetry"),
            "provider_alert_signing_secret": _secret("api-alert-in"),
            "provider_alert_forward_signing_secret": _secret("api-alert-out"),
            "download_completion_edge_gateway_signing_secret": _secret(
                "api-edge-completion"
            ),
            "download_completion_obs_access_log_signing_secret": _secret(
                "api-obs-completion"
            ),
            "download_gateway_service_token": _secret("api-gateway-token"),
            "download_gateway_registration_signing_secret": _secret("api-gateway-hmac"),
            "download_gateway_attempt_encryption_key_base64": base64.b64encode(
                hashlib.sha256(b"api-gateway-aes-key").digest()
            ).decode(),
            "jwt_signing_secret": _secret("api-jwt"),
            "huawei_obs_access_key_id": "AKID-PLATFORM-API-9X7Q",
            "huawei_obs_secret_access_key": _secret("api-obs"),
            "huawei_obs_security_token": None,
            "publishing_plugin_credentials": {"adapters": {}},
        }
    else:  # pragma: no cover - test helper is exhaustive
        raise AssertionError(role)
    return {
        "kind": "platform_process_runtime_secrets",
        "schema_version": 1,
        "process_role": role,
        "secrets": secrets,
    }


def _raw(role: str) -> bytes:
    return json.dumps(_document(role), separators=(",", ":")).encode()


def _protected_nonsecret_settings(role: str) -> dict[str, object]:
    values: dict[str, object] = {"environment": "production"}
    if role in {"dispatcher", "relay-sync", "timeout-worker"}:
        values.update(
            relay_default_backend_id="new-api-v1",
            relay_default_contract_revision="generations.v1",
        )
    if role == "dispatcher":
        values.update(
            input_asset_store="huawei_obs",
            input_asset_public_base_url="https://platform.example.invalid",
            input_asset_relay_base_url="https://platform.example.invalid",
            huawei_obs_endpoint="https://obs.cn-south-1.myhuaweicloud.com",
            huawei_obs_bucket="ai-video-inputs",
        )
    if role == "publishing-worker":
        values.update(
            publishing_worker_enabled=True,
            publishing_adapters="publishers.tiktok:create",
            publishing_media_resolver="publishers.obs:create_resolver",
        )
    if role == "download-gateway-registration-worker":
        values.update(
            download_gateway_registration_worker_enabled=True,
            download_gateway_registration_url=(
                "https://downloads.example.invalid/internal/v1/download-tickets"
            ),
            download_gateway_public_base_url="https://downloads.example.invalid",
        )
    return values


@pytest.mark.parametrize("role", sorted(PLATFORM_PROCESS_ROLES))
def test_all_process_bundle_shapes_parse(role: str) -> None:
    parsed = parse_platform_process_secret_document(_raw(role), role)
    assert parsed["process_role"] == role
    assert parsed["database_url"].startswith("postgresql+psycopg://")


@pytest.mark.parametrize("role", sorted(PLATFORM_PROCESS_ROLES))
def test_committed_schema_accepts_every_role_shape(role: str) -> None:
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "schemas"
        / ("platform-process-runtime-secrets.schema.json")
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(_document(role))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw + b"\n",
        lambda raw: raw + b"{}",
        lambda raw: raw.replace(
            b'"schema_version":1', b'"schema_version":1,"schema_version":1'
        ),
        lambda raw: raw.replace(b'"secrets":{', b'"secrets":{"unknown":"value",', 1),
        lambda raw: raw.replace(b'"database_url":', b'"removed_database_url":', 1),
    ],
)
def test_closed_parser_rejects_trailing_duplicate_unknown_and_missing(mutate) -> None:
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(mutate(_raw("migration")), "migration")


def test_role_substitution_is_rejected() -> None:
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(_raw("relay-sync"), "timeout-worker")


@pytest.mark.parametrize("invalid_version", [True, 1.0, "1"])
def test_schema_version_requires_the_exact_integer_type(invalid_version) -> None:
    document = _document("migration")
    document["schema_version"] = invalid_version
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "migration",
        )


@pytest.mark.parametrize("role", ["platform-api", "dispatcher"])
def test_obs_process_bundles_reject_unused_filesystem_signing_secret(
    role: str,
) -> None:
    document = _document(role)
    document["secrets"]["input_asset_signing_secret"] = _secret(
        f"unused-filesystem-{role}"
    )
    raw = json.dumps(document, separators=(",", ":")).encode()
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(raw, role)


def test_obs_access_uses_store_signed_url_without_filesystem_signer() -> None:
    expected = "https://obs.example.invalid/synthetic-object"

    class SyntheticObsStore:
        kind = "huawei_obs"

        def signed_url(self, *_, **__) -> str:
            return expected

    asset = types.SimpleNamespace(
        storage_backend="huawei_obs",
        object_key="synthetic-object",
        original_filename="synthetic.png",
        id="synthetic-asset",
    )
    assert (
        InputAssetService.access_url(
            asset=asset,
            store=SyntheticObsStore(),
            signer=None,
            expires_seconds=300,
            disposition="inline",
        )
        == expected
    )


def test_database_dsn_requires_verify_full_and_unique_query_keys() -> None:
    raw = _raw("migration")
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            raw.replace(b"sslmode=verify-full", b"sslmode=require"), "migration"
        )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            raw.replace(
                b"sslmode=verify-full",
                b"sslmode=verify-full&sslmode=verify-full",
            ),
            "migration",
        )


@pytest.mark.parametrize(
    "query_parameter",
    [
        "password=query-override",
        "user=query_override",
        "passfile=/run/alternate",
        "sslpassword=query-override",
        "options=-csearch_path%3Dattacker",
        "host=alternate.example.invalid",
        "dbname=alternate",
        "application_name=unapproved",
    ],
)
def test_database_dsn_rejects_every_query_parameter_except_exact_tls(
    query_parameter: str,
) -> None:
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            _raw("migration").replace(
                b"sslmode=verify-full",
                f"sslmode=verify-full&{query_parameter}".encode(),
            ),
            "migration",
        )


def test_sqlalchemy_query_credentials_override_authority_and_are_rejected() -> None:
    synthetic = make_url(
        "postgresql+psycopg://platform_migration:authority-canary-32-bytes-X9!"
        "@postgres.example.invalid:5432/ai_video?sslmode=verify-full"
        "&user=query_override&password=query-override-canary"
    )
    _, connect_args = PGDialect_psycopg().create_connect_args(synthetic)
    assert connect_args["user"] == "query_override"
    assert connect_args["password"] == "query-override-canary"
    document = _document("migration")
    document["secrets"]["database_url"] = synthetic.render_as_string(
        hide_password=False
    )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "migration",
        )


def test_database_username_is_bound_to_the_exact_process_role() -> None:
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            _raw("migration").replace(
                b"platform_migration:", b"platform_api:"
            ),
            "migration",
        )


def test_database_password_cannot_be_reused_by_another_bundle_secret() -> None:
    document = _document("dispatcher")
    document["secrets"]["relay_backends"]["new-api-v1"]["api_key"] = (
        _database_password("db-dispatcher")
    )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "dispatcher",
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "relay.example.invalid",
        "http://relay.example.invalid",
        "urn:relay:new-api-v1",
        "https://user@relay.example.invalid",
        "https://relay.example.invalid/path?credential=override",
        "https://relay.example.invalid/path#fragment",
        "https://relay.example.invalid/" + "x" * 2048,
    ],
)
def test_relay_backend_base_url_is_bounded_absolute_http_uri(base_url: str) -> None:
    document = _document("relay-sync")
    document["secrets"]["relay_backends"]["new-api-v1"]["base_url"] = base_url
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "relay-sync",
        )


@pytest.mark.parametrize(
    "client_id",
    [
        "x" * 121,
        "contains whitespace",
        "slash/not-allowed",
        "colon:not-allowed",
        "change-me-client",
        "非ascii-client",
    ],
)
def test_relay_client_ids_use_one_bounded_ascii_contract(client_id: str) -> None:
    document = _document("relay-sync")
    document["secrets"]["relay_backends"]["new-api-v1"]["client_id"] = client_id
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "relay-sync",
        )


@pytest.mark.parametrize("role", sorted(PLATFORM_PROCESS_ROLES))
def test_semantic_projection_is_sorted_unique_and_commits_database_password(
    role: str,
) -> None:
    normalized = parse_platform_process_secret_document(_raw(role), role)
    commitments = platform_process_secret_semantic_commitments(normalized, role)
    identifiers = [item["id"] for item in commitments]
    prefix = process_secrets.PLATFORM_SECRET_ISOLATION_PREFIX_BY_PROCESS[role]
    assert identifiers == sorted(identifiers)
    assert len(identifiers) == len(set(identifiers))
    database_commitment = next(
        item for item in commitments if item["id"] == f"{prefix}.database.password"
    )
    assert database_commitment["sha256"] == hashlib.sha256(
        _database_password(f"db-{role}").encode()
    ).hexdigest()


def test_api_semantic_projection_matches_the_unified_validator_ids() -> None:
    normalized = parse_platform_process_secret_document(
        _raw("platform-api"), "platform-api"
    )
    identifiers = {
        item["id"]
        for item in platform_process_secret_semantic_commitments(
            normalized, "platform-api"
        )
    }
    assert identifiers == {
        "platform.api.callback.new-api-v1",
        "platform.api.channel_cost_signing_secret",
        "platform.api.database.endpoint",
        "platform.api.database.password",
        "platform.api.database.target",
        "platform.api.download_completion_edge_gateway_signing_secret",
        "platform.api.download_completion_obs_access_log_signing_secret",
        "platform.api.download_edge_completion_service_token",
        "platform.api.download_gateway_attempt_encryption_key.decoded",
        "platform.api.download_gateway_attempt_encryption_key.encoded",
        "platform.api.download_gateway_registration_signing_secret",
        "platform.api.download_gateway_service_token",
        "platform.api.internal_service_token",
        "platform.api.jwt_signing_secret",
        "platform.api.obs.access_key_id",
        "platform.api.obs.secret_access_key",
        "platform.api.provider_alert_forward_signing_secret",
        "platform.api.provider_alert_signing_secret",
        "platform.api.publishing_plugin.adapters",
        "platform.api.relay_backend.new-api-v1.api_key",
        "platform.api.relay_operations_token",
        "platform.api.relay_reconciliation_approval_secret",
        "platform.api.relay_telemetry_signing_secret",
    } - {"platform.api.publishing_plugin.adapters"}


def test_aes_semantic_projection_commits_encoded_and_decoded_forms() -> None:
    role = "download-gateway-registration-worker"
    normalized = parse_platform_process_secret_document(_raw(role), role)
    commitments = {
        item["id"]: item["sha256"]
        for item in platform_process_secret_semantic_commitments(normalized, role)
    }
    prefix = "platform.download_gateway_worker.download_gateway_attempt_encryption_key"
    encoded = normalized["download_gateway_attempt_encryption_key_base64"]
    assert commitments[f"{prefix}.encoded"] == hashlib.sha256(
        encoded.encode()
    ).hexdigest()
    assert commitments[f"{prefix}.decoded"] == hashlib.sha256(
        base64.b64decode(encoded)
    ).hexdigest()


def test_secret_values_enforce_the_committed_maximum_length() -> None:
    document = _document("download-gateway-registration-worker")
    document["secrets"]["download_gateway_service_token"] = (
        "abcdefgh" * 2049
    )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "download-gateway-registration-worker",
        )


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("dispatcher", "huawei_obs_secret_access_key"),
        (
            "download-gateway-registration-worker",
            "download_gateway_service_token",
        ),
    ],
)
def test_normal_obs_and_download_secrets_reject_short_periods(
    role: str,
    field: str,
) -> None:
    document = _document(role)
    document["secrets"][field] = "Ab3_def-" * 4
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            role,
        )


def test_relay_backend_api_key_rejects_short_periods() -> None:
    document = _document("relay-sync")
    document["secrets"]["relay_backends"]["new-api-v1"]["api_key"] = (
        "Ab3_def-" * 4
    )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "relay-sync",
        )


@pytest.mark.parametrize(
    "field",
    ["relay_client_id", "relay_api_key", "relay_callback_signing_secret"],
)
def test_protected_bundle_rejects_every_legacy_scalar(field: str) -> None:
    document = _document("platform-api")
    document["secrets"][field] = _secret(field)
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "platform-api",
        )


@pytest.mark.parametrize("map_name", ["relay_backends", "relay_callback_signing_secrets"])
def test_protected_bundle_rejects_callable_legacy_backend(map_name: str) -> None:
    document = _document("platform-api")
    if map_name == "relay_backends":
        document["secrets"][map_name]["legacy-default-v1"] = {
            "base_url": "https://legacy.example.invalid",
            "client_id": "legacy-client",
            "api_key": _secret("legacy-key"),
            "contract_revision": "generations.v1",
        }
    else:
        document["secrets"][map_name]["legacy-default-v1"] = _secret(
            "legacy-callback"
        )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "platform-api",
        )


@pytest.mark.parametrize(
    ("backend_id", "contract_revision"),
    [
        ("new-api-v2", "generations.v1"),
        ("new-api-v1", "generations.v2"),
    ],
)
def test_protected_bundle_pins_native_new_api_identity_and_contract(
    backend_id: str,
    contract_revision: str,
) -> None:
    document = _document("relay-sync")
    backend = document["secrets"]["relay_backends"].pop("new-api-v1")
    backend["contract_revision"] = contract_revision
    document["secrets"]["relay_backends"][backend_id] = backend

    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "relay-sync",
        )


def test_database_password_and_plugin_credential_reject_short_periods() -> None:
    document = _document("migration")
    document["secrets"]["database_url"] = (
        "postgresql+psycopg://platform_migration:"
        + "Ab3_def-" * 4
        + "@postgres.example.invalid:5432/ai_video?sslmode=verify-full"
    )
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "migration",
        )

    document = _document("publishing-worker")
    document["secrets"]["publishing_plugin_credentials"]["adapters"][
        "publishers.tiktok:create"
    ]["CLIENT_SECRET"] = "Ab3_def-" * 4
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "publishing-worker",
        )


def test_relay_callback_and_plugin_maps_enforce_schema_cardinality() -> None:
    document = _document("relay-sync")
    template = document["secrets"]["relay_backends"]["new-api-v1"]
    document["secrets"]["relay_backends"] = {
        f"backend-{index}": {
            **template,
            "api_key": _secret(f"backend-limit-{index}"),
        }
        for index in range(33)
    }
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "relay-sync",
        )

    document = _document("platform-api")
    document["secrets"]["relay_callback_signing_secrets"] = {
        f"backend-{index}": _secret(f"callback-limit-{index}")
        for index in range(33)
    }
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "platform-api",
        )

    document = _document("publishing-worker")
    document["secrets"]["publishing_plugin_credentials"]["adapters"] = {
        f"publishers.adapter_{index}:create": {
            "TOKEN": _secret(f"plugin-limit-{index}")
        }
        for index in range(33)
    }
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "publishing-worker",
        )


def test_plugin_credential_map_enforces_schema_cardinality() -> None:
    document = _document("publishing-worker")
    document["secrets"]["publishing_plugin_credentials"]["adapters"] = {
        "publishers.tiktok:create": {
            f"TOKEN_{index}": _secret(f"credential-limit-{index}")
            for index in range(65)
        }
    }
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "publishing-worker",
        )


def test_plugin_spec_has_one_cross_language_256_byte_limit() -> None:
    accepted = _document("publishing-worker")
    accepted["secrets"]["publishing_plugin_credentials"]["adapters"] = {
        "a" * 254 + ":f": {"TOKEN": _secret("plugin-spec-boundary")}
    }
    raw = json.dumps(accepted, separators=(",", ":")).encode()
    parse_platform_process_secret_document(raw, "publishing-worker")

    rejected = _document("publishing-worker")
    rejected["secrets"]["publishing_plugin_credentials"]["adapters"] = {
        "a" * 255 + ":f": {"TOKEN": _secret("plugin-spec-overflow")}
    }
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(rejected, separators=(",", ":")).encode(),
            "publishing-worker",
        )


def test_obs_identity_rejects_internal_unicode_whitespace() -> None:
    document = _document("dispatcher")
    document["secrets"]["huawei_obs_access_key_id"] = "AKID\u2003DISPATCHER"
    with pytest.raises(PlatformProcessSecretError):
        parse_platform_process_secret_document(
            json.dumps(document, separators=(",", ":")).encode(),
            "dispatcher",
        )


def test_database_ca_requires_one_canonical_x509_pem_sequence() -> None:
    raw = _synthetic_database_ca()
    validate_platform_database_ca(raw)

    for rejected in (
        raw.rstrip(b"\n"),
        raw.replace(b"\n", b"\r\n"),
        b"prefix\n" + raw,
        raw + b"suffix\n",
    ):
        with pytest.raises(PlatformProcessSecretError, match="database CA"):
            validate_platform_database_ca(rejected)


@pytest.mark.skipif(
    os.name == "nt", reason="private tmpfs/owner mode contract is Linux-only"
)
def test_verified_database_ca_snapshot_breaks_bind_source_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = _synthetic_database_ca()
    replacement_raw = _synthetic_database_ca()
    source = {"raw": source_raw}
    snapshot_directory = tmp_path / "snapshot"
    snapshot_directory.mkdir(mode=0o700)
    snapshot_directory.chmod(0o700)

    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "migration")
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_process_secret_file",
        lambda: _raw("migration"),
    )
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_database_ca_file",
        lambda: source["raw"],
    )

    from platform_api import platform_secret_receipt

    isolation_context = object()

    def verified_then_swap(**_: object) -> object:
        source["raw"] = replacement_raw
        return isolation_context

    monkeypatch.setattr(
        platform_secret_receipt,
        "verify_platform_secret_isolation_receipt_sources",
        verified_then_swap,
    )
    from platform_api import platform_database_release_proof

    proof_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        platform_database_release_proof,
        "load_and_install_platform_database_release_proof",
        lambda **values: proof_calls.append(values),
    )
    real_materialize = process_secrets.materialize_verified_platform_database_ca
    monkeypatch.setattr(
        process_secrets,
        "materialize_verified_platform_database_ca",
        lambda raw: real_materialize(
            raw,
            directory=str(snapshot_directory.resolve()),
            require_tmpfs=False,
        ),
    )

    settings = load_platform_process_secret_settings()
    snapshot_path = make_url(settings["database_url"]).query["sslrootcert"]
    assert snapshot_path != PLATFORM_DATABASE_CA_PATH
    assert Path(snapshot_path).read_bytes() == source_raw
    assert Path(snapshot_path).read_bytes() != source["raw"]
    assert proof_calls == [
        {
            "isolation": isolation_context,
            "database_endpoint_sha256": next(
                item["sha256"]
                for item in platform_process_secret_semantic_commitments(
                    parse_platform_process_secret_document(
                        _raw("migration"),
                        "migration",
                    ),
                    "migration",
                )
                if item["id"].endswith(".database.endpoint")
            ),
        }
    ]


def test_database_url_rewrite_changes_only_the_verified_ca_source() -> None:
    original = _database_url("migration")
    rewritten = rewrite_platform_database_url_ca_path(
        original,
        snapshot_path="/run/platform-database-ca-snapshot/committed.pem",
    )
    before = make_url(original)
    after = make_url(rewritten)
    assert after.set(query={}) == before.set(query={})
    assert after.query == {
        "sslmode": "verify-full",
        "sslrootcert": "/run/platform-database-ca-snapshot/committed.pem",
    }


def test_protected_entrypoint_cannot_consume_another_roles_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_config.get_settings.cache_clear()
    monkeypatch.setattr(
        platform_config,
        "load_platform_process_secret_settings",
        lambda: parse_platform_process_secret_document(
            _raw("relay-sync"), "relay-sync"
        ),
    )
    with pytest.raises(RuntimeError, match="does not match"):
        platform_config.get_settings("dispatcher")
    platform_config.get_settings.cache_clear()


def test_protected_settings_validation_does_not_echo_bundle_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_config.get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    canary = "SYNTHETIC_SECRET_CANARY_MUST_NOT_APPEAR"
    monkeypatch.setattr(
        platform_config,
        "load_platform_process_secret_settings",
        lambda: {
            "process_role": "migration",
            "database_url": canary,
        },
    )
    with pytest.raises(RuntimeError) as captured:
        platform_config.get_settings("migration")
    assert canary not in str(captured.value)
    assert captured.value.__suppress_context__ is True
    platform_config.get_settings.cache_clear()


def test_protected_migration_keeps_release_gateway_origin_out_of_role_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_config.get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "migration")
    monkeypatch.setenv(
        "DOWNLOAD_GATEWAY_PUBLIC_BASE_URL",
        "https://downloads.example.invalid",
    )
    monkeypatch.setattr(
        platform_config,
        "load_platform_process_secret_settings",
        lambda: parse_platform_process_secret_document(
            _raw("migration"),
            "migration",
        ),
    )
    try:
        settings = platform_config.get_settings("migration")
        assert settings.process_role == "migration"
        assert settings.download_gateway_public_base_url is None
    finally:
        platform_config.get_settings.cache_clear()


@pytest.mark.parametrize(
    "role",
    [
        "migration",
        "dispatcher",
        "relay-sync",
        "timeout-worker",
        "publishing-worker",
        "download-gateway-registration-worker",
    ],
)
def test_production_worker_settings_validate_only_their_process_minimum(
    role: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    settings = Settings(
        **_protected_nonsecret_settings(role),
        **parse_platform_process_secret_document(_raw(role), role),
    )
    assert settings.process_role == role


_NON_API_PROCESS_ROLES = (
    "migration",
    "dispatcher",
    "relay-sync",
    "timeout-worker",
    "publishing-worker",
    "download-gateway-registration-worker",
)

_BROWSER_AUTH_ENV_VALUES = {
    "JWT_SIGNING_SECRET": "browser-secret-material-must-not-reach-worker",
    "JWT_ISSUER": "browser-issuer",
    "JWT_AUDIENCE": "browser-audience",
    "AUTH_LEGACY_BEARER_ENABLED": "false",
    "OIDC_ENABLED": "false",
    "OIDC_SELF_SIGNUP_ENABLED": "false",
    "OIDC_ISSUER": "https://idp.example.invalid/",
    "OIDC_AUTHORIZATION_ENDPOINT": "https://idp.example.invalid/authorize",
    "OIDC_TOKEN_ENDPOINT": "https://idp.example.invalid/token",
    "OIDC_JWKS_URI": "https://idp.example.invalid/jwks",
    "OIDC_CLIENT_ID": "ai-video-platform",
    "OIDC_REDIRECT_URI": "https://platform.example.invalid/api/v1/auth/callback",
    "FRONTEND_ORIGIN": "https://app.example.invalid",
    "ACCOUNT_MANAGEMENT_URL": "https://idp.example.invalid/account",
    "AUTH_SESSION_TTL_SECONDS": "28800",
    "AUTH_SESSION_IDLE_TTL_SECONDS": "3600",
    "AUTH_ACCOUNT_STEP_UP_MAX_AGE_SECONDS": "300",
    "OIDC_LOGIN_TRANSACTION_TTL_SECONDS": "600",
    "OIDC_ID_TOKEN_MAX_LIFETIME_SECONDS": "900",
    "OIDC_LOGIN_IP_WINDOW_SECONDS": "600",
    "OIDC_LOGIN_IP_MAX_ATTEMPTS": "20",
    "INVITATION_TTL_SECONDS": "604800",
    "PLATFORM_OWNER_USER_IDS": "owner-subject",
    "PLATFORM_ADMIN_REQUIRED_AMR": "webauthn",
    "PLATFORM_ADMIN_STEP_UP_MAX_AGE_SECONDS": "300",
}


@pytest.mark.parametrize("role", _NON_API_PROCESS_ROLES)
@pytest.mark.parametrize("environment_name", sorted(_BROWSER_AUTH_ENV_VALUES))
@pytest.mark.parametrize("present_value", ["", "configured"])
def test_protected_non_api_process_rejects_every_browser_auth_environment_key(
    role: str,
    environment_name: str,
    present_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv(
        environment_name,
        _BROWSER_AUTH_ENV_VALUES[environment_name] if present_value else "",
    )
    with pytest.raises(Exception) as captured:
        Settings(
            _env_file=None,
            **_protected_nonsecret_settings(role),
            **parse_platform_process_secret_document(_raw(role), role),
        )
    assert type(captured.value).__name__ in {"ValidationError", "SettingsError"}


@pytest.mark.parametrize("role", _NON_API_PROCESS_ROLES)
@pytest.mark.parametrize("environment_name", sorted(_BROWSER_AUTH_ENV_VALUES))
def test_cross_role_browser_auth_environment_fails_before_any_protected_read(
    role: str,
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", role)
    monkeypatch.setenv(environment_name, "")
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_process_secret_file",
        lambda: pytest.fail("browser-auth gate reached process secret file I/O"),
    )
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_database_ca_file",
        lambda: pytest.fail("browser-auth gate reached database CA file I/O"),
    )
    with pytest.raises(
        PlatformProcessSecretError,
        match="browser authentication environment",
    ) as captured:
        load_platform_process_secret_settings()
    assert environment_name in str(captured.value)


def test_cross_site_browser_origins_fail_before_any_protected_source_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_config.get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "platform-api")
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://app.example.net")
    monkeypatch.setenv(
        "INPUT_ASSET_PUBLIC_BASE_URL",
        "https://platform.example.com",
    )
    monkeypatch.setenv(
        "OIDC_REDIRECT_URI",
        "https://platform.example.com/api/v1/auth/callback",
    )
    monkeypatch.setattr(
        platform_config,
        "load_platform_process_secret_settings",
        lambda: pytest.fail("schemeful-site gate reached protected source I/O"),
    )
    try:
        with pytest.raises(RuntimeError, match="schemeful site"):
            platform_config.get_settings("platform-api")
    finally:
        platform_config.get_settings.cache_clear()


@pytest.mark.parametrize(
    ("environment_name", "invalid_value", "message"),
    [
        ("FRONTEND_ORIGIN", None, "FRONTEND_ORIGIN"),
        ("FRONTEND_ORIGIN", "http://app.example.com", "FRONTEND_ORIGIN"),
        (
            "INPUT_ASSET_PUBLIC_BASE_URL",
            None,
            "Platform public origin",
        ),
        (
            "INPUT_ASSET_PUBLIC_BASE_URL",
            "http://platform.example.com",
            "Platform public origin",
        ),
        ("OIDC_REDIRECT_URI", None, "OIDC_REDIRECT_URI"),
        (
            "OIDC_REDIRECT_URI",
            "http://platform.example.com/api/v1/auth/callback",
            "OIDC_REDIRECT_URI",
        ),
        (
            "OIDC_REDIRECT_URI",
            "https://other.example.com/api/v1/auth/callback",
            "canonical Platform public origin",
        ),
    ],
)
def test_invalid_browser_origin_fails_before_any_protected_source_read(
    environment_name: str,
    invalid_value: str | None,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_config.get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "platform-api")
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://app.example.com")
    monkeypatch.setenv(
        "INPUT_ASSET_PUBLIC_BASE_URL",
        "https://platform.example.com",
    )
    monkeypatch.setenv(
        "OIDC_REDIRECT_URI",
        "https://platform.example.com/api/v1/auth/callback",
    )
    if invalid_value is None:
        monkeypatch.delenv(environment_name, raising=False)
    else:
        monkeypatch.setenv(environment_name, invalid_value)
    monkeypatch.setattr(
        platform_config,
        "load_platform_process_secret_settings",
        lambda: pytest.fail("browser-origin gate reached protected source I/O"),
    )
    try:
        with pytest.raises(RuntimeError, match=message):
            platform_config.get_settings("platform-api")
    finally:
        platform_config.get_settings.cache_clear()


def test_production_api_settings_accepts_its_full_file_only_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    settings = Settings(
        environment="production",
        cors_origins=["https://app.example.invalid"],
        relay_default_backend_id="new-api-v1",
        relay_default_contract_revision="generations.v1",
        relay_operations_base_url="https://relay.example.invalid",
        relay_native_admin_console_origin="https://relay-admin.example.invalid",
        relay_callback_public_url=(
            "https://platform.example.invalid/internal/relay-callbacks"
        ),
        channel_cost_signature_required=True,
        provider_alert_forward_webhook_url="https://alerts.example.invalid/events",
        download_gateway_registration_url=(
            "https://downloads.example.invalid/internal/v1/download-tickets"
        ),
        download_gateway_public_base_url="https://downloads.example.invalid",
        download_gateway_registration_worker_enabled=True,
        relay_allow_legacy_artifact_download_response=False,
        jwt_issuer="https://idp.example.invalid/",
        jwt_audience="ai-video-platform",
        oidc_enabled=True,
        oidc_self_signup_enabled=False,
        oidc_issuer="https://idp.example.invalid/",
        oidc_authorization_endpoint="https://idp.example.invalid/authorize",
        oidc_token_endpoint="https://idp.example.invalid/token",
        oidc_jwks_uri="https://idp.example.invalid/jwks",
        oidc_client_id="ai-video-platform",
        oidc_redirect_uri=(
            "https://platform.example.invalid/api/v1/auth/callback"
        ),
        frontend_origin="https://app.example.invalid",
        account_management_url="https://idp.example.invalid/account",
        platform_owner_user_ids=["owner-subject-01"],
        platform_admin_required_amr=["webauthn"],
        input_asset_store="huawei_obs",
        input_asset_public_base_url="https://platform.example.invalid",
        input_asset_relay_base_url="https://platform.example.invalid",
        huawei_obs_endpoint="https://obs.cn-south-1.myhuaweicloud.com",
        huawei_obs_bucket="ai-video-inputs",
        publishing_worker_enabled=False,
        **parse_platform_process_secret_document(_raw("platform-api"), "platform-api"),
    )
    assert settings.process_role == "platform-api"


@pytest.mark.parametrize("raw_name", ["DATABASE_URL", "database_url"])
def test_protected_loader_rejects_raw_secret_environment_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
    raw_name: str,
) -> None:
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "migration")
    monkeypatch.setenv(raw_name, "")
    with pytest.raises(PlatformProcessSecretError, match="DATABASE_URL"):
        load_platform_process_secret_settings()


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize("explicit", ["false", "0", ""])
def test_outer_protected_environment_cannot_disable_typed_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    explicit: str,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", explicit)
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_process_secret_file",
        lambda: pytest.fail("invalid protection flag reached file I/O"),
    )
    with pytest.raises(
        PlatformProcessSecretError,
        match="cannot be disabled",
    ):
        load_platform_process_secret_settings()


@pytest.mark.parametrize(
    "environment",
    ["Production", "PRODUCTION", " production", "production ", "Staging"],
)
def test_protected_environment_alias_is_rejected_before_any_source_read(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    events: list[str] = []
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("PLATFORM_PROTECTED_RUNTIME", "true")
    monkeypatch.setenv("PLATFORM_PROCESS_ROLE", "migration")
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_process_secret_file",
        lambda: events.append("bundle") or b"{}",
    )
    monkeypatch.setattr(
        process_secrets,
        "read_protected_platform_database_ca_file",
        lambda: events.append("ca") or b"invalid",
    )
    with pytest.raises(PlatformProcessSecretError, match="ENVIRONMENT"):
        load_platform_process_secret_settings()
    assert events == []


def test_staging_without_explicit_flag_still_rejects_raw_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("PLATFORM_PROTECTED_RUNTIME", raising=False)
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(PlatformProcessSecretError, match="DATABASE_URL"):
        load_platform_process_secret_settings()


def test_production_publishing_factories_require_explicit_manifest_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("synthetic_secure_publishing")
    module.no_manifest_adapter = lambda: MockPublisherAdapter()
    received: list[dict[str, str]] = []

    def secure_adapter(*, credential_manifest):
        received.append(dict(credential_manifest))
        return MockPublisherAdapter()

    class Resolver:
        def resolve(self, artifact):
            return ("https://media.example.invalid/item",)

    def secure_resolver(*, credential_manifest):
        received.append(dict(credential_manifest))
        return Resolver()

    module.secure_adapter = secure_adapter
    module.secure_resolver = secure_resolver
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(ValueError, match="must require keyword-only"):
        load_publisher_adapter(
            f"{module.__name__}:no_manifest_adapter",
            credential_manifest={},
            require_credential_manifest=True,
        )
    manifest = {"CLIENT_SECRET": _secret("factory-client")}
    load_publisher_adapter(
        f"{module.__name__}:secure_adapter",
        credential_manifest=manifest,
        require_credential_manifest=True,
    )
    load_publication_media_resolver(
        f"{module.__name__}:secure_resolver",
        credential_manifest=manifest,
        require_credential_manifest=True,
    )
    assert received == [manifest, manifest]


def test_production_factory_exception_is_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("synthetic_failing_publisher")
    canary = "SYNTHETIC_PLUGIN_SECRET_CANARY"

    def failing_factory(*, credential_manifest):
        raise ValueError(credential_manifest["CLIENT_SECRET"])

    module.create = failing_factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(RuntimeError) as captured:
        load_publisher_adapter(
            f"{module.__name__}:create",
            credential_manifest={"CLIENT_SECRET": canary},
            require_credential_manifest=True,
        )
    assert canary not in str(captured.value)
    assert captured.value.__suppress_context__ is True


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX owner/mount semantics run in Linux image"
)
def test_protected_reader_checks_owner_mode_inode_and_read_only_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.json"
    path.write_bytes(_raw("migration"))
    path.chmod(0o400)
    monkeypatch.setenv(
        process_secrets.PLATFORM_PROCESS_SECRET_FILE_ENV, str(path.resolve())
    )
    monkeypatch.setattr(process_secrets, "_protected_file_read_only", lambda _: True)
    real_open = os.open

    def protected_open(target, flags, *args, **kwargs):
        if flags & os.O_WRONLY:
            raise PermissionError("synthetic read-only bind")
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", protected_open)
    assert read_protected_platform_process_secret_file() == _raw("migration")
