from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from uuid import UUID

from .relay_identity import (
    LEGACY_RELAY_BACKEND_ID,
    NEW_API_RELAY_BACKEND_ID,
    NEW_API_RELAY_CONTRACT_REVISION,
)

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

if sys.platform.startswith("linux"):
    import fcntl
else:  # pragma: no cover - deployment contract is Linux
    fcntl = None  # type: ignore[assignment]

PLATFORM_PROCESS_SECRET_KIND = "platform_process_runtime_secrets"
PLATFORM_PROCESS_SECRET_SCHEMA_VERSION = 1
PLATFORM_PROCESS_SECRET_FILE_ENV = "PLATFORM_PROCESS_RUNTIME_SECRETS_FILE"
PLATFORM_PROCESS_ROLE_ENV = "PLATFORM_PROCESS_ROLE"
PLATFORM_PROTECTED_RUNTIME_ENV = "PLATFORM_PROTECTED_RUNTIME"
PLATFORM_DATABASE_CA_FILE_ENV = "PLATFORM_DATABASE_CA_FILE"
PLATFORM_DATABASE_CA_FILE_ID = "platform_database_ca"
PLATFORM_DATABASE_CA_SNAPSHOT_DIRECTORY = "/run/platform-database-ca-snapshot"
_MAXIMUM_SECRET_FILE_BYTES = 256 * 1024
_MAXIMUM_DATABASE_CA_BYTES = 256 * 1024
_MINIMUM_SECRET_BYTES = 32
_SEALED_DATABASE_CA_DESCRIPTORS: dict[str, int] = {}

PLATFORM_PROCESS_ROLES = frozenset(
    {
        "migration",
        "platform-api",
        "dispatcher",
        "relay-sync",
        "timeout-worker",
        "publishing-worker",
        "download-gateway-registration-worker",
    }
)

PLATFORM_DATABASE_ROLE_BY_PROCESS = MappingProxyType(
    {
        "migration": "platform_migration",
        "platform-api": "platform_api",
        "dispatcher": "platform_dispatcher",
        "relay-sync": "platform_relay_sync",
        "timeout-worker": "platform_timeout_worker",
        "publishing-worker": "platform_publishing_worker",
        "download-gateway-registration-worker": "platform_download_gateway_worker",
    }
)

_RELAY_FIELDS = frozenset(
    {
        "relay_backends",
    }
)
_PROCESS_SECRET_FIELDS: dict[str, frozenset[str]] = {
    "migration": frozenset({"database_url"}),
    "platform-api": frozenset(
        {
            "database_url",
            *_RELAY_FIELDS,
            "relay_tenant_id",
            "relay_operations_token",
            "relay_reconciliation_approval_key_id",
            "relay_reconciliation_approval_secret",
            "relay_callback_signing_secrets",
            "internal_service_token",
            "download_edge_completion_service_token",
            "channel_cost_signing_secret",
            "relay_telemetry_signing_secret",
            "provider_alert_signing_secret",
            "provider_alert_forward_signing_secret",
            "download_completion_edge_gateway_signing_secret",
            "download_completion_obs_access_log_signing_secret",
            "download_gateway_service_token",
            "download_gateway_registration_signing_secret",
            "download_gateway_attempt_encryption_key_base64",
            "jwt_signing_secret",
            "huawei_obs_access_key_id",
            "huawei_obs_secret_access_key",
            "huawei_obs_security_token",
            "publishing_plugin_credentials",
        }
    ),
    "dispatcher": frozenset(
        {
            "database_url",
            *_RELAY_FIELDS,
            "huawei_obs_access_key_id",
            "huawei_obs_secret_access_key",
            "huawei_obs_security_token",
        }
    ),
    "relay-sync": frozenset({"database_url", *_RELAY_FIELDS}),
    "timeout-worker": frozenset({"database_url", *_RELAY_FIELDS}),
    "publishing-worker": frozenset({"database_url", "publishing_plugin_credentials"}),
    "download-gateway-registration-worker": frozenset(
        {
            "database_url",
            "download_gateway_service_token",
            "download_gateway_registration_signing_secret",
            "download_gateway_attempt_encryption_key_base64",
        }
    ),
}

# These settings contain credentials, cryptographic key material, or a DSN
# password. Protected staging/production processes reject them even when the
# variable is present with an empty value. The only accepted source is the
# role-specific protected file.
RAW_PLATFORM_SECRET_ENVIRONMENTS = frozenset(
    {
        "DATABASE_URL",
        "PLATFORM_DATABASE_URL",
        "PLATFORM_DATABASE_ROLE_ADMIN_DSN",
        "PLATFORM_MIGRATION_DATABASE_PASSWORD",
        "PLATFORM_API_DATABASE_PASSWORD",
        "PLATFORM_DISPATCHER_DATABASE_PASSWORD",
        "PLATFORM_RELAY_SYNC_DATABASE_PASSWORD",
        "PLATFORM_TIMEOUT_WORKER_DATABASE_PASSWORD",
        "PLATFORM_PUBLISHING_WORKER_DATABASE_PASSWORD",
        "PLATFORM_DOWNLOAD_GATEWAY_WORKER_DATABASE_PASSWORD",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "RELAY_BACKENDS",
        "RELAY_BASE_URL",
        "RELAY_CLIENT_ID",
        "RELAY_API_KEY",
        "RELAY_LEGACY_COMPATIBILITY_ENABLED",
        "RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE",
        "RELAY_TENANT_ID",
        "RELAY_OPERATIONS_TOKEN",
        "RELAY_RECONCILIATION_APPROVAL_KEY_ID",
        "RELAY_RECONCILIATION_APPROVAL_SECRET",
        "RELAY_CALLBACK_SIGNING_SECRET",
        "RELAY_CALLBACK_SIGNING_SECRETS",
        "INTERNAL_SERVICE_TOKEN",
        "DOWNLOAD_EDGE_COMPLETION_SERVICE_TOKEN",
        "CHANNEL_COST_SIGNING_SECRET",
        "RELAY_TELEMETRY_SIGNING_SECRET",
        "PROVIDER_ALERT_SIGNING_SECRET",
        "PROVIDER_ALERT_FORWARD_SIGNING_SECRET",
        "DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET",
        "DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET",
        "DOWNLOAD_GATEWAY_SERVICE_TOKEN",
        "DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET",
        "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64",
        "JWT_SIGNING_SECRET",
        "INPUT_ASSET_SIGNING_SECRET",
        "HUAWEI_OBS_ACCESS_KEY_ID",
        "HUAWEI_OBS_SECRET_ACCESS_KEY",
        "HUAWEI_OBS_SECURITY_TOKEN",
        "PUBLISHING_PLUGIN_CREDENTIALS",
        "BOOTSTRAP_TOKEN",
    }
)

# Browser authentication belongs only to the public Platform API. Presence is
# rejected even for empty values on every other protected process before any
# protected file, CA, receipt, release proof, or database source is read.
PLATFORM_BROWSER_AUTH_ENVIRONMENTS = frozenset(
    {
        "JWT_SIGNING_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "AUTH_LEGACY_BEARER_ENABLED",
        "OIDC_ENABLED",
        "OIDC_SELF_SIGNUP_ENABLED",
        "OIDC_ISSUER",
        "OIDC_AUTHORIZATION_ENDPOINT",
        "OIDC_TOKEN_ENDPOINT",
        "OIDC_JWKS_URI",
        "OIDC_CLIENT_ID",
        "OIDC_REDIRECT_URI",
        "FRONTEND_ORIGIN",
        "ACCOUNT_MANAGEMENT_URL",
        "AUTH_SESSION_TTL_SECONDS",
        "AUTH_SESSION_IDLE_TTL_SECONDS",
        "AUTH_ACCOUNT_STEP_UP_MAX_AGE_SECONDS",
        "OIDC_LOGIN_TRANSACTION_TTL_SECONDS",
        "OIDC_ID_TOKEN_MAX_LIFETIME_SECONDS",
        "OIDC_LOGIN_IP_WINDOW_SECONDS",
        "OIDC_LOGIN_IP_MAX_ATTEMPTS",
        "INVITATION_TTL_SECONDS",
        "PLATFORM_OWNER_USER_IDS",
        "PLATFORM_ADMIN_REQUIRED_AMR",
        "PLATFORM_ADMIN_STEP_UP_MAX_AGE_SECONDS",
    }
)

_IDENTITY_FIELDS = frozenset(
    {
        "relay_tenant_id",
        "relay_reconciliation_approval_key_id",
        "huawei_obs_access_key_id",
    }
)
_PLUGIN_SPEC = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
_PLUGIN_CREDENTIAL_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_BACKEND_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_CONTRACT_REVISION = re.compile(r"^[a-z][a-z0-9._-]{0,79}$")
_PLACEHOLDER = re.compile(
    r"^(?:change[-_]?me|default|example|placeholder|replace[-_]?with|secret|test|todo|token|your[-_])",
    re.IGNORECASE,
)
_DATABASE_PASSWORD = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class PlatformProcessSecretError(RuntimeError):
    """A deliberately value-free protected Platform bootstrap failure."""


def _invalid(message: str = "Platform process runtime secret file is invalid") -> None:
    raise PlatformProcessSecretError(message)


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _strict_json_document(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _invalid()
    if not text or text.startswith("\ufeff"):
        _invalid()
    decoder = json.JSONDecoder(object_pairs_hook=_reject_duplicate_object)
    try:
        document, end = decoder.raw_decode(text)
    except (json.JSONDecodeError, PlatformProcessSecretError):
        _invalid()
    # Compact rendering is intentional: whitespace before/after the one JSON
    # document creates an ambiguous file commitment and is rejected.
    if end != len(text) or not isinstance(document, dict):
        _invalid()
    return document


def _clean_string(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _invalid()
    if (not value and not allow_empty) or value != value.strip():
        _invalid()
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        _invalid()
    return value


def _secret(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    normalized = _clean_string(value)
    encoded = normalized.encode("utf-8")
    if (
        len(normalized) > 16384
        or len(encoded) > 16384
        or len(encoded) < _MINIMUM_SECRET_BYTES
        or len(set(encoded)) < 8
        or any(
            all(
                encoded[index] == encoded[index % period]
                for index in range(period, len(encoded))
            )
            for period in range(1, min(16, len(encoded) // 2) + 1)
        )
        or _PLACEHOLDER.match(normalized)
    ):
        _invalid()
    return normalized


def _database_password(value: Any) -> str:
    normalized = _secret(value)
    if not isinstance(normalized, str) or not _DATABASE_PASSWORD.fullmatch(normalized):
        _invalid()
    return normalized


def _client_id(value: Any) -> str:
    normalized = _clean_string(value)
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", normalized) is None
        or _PLACEHOLDER.match(normalized)
    ):
        _invalid()
    return normalized


def _relay_base_url(value: Any) -> str:
    normalized = _clean_string(value)
    if len(normalized) > 2048:
        _invalid()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        _invalid()
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        _invalid()
    return normalized


def _database_ca_source_path() -> str:
    path = os.environ.get(PLATFORM_DATABASE_CA_FILE_ENV, "")
    if (
        not path
        or path != path.strip()
        or "\x00" in path
        or "\r" in path
        or "\n" in path
        or not path.startswith("/")
        or posixpath.normpath(path) != path
    ):
        _invalid("Platform database CA file path is invalid")
    return path


def _database_target(value: str, *, database_override: str | None = None) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        query = dict(
            parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        )
    except ValueError:
        _invalid()
    host = (parsed.hostname or "").rstrip(".").lower()
    database = parsed.path.removeprefix("/")
    if database_override is not None:
        database = database_override
    if (
        not host
        or parsed.hostname != host
        or port is None
        or not 1 <= port <= 65535
        or parsed.path.count("/") != 1
        or "%" in parsed.path
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", database) is None
        or query.get("sslmode") != "verify-full"
        or query.get("sslrootcert") != _database_ca_source_path()
    ):
        _invalid()
    return (
        "postgres-target-v1\n"
        f"host={host}\n"
        f"port={port}\n"
        f"database={database}\n"
        "sslmode=verify-full\n"
        f"sslrootcert={query['sslrootcert']}"
    )


def _database_endpoint(value: str, *, database_override: str | None = None) -> str:
    """Return the CA-path-independent endpoint committed by the global gate."""

    # Reuse the strict target parser first so the endpoint projection cannot
    # accept a DSN which the actual consumer would reject.
    _database_target(value, database_override=database_override)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    database = (
        database_override
        if database_override is not None
        else parsed.path.removeprefix("/")
    )
    return (
        "postgres-endpoint-v1\n"
        f"host={host}\n"
        f"port={parsed.port}\n"
        f"database={database}"
    )


def _database_url(value: Any, *, process_role: str) -> str:
    normalized = _clean_string(value)
    if not 40 <= len(normalized) <= 8192:
        _invalid()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        _invalid()
    password = unquote(parsed.password or "")
    try:
        query_items = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        _invalid()
    expected_username = PLATFORM_DATABASE_ROLE_BY_PROCESS[process_role]
    expected_query = urlencode(
        (
            ("sslmode", "verify-full"),
            ("sslrootcert", _database_ca_source_path()),
        )
    )
    if (
        parsed.scheme != "postgresql+psycopg"
        or not parsed.hostname
        or port is None
        or parsed.username != expected_username
        or not password
        or parsed.path in {"", "/"}
        or parsed.fragment
        or query_items
        != [
            ("sslmode", "verify-full"),
            ("sslrootcert", _database_ca_source_path()),
        ]
        or parsed.query != expected_query
    ):
        _invalid()
    _database_password(password)
    _database_target(normalized)
    return normalized


def _relay_backends(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or set(value) != {NEW_API_RELAY_BACKEND_ID}:
        _invalid()
    result: dict[str, dict[str, str]] = {}
    for backend_id, item in value.items():
        if (
            not isinstance(backend_id, str)
            or not _BACKEND_ID.fullmatch(backend_id)
            or backend_id == LEGACY_RELAY_BACKEND_ID
        ):
            _invalid()
        if not isinstance(item, dict) or set(item) != {
            "base_url",
            "client_id",
            "api_key",
            "contract_revision",
        }:
            _invalid()
        result[backend_id] = {
            "base_url": _relay_base_url(item["base_url"]),
            "client_id": _client_id(item["client_id"]),
            "api_key": _secret(item["api_key"]),
            "contract_revision": _clean_string(item["contract_revision"]),
        }
        if not _CONTRACT_REVISION.fullmatch(result[backend_id]["contract_revision"]):
            _invalid()
        if result[backend_id]["contract_revision"] != NEW_API_RELAY_CONTRACT_REVISION:
            _invalid()
    return result


def _callback_secrets(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {NEW_API_RELAY_BACKEND_ID}:
        _invalid()
    result: dict[str, str] = {}
    for backend_id, secret in value.items():
        if (
            not isinstance(backend_id, str)
            or not _BACKEND_ID.fullmatch(backend_id)
            or backend_id == LEGACY_RELAY_BACKEND_ID
        ):
            _invalid()
        result[backend_id] = _secret(secret)
    return result


def _plugin_credentials(
    value: Any, *, role: str
) -> dict[str, dict[str, dict[str, str]]]:
    expected_sections = (
        {"adapters", "media_resolvers"} if role == "publishing-worker" else {"adapters"}
    )
    if not isinstance(value, dict) or set(value) != expected_sections:
        _invalid()
    result: dict[str, dict[str, dict[str, str]]] = {}
    for section in sorted(expected_sections):
        entries = value[section]
        if not isinstance(entries, dict) or len(entries) > 32:
            _invalid()
        normalized_entries: dict[str, dict[str, str]] = {}
        for spec, credentials in entries.items():
            if (
                not isinstance(spec, str)
                or len(spec) > 256
                or not _PLUGIN_SPEC.fullmatch(spec)
            ):
                _invalid()
            if not isinstance(credentials, dict) or len(credentials) > 64:
                _invalid()
            normalized_credentials: dict[str, str] = {}
            for name, secret in credentials.items():
                if not isinstance(name, str) or not _PLUGIN_CREDENTIAL_NAME.fullmatch(
                    name
                ):
                    _invalid()
                normalized_credentials[name] = _secret(secret)
            normalized_entries[spec] = normalized_credentials
        result[section] = normalized_entries
    return result


def _canonical_aes_key(value: Any) -> str:
    normalized = _clean_string(value)
    try:
        decoded = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError):
        _invalid()
    if (
        len(decoded) != 32
        or len(set(decoded)) < 16
        or decoded in {bytes(32), bytes(range(32))}
        or base64.b64encode(decoded).decode("ascii") != normalized
    ):
        _invalid()
    return normalized


def _identity(name: str, value: Any) -> str:
    normalized = _clean_string(value)
    if name == "relay_tenant_id":
        try:
            if str(UUID(normalized)) != normalized or UUID(normalized).int == 0:
                _invalid()
        except ValueError:
            _invalid()
    elif name == "relay_reconciliation_approval_key_id":
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", normalized) is None:
            _invalid()
    elif name == "huawei_obs_access_key_id":
        if (
            len(normalized) > 128
            or any(character.isspace() for character in normalized)
            or _PLACEHOLDER.match(normalized)
        ):
            _invalid()
    elif any(character.isspace() for character in normalized):
        _invalid()
    return normalized


PLATFORM_SECRET_ISOLATION_CONSUMER_BY_PROCESS = MappingProxyType(
    {
        "migration": "platform-migration",
        "platform-api": "platform-api",
        "dispatcher": "platform-dispatcher",
        "relay-sync": "platform-relay-sync",
        "timeout-worker": "platform-timeout-worker",
        "publishing-worker": "platform-publishing-worker",
        "download-gateway-registration-worker": (
            "platform-download-gateway-registration-worker"
        ),
    }
)

PLATFORM_SECRET_ISOLATION_FILE_ID_BY_PROCESS = MappingProxyType(
    {
        "migration": "platform_migration_runtime",
        "platform-api": "platform_api_runtime",
        "dispatcher": "platform_dispatcher_runtime",
        "relay-sync": "platform_relay_sync_runtime",
        "timeout-worker": "platform_timeout_worker_runtime",
        "publishing-worker": "platform_publishing_worker_runtime",
        "download-gateway-registration-worker": (
            "platform_download_gateway_worker_runtime"
        ),
    }
)

PLATFORM_SECRET_ISOLATION_PREFIX_BY_PROCESS = MappingProxyType(
    {
        "migration": "platform.migration",
        "platform-api": "platform.api",
        "dispatcher": "platform.dispatcher",
        "relay-sync": "platform.relay_sync",
        "timeout-worker": "platform.timeout_worker",
        "publishing-worker": "platform.publishing_worker",
        "download-gateway-registration-worker": (
            "platform.download_gateway_worker"
        ),
    }
)

_PLATFORM_API_COMMITTED_SECRET_FIELDS = (
    "relay_operations_token",
    "relay_reconciliation_approval_secret",
    "internal_service_token",
    "download_edge_completion_service_token",
    "channel_cost_signing_secret",
    "relay_telemetry_signing_secret",
    "provider_alert_signing_secret",
    "provider_alert_forward_signing_secret",
    "download_completion_edge_gateway_signing_secret",
    "download_completion_obs_access_log_signing_secret",
    "download_gateway_service_token",
    "download_gateway_registration_signing_secret",
    "jwt_signing_secret",
)


def platform_process_secret_semantic_commitments(
    normalized: Mapping[str, Any], expected_role: str
) -> tuple[dict[str, str], ...]:
    """Project one parsed snapshot into the validator's value-free contract."""

    if (
        expected_role not in PLATFORM_PROCESS_ROLES
        or normalized.get("process_role") != expected_role
    ):
        _invalid()
    prefix = PLATFORM_SECRET_ISOLATION_PREFIX_BY_PROCESS[expected_role]
    commitments: list[dict[str, str]] = []

    def add(identifier: str, value: str | bytes) -> None:
        encoded = value if isinstance(value, bytes) else value.encode("utf-8")
        commitments.append(
            {
                "id": f"{prefix}.{identifier}",
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )

    add(
        "database.password",
        unquote(urlsplit(normalized["database_url"]).password or ""),
    )
    add("database.target", _database_target(normalized["database_url"]))
    add("database.endpoint", _database_endpoint(normalized["database_url"]))
    relay_backends = normalized.get("relay_backends")
    if relay_backends is not None:
        for backend_id in sorted(relay_backends):
            add(
                f"relay_backend.{backend_id}.api_key",
                relay_backends[backend_id]["api_key"],
            )

    if expected_role == "platform-api":
        for name in _PLATFORM_API_COMMITTED_SECRET_FIELDS:
            add(name, normalized[name])
        for backend_id in sorted(normalized["relay_callback_signing_secrets"]):
            add(
                f"callback.{backend_id}",
                normalized["relay_callback_signing_secrets"][backend_id],
            )

    if expected_role in {"platform-api", "dispatcher"}:
        add("obs.access_key_id", normalized["huawei_obs_access_key_id"])
        add("obs.secret_access_key", normalized["huawei_obs_secret_access_key"])
        security_token = normalized["huawei_obs_security_token"]
        if security_token is not None:
            add("obs.security_token", security_token)

    if expected_role in {
        "platform-api",
        "download-gateway-registration-worker",
    }:
        if expected_role == "download-gateway-registration-worker":
            add(
                "download_gateway_service_token",
                normalized["download_gateway_service_token"],
            )
            add(
                "download_gateway_registration_signing_secret",
                normalized["download_gateway_registration_signing_secret"],
            )
        encoded = normalized["download_gateway_attempt_encryption_key_base64"]
        add("download_gateway_attempt_encryption_key.encoded", encoded)
        add(
            "download_gateway_attempt_encryption_key.decoded",
            base64.b64decode(encoded, validate=True),
        )

    plugin_credentials = normalized.get("publishing_plugin_credentials")
    if plugin_credentials is not None:
        for section in sorted(plugin_credentials):
            for spec in sorted(plugin_credentials[section]):
                credentials = plugin_credentials[section][spec]
                for name in sorted(credentials):
                    add(
                        f"publishing_plugin.{section}.{spec}.{name}",
                        credentials[name],
                    )

    commitments.sort(key=lambda item: item["id"])
    if len({item["id"] for item in commitments}) != len(commitments):
        _invalid()
    return tuple(commitments)


def parse_platform_process_secret_document(
    raw: bytes, expected_role: str
) -> dict[str, Any]:
    if (
        expected_role not in PLATFORM_PROCESS_ROLES
        or not raw
        or len(raw) > _MAXIMUM_SECRET_FILE_BYTES
    ):
        _invalid()
    document = _strict_json_document(raw)
    if set(document) != {"kind", "schema_version", "process_role", "secrets"}:
        _invalid()
    if (
        document["kind"] != PLATFORM_PROCESS_SECRET_KIND
        or type(document["schema_version"]) is not int
        or document["schema_version"] != PLATFORM_PROCESS_SECRET_SCHEMA_VERSION
        or document["process_role"] != expected_role
        or not isinstance(document["secrets"], dict)
    ):
        _invalid()
    secrets = document["secrets"]
    if set(secrets) != _PROCESS_SECRET_FIELDS[expected_role]:
        _invalid()

    normalized: dict[str, Any] = {"process_role": expected_role}
    for name, value in secrets.items():
        if name == "database_url":
            normalized[name] = _database_url(value, process_role=expected_role)
        elif name == "relay_backends":
            normalized[name] = _relay_backends(value)
        elif name == "relay_callback_signing_secrets":
            normalized[name] = _callback_secrets(value)
        elif name == "publishing_plugin_credentials":
            normalized[name] = _plugin_credentials(value, role=expected_role)
        elif name == "download_gateway_attempt_encryption_key_base64":
            normalized[name] = _canonical_aes_key(value)
        elif name in _IDENTITY_FIELDS:
            normalized[name] = _identity(name, value)
        elif name == "huawei_obs_security_token":
            normalized[name] = _secret(value, allow_none=True)
        else:
            normalized[name] = _secret(value)

    # Catch accidental reuse inside one process bundle. Identity values and the
    # encoded AES representation are not compared as raw secret material.
    secret_values: list[str] = []
    for name, value in normalized.items():
        if name == "database_url":
            secret_values.append(
                unquote(urlsplit(value).password or "")
            )
            continue
        if name in {"process_role", *_IDENTITY_FIELDS}:
            continue
        if name == "relay_backends":
            secret_values.extend(item["api_key"] for item in value.values())
        elif name in {"relay_callback_signing_secrets"}:
            secret_values.extend(value.values())
        elif name == "publishing_plugin_credentials":
            secret_values.extend(
                secret
                for section in value.values()
                for credentials in section.values()
                for secret in credentials.values()
            )
        elif name == "download_gateway_attempt_encryption_key_base64":
            secret_values.append(base64.b64decode(value).decode("latin1"))
        elif value is not None:
            secret_values.append(value)
    if len(secret_values) != len(set(secret_values)):
        _invalid()
    return normalized


def _protected_file_read_only(file_descriptor: int) -> bool:
    if not sys.platform.startswith("linux"):
        return True
    flags = os.fstatvfs(file_descriptor).f_flag
    return bool(flags & getattr(os, "ST_RDONLY", 1))


def _read_protected_platform_source(
    *,
    environment: str,
    label: str,
    maximum_bytes: int,
) -> bytes:
    path = os.environ.get(environment, "")
    if (
        not path
        or path != path.strip()
        or "\x00" in path
        or "\r" in path
        or "\n" in path
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
    ):
        _invalid(f"Platform {label} file path is invalid")
    try:
        before = os.lstat(path)
    except OSError:
        _invalid(f"Platform {label} file is unavailable")
    mode = stat.S_IMODE(before.st_mode)
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else before.st_uid
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size < 1
        or before.st_size > maximum_bytes
        or mode not in {0o400, 0o600}
        or before.st_uid != effective_uid
    ):
        _invalid(f"Platform {label} file protection is invalid")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _invalid(f"Platform {label} file could not be read")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or stat.S_IMODE(opened.st_mode) not in {0o400, 0o600}
            or opened.st_uid != effective_uid
            or opened.st_size < 1
            or opened.st_size > maximum_bytes
            or not _protected_file_read_only(descriptor)
        ):
            _invalid(f"Platform {label} file protection is invalid")
        try:
            writable = os.open(
                path,
                os.O_WRONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            writable = None
        if writable is not None:
            os.close(writable)
            _invalid(f"Platform {label} file mount is writable")
        during = os.lstat(path)
        if (during.st_dev, during.st_ino) != (opened.st_dev, opened.st_ino):
            _invalid(f"Platform {label} file changed during validation")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _invalid(f"Platform {label} file could not be read")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != opened.st_size:
            _invalid(f"Platform {label} file could not be read")
        return raw
    finally:
        os.close(descriptor)


def read_protected_platform_process_secret_file() -> bytes:
    return _read_protected_platform_source(
        environment=PLATFORM_PROCESS_SECRET_FILE_ENV,
        label="process runtime secret",
        maximum_bytes=_MAXIMUM_SECRET_FILE_BYTES,
    )


def read_protected_platform_database_ca_file() -> bytes:
    raw = _read_protected_platform_source(
        environment=PLATFORM_DATABASE_CA_FILE_ENV,
        label="database CA",
        maximum_bytes=_MAXIMUM_DATABASE_CA_BYTES,
    )
    validate_platform_database_ca(raw)
    return raw


def validate_platform_database_ca(raw: bytes) -> None:
    if (
        not raw
        or len(raw) > _MAXIMUM_DATABASE_CA_BYTES
        or not raw.endswith(b"\n")
        or b"\r" in raw
    ):
        _invalid("Platform database CA file is invalid")
    try:
        certificates = x509.load_pem_x509_certificates(raw)
    except ValueError:
        _invalid("Platform database CA file is invalid")
    if (
        not certificates
        or b"".join(
            certificate.public_bytes(Encoding.PEM)
            for certificate in certificates
        )
        != raw
    ):
        _invalid("Platform database CA file is invalid")


def _private_snapshot_directory_is_tmpfs(path: str) -> bool:
    if not sys.platform.startswith("linux"):
        return True
    try:
        mountinfo = open("/proc/self/mountinfo", "r", encoding="utf-8")
    except OSError:
        return False
    with mountinfo:
        for line in mountinfo:
            before, separator, after = line.rstrip("\n").partition(" - ")
            if not separator:
                continue
            fields = before.split(" ")
            filesystem_fields = after.split(" ")
            if (
                len(fields) >= 6
                and len(filesystem_fields) >= 1
                and fields[4] == path
                and filesystem_fields[0] == "tmpfs"
            ):
                return True
    return False


def materialize_verified_platform_database_ca(
    raw: bytes,
    *,
    directory: str = PLATFORM_DATABASE_CA_SNAPSHOT_DIRECTORY,
    require_tmpfs: bool = True,
) -> str:
    """Materialize already-committed CA bytes without reopening the bind source."""

    digest = hashlib.sha256(raw).hexdigest()
    if (
        sys.platform.startswith("linux")
        and fcntl is not None
        and hasattr(os, "memfd_create")
    ):
        existing = _SEALED_DATABASE_CA_DESCRIPTORS.get(digest)
        if existing is not None:
            return f"/proc/self/fd/{existing}"
        try:
            descriptor = os.memfd_create(
                "platform-database-ca-" + digest,
                getattr(os, "MFD_CLOEXEC", 0x0001)
                | getattr(os, "MFD_ALLOW_SEALING", 0x0002),
            )
            os.fchmod(descriptor, 0o400)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written < 1:
                    raise OSError
                offset += written
            os.fsync(descriptor)
            seals = (
                getattr(fcntl, "F_SEAL_SEAL", 0x0001)
                | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
                | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                | getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            )
            fcntl.fcntl(
                descriptor,
                getattr(fcntl, "F_ADD_SEALS", 1033),
                seals,
            )
            actual_seals = fcntl.fcntl(
                descriptor,
                getattr(fcntl, "F_GET_SEALS", 1034),
            )
            opened = os.fstat(descriptor)
            if (
                actual_seals & seals != seals
                or opened.st_size != len(raw)
                or stat.S_IMODE(opened.st_mode) != 0o400
            ):
                raise OSError
        except OSError:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            _invalid("Platform database CA sealed snapshot could not be created")
        _SEALED_DATABASE_CA_DESCRIPTORS[digest] = descriptor
        return f"/proc/self/fd/{descriptor}"

    if (
        not directory
        or not os.path.isabs(directory)
        or os.path.normpath(directory) != directory
        or os.path.realpath(directory) != directory
    ):
        _invalid("Platform database CA snapshot directory is invalid")
    try:
        before = os.lstat(directory)
    except OSError:
        _invalid("Platform database CA snapshot directory is unavailable")
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else before.st_uid
    private_mode = (
        stat.S_IMODE(before.st_mode) == 0o700
        if sys.platform.startswith("linux")
        else True
    )
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_uid != effective_uid
        or not private_mode
        or (require_tmpfs and not _private_snapshot_directory_is_tmpfs(directory))
    ):
        _invalid("Platform database CA snapshot directory protection is invalid")
    name = "platform-database-ca-" + hashlib.sha256(raw).hexdigest() + ".pem"
    path = os.path.join(directory, name)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o400)
    except FileExistsError:
        _invalid("Platform database CA snapshot already exists")
    except OSError:
        _invalid("Platform database CA snapshot could not be created")
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written < 1:
                _invalid("Platform database CA snapshot could not be created")
            offset += written
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != effective_uid
            or (
                sys.platform.startswith("linux")
                and stat.S_IMODE(opened.st_mode) != 0o400
            )
            or opened.st_size != len(raw)
        ):
            _invalid("Platform database CA snapshot protection is invalid")
    finally:
        os.close(descriptor)
    after = os.lstat(path)
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_uid != effective_uid
        or (
            sys.platform.startswith("linux")
            and stat.S_IMODE(after.st_mode) != 0o400
        )
        or after.st_size != len(raw)
    ):
        _invalid("Platform database CA snapshot protection is invalid")
    return path


def rewrite_platform_database_url_ca_path(
    database_url: str,
    *,
    snapshot_path: str,
) -> str:
    parsed = urlsplit(database_url)
    query = [
        ("sslmode", "verify-full"),
        ("sslrootcert", snapshot_path),
    ]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            "",
        )
    )


def protected_platform_runtime_requested() -> bool:
    outer_environment = os.environ.get("ENVIRONMENT", "")
    outer_environment_is_protected = outer_environment in {
        "production",
        "staging",
    }
    if (
        not outer_environment_is_protected
        and outer_environment.strip().lower() in {"production", "staging"}
    ):
        # Reject aliases before any bundle/receipt/CA/proof source is opened.
        raise PlatformProcessSecretError("ENVIRONMENT is invalid")
    explicit = os.environ.get(PLATFORM_PROTECTED_RUNTIME_ENV)
    if outer_environment_is_protected:
        if explicit is not None and explicit != "true":
            raise PlatformProcessSecretError(
                "Protected Platform runtime cannot be disabled"
            )
        return True
    if explicit is None or explicit == "false":
        return False
    if explicit == "true":
        return True
    raise PlatformProcessSecretError("PLATFORM_PROTECTED_RUNTIME is invalid")


def reject_raw_platform_secret_environment() -> None:
    # libpq accepts a broad and evolving PG* environment namespace, including
    # alternate passwords/services, TLS client keys and connection options.
    # Reject the namespace as a prefix so a future driver variable cannot
    # silently bypass the typed DSN/committed CA contract.
    if any(name.casefold().startswith("pg") for name in os.environ):
        raise PlatformProcessSecretError(
            "Protected Platform runtime rejects libpq environment variables"
        )
    present = sorted(
        {
            name.upper()
            for name in os.environ
            if name.upper() in RAW_PLATFORM_SECRET_ENVIRONMENTS
        }
    )
    if present:
        # Never echo values. Names are server-owned and safe diagnostics.
        raise PlatformProcessSecretError(
            "Protected Platform runtime rejects raw secret environment variable(s): "
            + ", ".join(present)
        )


def reject_cross_role_browser_auth_environment(role: str) -> None:
    if role == "platform-api":
        return
    present = sorted(
        {
            name.upper()
            for name in os.environ
            if name.upper() in PLATFORM_BROWSER_AUTH_ENVIRONMENTS
        }
    )
    if present:
        raise PlatformProcessSecretError(
            "Protected non-API process rejects browser authentication "
            "environment variable(s): "
            + ", ".join(present)
        )


def load_platform_process_secret_settings() -> dict[str, Any]:
    """Load one immutable role snapshot before Settings touches other sources."""

    if not protected_platform_runtime_requested():
        return {}
    role = os.environ.get(PLATFORM_PROCESS_ROLE_ENV, "")
    if role not in PLATFORM_PROCESS_ROLES:
        # Preserve value-free raw-secret diagnostics even when an older
        # staging launcher omitted the role. This still happens before every
        # protected source read.
        reject_raw_platform_secret_environment()
        raise PlatformProcessSecretError("PLATFORM_PROCESS_ROLE is invalid")
    reject_cross_role_browser_auth_environment(role)
    reject_raw_platform_secret_environment()
    raw = read_protected_platform_process_secret_file()
    database_ca_raw = read_protected_platform_database_ca_file()
    normalized = parse_platform_process_secret_document(raw, role)
    semantics = platform_process_secret_semantic_commitments(normalized, role)
    try:
        # Imported only after raw-env rejection and the one immutable bundle read,
        # avoiding a second source read and keeping the parser independently usable.
        from .platform_secret_receipt import (
            verify_platform_secret_isolation_receipt_sources,
        )

        isolation_context = verify_platform_secret_isolation_receipt_sources(
            consumer=PLATFORM_SECRET_ISOLATION_CONSUMER_BY_PROCESS[role],
            files={
                PLATFORM_SECRET_ISOLATION_FILE_ID_BY_PROCESS[role]: raw,
                PLATFORM_DATABASE_CA_FILE_ID: database_ca_raw,
            },
            semantics=semantics,
        )
        endpoint_commitments = tuple(
            item["sha256"]
            for item in semantics
            if item["id"].endswith(".database.endpoint")
        )
        if len(endpoint_commitments) != 1:
            raise PlatformProcessSecretError(
                "Protected Platform database endpoint commitment is invalid"
            )
        from .platform_database_release_proof import (
            load_and_install_platform_database_release_proof,
        )

        load_and_install_platform_database_release_proof(
            isolation=isolation_context,
            database_endpoint_sha256=endpoint_commitments[0],
        )
    except PlatformProcessSecretError:
        raise
    except RuntimeError as exc:
        raise PlatformProcessSecretError(str(exc)) from None
    snapshot_path = materialize_verified_platform_database_ca(database_ca_raw)
    normalized["database_url"] = rewrite_platform_database_url_ca_path(
        normalized["database_url"],
        snapshot_path=snapshot_path,
    )
    return normalized


def immutable_plugin_credentials(
    manifest: Mapping[str, Mapping[str, Mapping[str, str]]],
    section: str,
    spec: str,
) -> Mapping[str, str]:
    entries = manifest.get(section)
    if entries is None or spec not in entries:
        raise PlatformProcessSecretError(
            "Production publishing plug-in credential manifest is incomplete"
        )
    return MappingProxyType(dict(entries[spec]))
