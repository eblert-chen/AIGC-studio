from __future__ import annotations

import base64
import binascii
from functools import lru_cache
import hmac
import ipaddress
import os
import re
from typing import Literal, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import tldextract

from .relay_backends import (
    DEFAULT_RELAY_CONTRACT_REVISION,
    LEGACY_RELAY_BACKEND_ID,
    NEW_API_RELAY_BACKEND_ID,
    NEW_API_RELAY_CONTRACT_REVISION,
    RELAY_BACKEND_ID_PATTERN,
    RELAY_CONTRACT_REVISION_PATTERN,
    RelayBackendConfiguration,
)
from .process_secrets import (
    PLATFORM_PROCESS_ROLES,
    immutable_plugin_credentials,
    load_platform_process_secret_settings,
    protected_platform_runtime_requested,
)

_PRODUCTION_DATABASE_SCHEME = "postgresql+psycopg"
_KNOWN_PLACEHOLDER_VALUES = {
    "change-me",
    "changeme",
    "client",
    "client-id",
    "default",
    "example",
    "example-client",
    "placeholder",
    "relay-client",
    "replace-me",
    "secret",
    "test",
    "todo",
    "token",
    "your-client-id",
    "your-secret",
}


def runtime_settings_are_protected(settings: object) -> bool:
    """Resolve the protected boundary without allowing staging to downgrade.

    Small dependency tests use settings-like objects rather than ``Settings``;
    keeping this resolver public makes those call sites preserve the same
    staging/production semantics without relying on a particular concrete
    settings class.
    """

    environment = getattr(settings, "environment", "")
    if environment in {"production", "staging"}:
        return True
    configured = getattr(settings, "protected_runtime", None)
    if configured is not None:
        return bool(configured)
    return protected_platform_runtime_requested()
_KNOWN_PLACEHOLDER_PREFIXES = (
    "change-me-",
    "default-",
    "example-",
    "placeholder-",
    "replace-me-",
    "replace-with-",
    "secret-",
    "test-",
    "your-",
)
_MINIMUM_PRODUCTION_SECRET_BYTES = 32
PHISHING_RESISTANT_PLATFORM_ADMIN_AMR = frozenset(
    {"fido", "hwk", "passkey", "webauthn"}
)
_OFFLINE_PUBLIC_SUFFIX_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


def _is_known_placeholder(value: str) -> bool:
    normalized = value.strip().lower().replace("_", "-")
    return normalized in _KNOWN_PLACEHOLDER_VALUES or normalized.startswith(
        _KNOWN_PLACEHOLDER_PREFIXES
    )


def _validate_https_origin(origin: str) -> bool:
    if not origin or origin != origin.strip() or "*" in origin:
        return False
    try:
        parsed = urlsplit(origin)
        # Accessing port also rejects malformed values such as ":not-a-port".
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _validate_https_url(value: str) -> bool:
    if not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _is_official_huawei_obs_endpoint(value: str) -> bool:
    if not _validate_https_url(value):
        return False
    host = (urlsplit(value).hostname or "").lower().rstrip(".")
    return host.startswith("obs.") and host.endswith(
        (".myhuaweicloud.com", ".myhuaweicloud.cn")
    )


def _validate_http_root_url(value: str) -> bool:
    if not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _canonical_http_root_origin(value: str) -> str:
    """Return one comparison form for an already validated HTTP root URL."""

    parsed = urlsplit(value)
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    authority = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    if parsed.port is not None and parsed.port != default_port:
        authority = f"{authority}:{parsed.port}"
    return f"{scheme}://{authority}"


def _validate_publishing_oauth_url(value: str, *, production: bool) -> bool:
    if not value or value != value.strip() or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    if production:
        return parsed.scheme == "https" and not _is_loopback_hostname(hostname)
    return parsed.scheme == "https" or _is_loopback_hostname(hostname)


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_dns_hostname(hostname: str) -> bool:
    """Accept an ASCII DNS name without assuming it resolves publicly.

    The native Relay console can intentionally live on a private operations
    network.  Production therefore rejects IP literals and localhost, but does
    not perform DNS resolution or require a public suffix.
    """

    if len(hostname) > 253 or hostname.endswith("."):
        return False
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError:
        return False
    labels = hostname.split(".")
    return bool(labels) and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    )


def _schemeful_site(value: str) -> tuple[str, str] | None:
    """Return the browser-style schemeful site without network or cache I/O.

    Private PSL entries are part of the browser boundary: two unrelated
    ``github.io`` tenants must never be treated as same-site.  For an unknown
    suffix, the PSL prevailing ``*`` rule makes the last label the public
    suffix, so the final two labels are the registrable site.  Protected
    browser authentication rejects IP literals and single-label hosts.
    """

    if not value or value != value.strip() or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return None
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _is_dns_hostname(ascii_hostname):
        return None

    extracted = _OFFLINE_PUBLIC_SUFFIX_EXTRACTOR(ascii_hostname)
    registrable = extracted.top_domain_under_public_suffix
    if not registrable:
        if extracted.suffix:
            # The host itself is only a public suffix and has no registrable
            # site that may own a production BFF cookie.
            return None
        labels = ascii_hostname.split(".")
        if len(labels) < 2:
            return None
        registrable = ".".join(labels[-2:])
    return scheme, registrable.casefold()


def _browser_origins_share_schemeful_site(
    frontend_origin: str | None,
    platform_url: str | None,
) -> bool:
    if frontend_origin is None or platform_url is None:
        return False
    frontend_site = _schemeful_site(frontend_origin)
    platform_site = _schemeful_site(platform_url)
    return frontend_site is not None and frontend_site == platform_site


def _validate_protected_platform_api_browser_site_environment() -> None:
    """Fail before protected secret, CA, proof, or database source reads."""

    if not protected_platform_runtime_requested():
        return
    if os.environ.get("PLATFORM_PROCESS_ROLE") != "platform-api":
        return
    frontend_origin = os.environ.get("FRONTEND_ORIGIN")
    platform_origin = os.environ.get("INPUT_ASSET_PUBLIC_BASE_URL")
    oidc_redirect_uri = os.environ.get("OIDC_REDIRECT_URI")
    if frontend_origin is None or not _validate_https_origin(frontend_origin):
        raise RuntimeError(
            "Protected FRONTEND_ORIGIN must be a fixed credential-free HTTPS origin"
        )
    if platform_origin is None or not _validate_https_origin(platform_origin):
        raise RuntimeError(
            "Protected Platform public origin must be a fixed credential-free "
            "HTTPS origin"
        )
    if oidc_redirect_uri is None or not _validate_publishing_oauth_url(
        oidc_redirect_uri,
        production=True,
    ):
        raise RuntimeError(
            "Protected OIDC_REDIRECT_URI must be a fixed credential-free HTTPS URL"
        )
    redirect = urlsplit(oidc_redirect_uri)
    public_origin = urlsplit(platform_origin)
    if (
        redirect.path != "/api/v1/auth/callback"
        or (redirect.scheme, redirect.netloc)
        != (public_origin.scheme, public_origin.netloc)
    ):
        raise RuntimeError(
            "Protected OIDC_REDIRECT_URI must use the canonical Platform public origin"
        )
    if not _browser_origins_share_schemeful_site(
        frontend_origin,
        platform_origin,
    ):
        raise RuntimeError(
            "Protected Platform browser origins must share one schemeful site"
        )


def _normalize_relay_native_admin_console_origin(
    value: str,
    *,
    production: bool,
) -> str:
    """Validate and canonicalize the server-owned new-api console origin."""

    if (
        not value
        or value != value.strip()
        or "\\" in value
        or "%" in value
        or "?" in value
        or "#" in value
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(
            "RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN must be a credential-free HTTP(S) origin"
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN must be a credential-free HTTP(S) origin"
        ) from exc

    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN must be a credential-free HTTP(S) origin"
        )

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        is_ip_literal = False
        if not _is_dns_hostname(hostname):
            raise ValueError(
                "RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN hostname is invalid"
            ) from None
    else:
        is_ip_literal = True

    if parsed.netloc.endswith(":"):
        raise ValueError("RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN port is invalid")

    if production:
        ambiguous_ip_literal = bool(
            re.fullmatch(
                r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*",
                hostname,
            )
        )
        if (
            scheme != "https"
            or port not in {None, 443}
            or is_ip_literal
            or ambiguous_ip_literal
            or hostname == "localhost"
            or hostname.endswith(".localhost")
        ):
            raise ValueError(
                "Production RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN must use HTTPS "
                "on port 443 with a non-local DNS hostname"
            )
    elif scheme == "http" and not _is_loopback_hostname(hostname):
        raise ValueError(
            "Development/test HTTP RELAY_NATIVE_ADMIN_CONSOLE_ORIGIN must use a loopback host"
        )

    if ":" in hostname:
        authority = f"[{hostname}]"
    else:
        authority = hostname
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}"


def _validate_relay_callback_url(value: str, *, production: bool) -> bool:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    valid_scheme = (
        parsed.scheme == "https"
        if production
        else parsed.scheme
        in {
            "http",
            "https",
        }
    )
    valid_port = not production or port in {None, 443}
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if production:
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
            (".localhost", ".local", ".internal")
        ):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return False
    return bool(
        valid_scheme
        and valid_port
        and hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/internal/relay-callbacks"
        and not parsed.query
        and not parsed.fragment
    )


def _is_public_service_hostname(hostname: str) -> bool:
    normalized = hostname.casefold().rstrip(".")
    if (
        not normalized
        or normalized in {"localhost", "localhost.localdomain"}
        or normalized.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "." in normalized
    return address.is_global


def _validate_download_gateway_url(
    value: str,
    *,
    production: bool,
    registration: bool,
) -> bool:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    scheme_ok = (
        parsed.scheme == "https"
        if production
        else parsed.scheme
        in {
            "http",
            "https",
        }
    )
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if production and (
        port not in {None, 443} or not _is_public_service_hostname(hostname)
    ):
        return False
    expected_path = "/internal/v1/download-tickets" if registration else ""
    return bool(
        scheme_ok
        and hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path.rstrip("/") == expected_path
        and not parsed.query
        and not parsed.fragment
    )


def _validate_provider_alert_forward_url(
    value: str,
    *,
    production: bool,
) -> bool:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    raw_hostname = parsed.hostname or ""
    hostname = raw_hostname.casefold().rstrip(".")
    normalized_authority = (
        raw_hostname == hostname
        and parsed.netloc == parsed.netloc.lower()
        and "%" not in hostname
        and "\\" not in value
        and not parsed.path.startswith("//")
    )
    if production:
        scheme_ok = parsed.scheme == "https" and value.startswith("https://")
        host_ok = port in {None, 443} and _is_public_service_hostname(hostname)
    else:
        scheme_ok = parsed.scheme in {"http", "https"}
        host_ok = bool(hostname)
    return bool(
        scheme_ok
        and host_ok
        and normalized_authority
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


class Settings(BaseSettings):
    app_name: str = "AI Video Customer Platform"
    environment: Literal["development", "test", "staging", "production"] = (
        "development"
    )
    process_role: Literal[
        "migration",
        "platform-api",
        "dispatcher",
        "relay-sync",
        "timeout-worker",
        "publishing-worker",
        "download-gateway-registration-worker",
    ] = "platform-api"
    database_url: str = "sqlite:///./platform-app.db"
    auto_create_tables: bool = False
    development_header_auth_enabled: bool = False
    enable_bootstrap: bool = False
    bootstrap_token: str | None = None
    cors_origins: list[str] | None = None
    relay_base_url: str | None = None
    relay_default_backend_id: str = Field(
        default=NEW_API_RELAY_BACKEND_ID,
        pattern=RELAY_BACKEND_ID_PATTERN,
    )
    relay_default_contract_revision: str = Field(
        default=DEFAULT_RELAY_CONTRACT_REVISION,
        pattern=RELAY_CONTRACT_REVISION_PATTERN,
    )
    relay_backends: dict[str, RelayBackendConfiguration] = Field(
        default_factory=dict
    )
    relay_operations_base_url: str | None = None
    relay_native_admin_console_origin: str | None = None
    relay_client_id: str | None = None
    relay_api_key: str | None = None
    relay_legacy_compatibility_enabled: bool = False
    relay_tenant_id: str | None = None
    relay_operations_token: str | None = None
    relay_reconciliation_approval_key_id: str | None = None
    relay_reconciliation_approval_secret: str | None = None
    relay_dispatch_max_attempts: int = Field(default=12, ge=1, le=1000)
    relay_callback_public_url: str | None = None
    relay_callback_signing_secret: str | None = None
    relay_callback_signing_secrets: dict[str, SecretStr] = Field(
        default_factory=dict
    )
    relay_callback_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    task_queued_timeout_seconds: int = Field(default=3600, ge=60, le=604800)
    task_processing_timeout_seconds: int = Field(default=21600, ge=60, le=2592000)
    task_timeout_scan_interval_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    task_timeout_batch_size: int = Field(default=100, ge=1, le=1000)
    publishing_mock_enabled: bool = False
    publishing_worker_enabled: bool = False
    publishing_adapters: str = ""
    publishing_media_resolver: str = ""
    publishing_plugin_credentials: dict[
        str, dict[str, dict[str, SecretStr]]
    ] = Field(default_factory=lambda: {"adapters": {}}, exclude=True)
    publishing_worker_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    publishing_worker_batch_size: int = Field(default=50, ge=1, le=500)
    publishing_lease_seconds: int = Field(default=60, ge=10, le=900)
    publishing_max_attempts: int = Field(default=5, ge=1, le=100)
    publishing_oauth_callback_url: str | None = None
    publishing_oauth_success_url: str | None = None
    publishing_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=1800)
    internal_service_token: str | None = None
    download_edge_completion_service_token: str | None = None
    channel_cost_signing_secret: str | None = None
    channel_cost_signature_required: bool | None = None
    channel_cost_signature_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    relay_telemetry_signing_secret: str | None = None
    relay_telemetry_signature_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    provider_alert_signing_secret: str | None = None
    provider_alert_signature_max_age_seconds: int = Field(default=300, ge=30, le=3600)
    provider_alert_forward_webhook_url: str | None = None
    provider_alert_forward_signing_secret: str | None = None
    provider_alert_forward_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    download_completion_edge_gateway_signing_secret: str | None = None
    download_completion_obs_access_log_signing_secret: str | None = None
    download_completion_signature_max_age_seconds: int = Field(
        default=300, ge=30, le=3600
    )
    download_gateway_registration_url: str | None = None
    download_gateway_public_base_url: str | None = None
    download_gateway_service_token: str | None = None
    download_gateway_registration_signing_secret: str | None = None
    download_gateway_attempt_encryption_key_base64: str | None = None
    download_gateway_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    download_gateway_registration_worker_enabled: bool = False
    download_gateway_registration_poll_interval_seconds: float = Field(
        default=1.0, ge=0.1, le=60.0
    )
    download_gateway_registration_batch_size: int = Field(default=50, ge=1, le=500)
    download_gateway_registration_lease_seconds: int = Field(default=30, ge=5, le=900)
    download_gateway_registration_lease_margin_seconds: int = Field(
        default=10, ge=5, le=60
    )
    download_gateway_registration_max_attempts: int = Field(default=8, ge=1, le=100)
    download_gateway_registration_retry_base_seconds: int = Field(
        default=1, ge=1, le=300
    )
    download_gateway_registration_retry_cap_seconds: int = Field(
        default=60, ge=1, le=3600
    )
    download_gateway_ticket_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    download_gateway_source_ttl_margin_seconds: int = Field(default=60, ge=30, le=3600)
    relay_artifact_signed_url_ttl_seconds: int = Field(default=600, ge=1, le=86400)
    relay_allow_legacy_artifact_download_response: bool | None = None
    jwt_signing_secret: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    auth_legacy_bearer_enabled: bool = False
    oidc_enabled: bool = False
    oidc_self_signup_enabled: bool = False
    oidc_issuer: str | None = None
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_client_id: str | None = None
    oidc_redirect_uri: str | None = None
    frontend_origin: str | None = None
    account_management_url: str | None = None
    auth_session_ttl_seconds: int = Field(default=28_800, ge=300, le=2_592_000)
    auth_session_idle_ttl_seconds: int = Field(default=3_600, ge=60, le=604_800)
    auth_account_step_up_max_age_seconds: int = Field(default=300, ge=60, le=1_800)
    oidc_login_transaction_ttl_seconds: int = Field(default=600, ge=60, le=1_800)
    oidc_id_token_max_lifetime_seconds: int = Field(default=900, ge=60, le=3_600)
    oidc_login_ip_window_seconds: int = Field(default=600, ge=60, le=3_600)
    oidc_login_ip_max_attempts: int = Field(default=20, ge=1, le=1_000)
    invitation_ttl_seconds: int = Field(default=604_800, ge=300, le=2_592_000)
    platform_owner_user_ids: list[str] = Field(default_factory=list)
    platform_admin_required_amr: list[str] = Field(
        default_factory=lambda: sorted(PHISHING_RESISTANT_PLATFORM_ADMIN_AMR)
    )
    platform_admin_step_up_max_age_seconds: int = Field(default=300, ge=60, le=1800)
    input_asset_store: Literal["filesystem", "huawei_obs"] = "filesystem"
    input_asset_filesystem_root: str = "./data/platform-input-assets"
    input_asset_public_base_url: str = "http://127.0.0.1:8000"
    input_asset_relay_base_url: str | None = None
    input_asset_signing_secret: str = "development-input-asset-signing-secret"
    input_asset_signed_url_seconds: int = Field(default=300, ge=30, le=3600)
    input_asset_relay_signed_url_seconds: int = Field(default=3600, ge=300, le=86400)
    input_asset_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        le=2 * 1024 * 1024 * 1024,
    )
    artifact_promotion_download_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
    )
    huawei_obs_access_key_id: str | None = None
    huawei_obs_secret_access_key: str | None = None
    huawei_obs_security_token: str | None = None
    huawei_obs_endpoint: str | None = None
    huawei_obs_bucket: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "relay_tenant_id",
        "relay_operations_token",
        "relay_reconciliation_approval_key_id",
        "relay_reconciliation_approval_secret",
        "relay_native_admin_console_origin",
        "oidc_issuer",
        "oidc_authorization_endpoint",
        "oidc_token_endpoint",
        "oidc_jwks_uri",
        "oidc_client_id",
        "oidc_redirect_uri",
        "frontend_origin",
        "account_management_url",
        mode="before",
    )
    @classmethod
    def normalize_empty_optional_relay_control_values(cls, value: object) -> object:
        # Compose renders optional unset values as empty strings. Treat the
        # exact empty value as absent while retaining strict rejection of
        # whitespace-padded or partially configured production credentials.
        return None if value == "" else value

    @field_validator(
        "oidc_enabled",
        "oidc_self_signup_enabled",
        "auth_legacy_bearer_enabled",
        mode="before",
    )
    @classmethod
    def parse_strict_browser_auth_boolean(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value in {"true", "false"}:
            return value == "true"
        raise ValueError(
            "browser authentication booleans accept only true or false"
        )

    def publishing_plugin_secret_manifest(
        self,
        section: str,
        spec: str,
    ) -> Mapping[str, str]:
        raw_manifest = {
            section_name: {
                plugin_spec: {
                    name: secret.get_secret_value()
                    for name, secret in credentials.items()
                }
                for plugin_spec, credentials in entries.items()
            }
            for section_name, entries in self.publishing_plugin_credentials.items()
        }
        return immutable_plugin_credentials(raw_manifest, section, spec)

    def _validate_production_process_minimum(
        self,
        configured_backend_ids: set[str],
    ) -> None:
        role = self.process_role
        if role not in PLATFORM_PROCESS_ROLES or role == "platform-api":
            raise ValueError("Production PLATFORM_PROCESS_ROLE is invalid")
        if self.development_header_auth_enabled:
            raise ValueError(
                "Production DEVELOPMENT_HEADER_AUTH_ENABLED cannot be enabled"
            )
        if self.enable_bootstrap or self.bootstrap_token is not None:
            raise ValueError("Production bootstrap must be disabled")
        if self.auto_create_tables:
            raise ValueError("AUTO_CREATE_TABLES must be disabled in production")
        if urlsplit(self.database_url).scheme != _PRODUCTION_DATABASE_SCHEME:
            raise ValueError("Production DATABASE_URL must use postgresql+psycopg://")
        if role == "migration":
            return

        relay_roles = {"dispatcher", "relay-sync", "timeout-worker"}
        if role in relay_roles:
            if (
                configured_backend_ids != {NEW_API_RELAY_BACKEND_ID}
                or self.relay_default_backend_id != NEW_API_RELAY_BACKEND_ID
                or self.relay_default_contract_revision
                != NEW_API_RELAY_CONTRACT_REVISION
                or any((self.relay_base_url, self.relay_client_id, self.relay_api_key))
                or self.relay_legacy_compatibility_enabled
            ):
                raise ValueError(
                    "Protected Relay workers require the single native new-api "
                    "backend identity and contract"
                )
            for backend_id, backend in self.relay_backends.items():
                api_key = backend.api_key.get_secret_value()
                if (
                    not _validate_https_url(backend.base_url)
                    or _is_known_placeholder(backend.client_id)
                    or len(api_key.encode("utf-8"))
                    < _MINIMUM_PRODUCTION_SECRET_BYTES
                    or _is_known_placeholder(api_key)
                ):
                    raise ValueError(
                        f"Production Relay backend {backend_id!r} is invalid"
                    )

        if role == "dispatcher":
            if self.input_asset_store != "huawei_obs":
                raise ValueError("Production dispatcher input store must be huawei_obs")
            if not _is_official_huawei_obs_endpoint(self.huawei_obs_endpoint or ""):
                raise ValueError("Production dispatcher OBS endpoint is invalid")
            if not re.fullmatch(
                r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
                self.huawei_obs_bucket or "",
            ):
                raise ValueError("Production dispatcher OBS bucket is invalid")
            access_key_id = self.huawei_obs_access_key_id or ""
            if (
                not access_key_id
                or access_key_id != access_key_id.strip()
                or any(character.isspace() for character in access_key_id)
                or _is_known_placeholder(access_key_id)
            ):
                raise ValueError(
                    "Production dispatcher HUAWEI_OBS_ACCESS_KEY_ID is invalid"
                )
            for name, value in (
                ("HUAWEI_OBS_SECRET_ACCESS_KEY", self.huawei_obs_secret_access_key),
            ):
                normalized = value or ""
                if (
                    len(normalized.encode("utf-8")) < _MINIMUM_PRODUCTION_SECRET_BYTES
                    or normalized != normalized.strip()
                    or _is_known_placeholder(normalized)
                ):
                    raise ValueError(f"Production dispatcher {name} is invalid")
            if self.huawei_obs_security_token is not None and (
                len(self.huawei_obs_security_token.encode("utf-8"))
                < _MINIMUM_PRODUCTION_SECRET_BYTES
                or _is_known_placeholder(self.huawei_obs_security_token)
            ):
                raise ValueError("Production dispatcher OBS security token is invalid")
            if self.input_asset_relay_base_url is None:
                self.input_asset_relay_base_url = self.input_asset_public_base_url

        if role == "publishing-worker":
            if self.publishing_mock_enabled:
                raise ValueError("Production publishing mock cannot be enabled")
            adapter_specs = [
                item.strip() for item in self.publishing_adapters.split(",") if item.strip()
            ]
            resolver_spec = self.publishing_media_resolver.strip()
            if (
                not self.publishing_worker_enabled
                or not adapter_specs
                or not resolver_spec
            ):
                raise ValueError(
                    "Production publishing worker requires enabled adapters and media resolver"
                )
            if set(self.publishing_plugin_credentials) != {
                "adapters",
                "media_resolvers",
            }:
                raise ValueError(
                    "Production publishing worker credential manifest is incomplete"
                )
            if set(self.publishing_plugin_credentials["adapters"]) != set(
                adapter_specs
            ) or set(self.publishing_plugin_credentials["media_resolvers"]) != {
                resolver_spec
            }:
                raise ValueError(
                    "Production publishing plug-in specs must exactly match the credential manifest"
                )

        if role == "download-gateway-registration-worker":
            if not self.download_gateway_registration_worker_enabled:
                raise ValueError(
                    "Production Download Gateway registration worker must be enabled"
                )
            if not self.download_gateway_configured:
                raise ValueError("Production Download Gateway configuration is incomplete")

    @model_validator(mode="after")
    def validate_environment_safety(self) -> "Settings":
        protected_runtime = self.environment in {"production", "staging"} or (
            protected_platform_runtime_requested()
        )
        legacy_relay_identity = (
            self.relay_base_url,
            self.relay_client_id,
            self.relay_api_key,
        )
        if any(value is not None for value in legacy_relay_identity) and not all(
            legacy_relay_identity
        ):
            raise ValueError(
                "RELAY_BASE_URL, RELAY_CLIENT_ID, and RELAY_API_KEY must be "
                "configured together"
            )
        legacy_compatibility_requested = bool(
            any(legacy_relay_identity)
            or LEGACY_RELAY_BACKEND_ID in self.relay_backends
            or self.relay_callback_signing_secret
            or self.relay_allow_legacy_artifact_download_response is True
            or len(self.relay_backends) > 1
        )
        if (
            not protected_runtime
            and legacy_compatibility_requested
            and not self.relay_legacy_compatibility_enabled
        ):
            raise ValueError(
                "Legacy or multi-backend Relay compatibility requires "
                "RELAY_LEGACY_COMPATIBILITY_ENABLED"
            )
        if protected_runtime and self.relay_legacy_compatibility_enabled:
            raise ValueError(
                "Protected Platform cannot enable legacy Relay compatibility"
            )
        for backend_id, backend in self.relay_backends.items():
            if re.fullmatch(RELAY_BACKEND_ID_PATTERN, backend_id) is None:
                raise ValueError(f"RELAY_BACKENDS contains invalid id {backend_id!r}")
            if not _validate_http_root_url(backend.base_url):
                raise ValueError(
                    f"Relay backend {backend_id!r} base URL must be a "
                    "credential-free HTTP(S) root URL"
                )
            if (
                backend.client_id != backend.client_id.strip()
                or any(character.isspace() for character in backend.client_id)
            ):
                raise ValueError(
                    f"Relay backend {backend_id!r} client_id must not contain whitespace"
                )
        configured_backend_ids = set(self.relay_backends)
        if self.relay_legacy_compatibility_enabled and all(legacy_relay_identity):
            configured_backend_ids.add(LEGACY_RELAY_BACKEND_ID)
            if not self.relay_backends:
                configured_backend_ids.add(self.relay_default_backend_id)
        if (
            configured_backend_ids
            and self.relay_default_backend_id not in configured_backend_ids
        ):
            raise ValueError(
                "RELAY_DEFAULT_BACKEND_ID must identify a configured Relay backend"
            )
        configured_default = self.relay_backends.get(self.relay_default_backend_id)
        if configured_default is not None and (
            configured_default.contract_revision
            != self.relay_default_contract_revision
        ):
            raise ValueError(
                "RELAY_DEFAULT_CONTRACT_REVISION must match the configured "
                "default Relay backend"
            )
        if (
            all(legacy_relay_identity)
            and self.relay_default_backend_id == LEGACY_RELAY_BACKEND_ID
            and self.relay_default_contract_revision
            != DEFAULT_RELAY_CONTRACT_REVISION
        ):
            raise ValueError(
                "The legacy Relay backend contract revision is generations.v1"
            )
        if self.bootstrap_token is not None and not self.enable_bootstrap:
            raise ValueError("BOOTSTRAP_TOKEN requires ENABLE_BOOTSTRAP")
        browser_auth_fields = {
            "jwt_signing_secret",
            "jwt_issuer",
            "jwt_audience",
            "auth_legacy_bearer_enabled",
            "oidc_enabled",
            "oidc_self_signup_enabled",
            "oidc_issuer",
            "oidc_authorization_endpoint",
            "oidc_token_endpoint",
            "oidc_jwks_uri",
            "oidc_client_id",
            "oidc_redirect_uri",
            "frontend_origin",
            "account_management_url",
            "auth_session_ttl_seconds",
            "auth_session_idle_ttl_seconds",
            "auth_account_step_up_max_age_seconds",
            "oidc_login_transaction_ttl_seconds",
            "oidc_id_token_max_lifetime_seconds",
            "oidc_login_ip_window_seconds",
            "oidc_login_ip_max_attempts",
            "invitation_ttl_seconds",
            "platform_owner_user_ids",
            "platform_admin_required_amr",
            "platform_admin_step_up_max_age_seconds",
        }
        if (
            protected_runtime
            and self.process_role != "platform-api"
            and browser_auth_fields.intersection(self.model_fields_set)
        ):
            raise ValueError(
                "Protected non-API processes must not receive browser "
                "authentication configuration"
            )
        if (
            protected_runtime
            and self.process_role == "platform-api"
            and self.auth_legacy_bearer_enabled
        ):
            raise ValueError(
                "AUTH_LEGACY_BEARER_ENABLED cannot be enabled in a protected runtime"
            )
        if (
            protected_runtime
            and self.process_role == "platform-api"
            and not self.oidc_enabled
        ):
            raise ValueError("OIDC_ENABLED must be true in a protected runtime")
        if (
            protected_runtime
            and self.process_role == "platform-api"
            and "oidc_self_signup_enabled" not in self.model_fields_set
        ):
            raise ValueError(
                "OIDC_SELF_SIGNUP_ENABLED must be explicitly configured in a "
                "protected runtime"
            )
        if self.enable_bootstrap and not protected_runtime:
            bootstrap_token = self.bootstrap_token or ""
            if (
                len(bootstrap_token.encode("utf-8")) < 32
                or bootstrap_token != bootstrap_token.strip()
                or any(character.isspace() for character in bootstrap_token)
                or _is_known_placeholder(bootstrap_token)
            ):
                raise ValueError(
                    "BOOTSTRAP_TOKEN must contain at least 32 UTF-8 bytes of "
                    "non-placeholder, whitespace-free secret material"
                )
        oidc_required = {
            "OIDC_ISSUER": self.oidc_issuer,
            "OIDC_AUTHORIZATION_ENDPOINT": self.oidc_authorization_endpoint,
            "OIDC_TOKEN_ENDPOINT": self.oidc_token_endpoint,
            "OIDC_JWKS_URI": self.oidc_jwks_uri,
            "OIDC_CLIENT_ID": self.oidc_client_id,
            "OIDC_REDIRECT_URI": self.oidc_redirect_uri,
            "FRONTEND_ORIGIN": self.frontend_origin,
        }
        if self.oidc_enabled and not all(oidc_required.values()):
            missing = ", ".join(
                name for name, value in oidc_required.items() if not value
            )
            raise ValueError("OIDC authentication configuration is incomplete: " + missing)
        if not self.oidc_enabled and any(oidc_required.values()):
            raise ValueError("OIDC configuration requires OIDC_ENABLED=true")
        if self.oidc_enabled:
            for setting_name, value in oidc_required.items():
                assert value is not None
                if setting_name == "OIDC_CLIENT_ID":
                    if (
                        value != value.strip()
                        or not value
                        or any(character.isspace() for character in value)
                        or _is_known_placeholder(value)
                    ):
                        raise ValueError("OIDC_CLIENT_ID is invalid")
                    continue
                if setting_name == "FRONTEND_ORIGIN":
                    valid = (
                        _validate_https_origin(value)
                        if protected_runtime
                        else (
                            _validate_https_origin(value)
                            or (
                                _validate_http_root_url(value)
                                and _is_loopback_hostname(
                                    (urlsplit(value).hostname or "").lower()
                                )
                            )
                        )
                    )
                else:
                    valid = _validate_publishing_oauth_url(
                        value, production=protected_runtime
                    )
                if not valid:
                    raise ValueError(
                        f"{setting_name} must be a fixed credential-free "
                        + ("HTTPS" if protected_runtime else "HTTPS or loopback HTTP")
                        + " URL"
                    )
            if self.auth_session_idle_ttl_seconds > self.auth_session_ttl_seconds:
                raise ValueError(
                    "AUTH_SESSION_IDLE_TTL_SECONDS cannot exceed AUTH_SESSION_TTL_SECONDS"
                )
            if protected_runtime:
                redirect = urlsplit(self.oidc_redirect_uri or "")
                platform_origin = urlsplit(self.input_asset_public_base_url)
                if (
                    redirect.path != "/api/v1/auth/callback"
                    or redirect.query
                    or redirect.fragment
                    or (redirect.scheme, redirect.netloc)
                    != (platform_origin.scheme, platform_origin.netloc)
                ):
                    raise ValueError(
                        "OIDC_REDIRECT_URI must be the canonical Platform origin "
                        "plus /api/v1/auth/callback"
                    )
                if self.cors_origins != [self.frontend_origin]:
                    raise ValueError(
                        "Protected browser authentication requires CORS_ORIGINS "
                        "to contain exactly the single FRONTEND_ORIGIN"
                    )
                if not _browser_origins_share_schemeful_site(
                    self.frontend_origin,
                    self.oidc_redirect_uri,
                ):
                    raise ValueError(
                        "Protected FRONTEND_ORIGIN and Platform API origin must "
                        "share one schemeful site"
                    )
        if self.account_management_url is not None and not _validate_publishing_oauth_url(
            self.account_management_url, production=protected_runtime
        ):
            raise ValueError(
                "ACCOUNT_MANAGEMENT_URL must be a fixed credential-free "
                + ("HTTPS" if protected_runtime else "HTTPS or loopback HTTP")
                + " URL"
            )
        if (
            protected_runtime
            and self.process_role == "platform-api"
            and self.account_management_url is None
        ):
            raise ValueError(
                "ACCOUNT_MANAGEMENT_URL is required for protected Platform API"
            )
        operations_identity = (
            self.relay_tenant_id,
            self.relay_operations_token,
            self.relay_reconciliation_approval_key_id,
            self.relay_reconciliation_approval_secret,
        )
        if any(operations_identity) and not all(operations_identity):
            raise ValueError(
                "RELAY_TENANT_ID, RELAY_OPERATIONS_TOKEN, "
                "RELAY_RECONCILIATION_APPROVAL_KEY_ID, and "
                "RELAY_RECONCILIATION_APPROVAL_SECRET must be configured together"
            )
        if self.relay_operations_base_url and not all(operations_identity):
            raise ValueError(
                "RELAY_OPERATIONS_BASE_URL requires RELAY_TENANT_ID and "
                "RELAY_OPERATIONS_TOKEN"
            )
        if self.relay_native_admin_console_origin is not None:
            self.relay_native_admin_console_origin = (
                _normalize_relay_native_admin_console_origin(
                    self.relay_native_admin_console_origin,
                    production=protected_runtime,
                )
            )
        if self.relay_tenant_id is not None:
            operations_base_url = self.relay_operations_base_url
            if not operations_base_url:
                raise ValueError(
                    "RELAY_OPERATIONS_BASE_URL is required when Relay operations "
                    "are configured"
                )
            if not _validate_http_root_url(operations_base_url):
                raise ValueError(
                    "Relay operations base URL must be a credential-free HTTP(S) "
                    "root URL"
                )
            if protected_runtime and not _validate_https_url(
                operations_base_url
            ):
                raise ValueError(
                    "Production Relay operations base URL must be a "
                    "credential-free HTTPS root URL"
                )
            if protected_runtime:
                native_backend = self.relay_backends.get(
                    NEW_API_RELAY_BACKEND_ID
                )
                if native_backend is None or _canonical_http_root_origin(
                    operations_base_url
                ) != _canonical_http_root_origin(native_backend.base_url):
                    raise ValueError(
                        "Protected RELAY_OPERATIONS_BASE_URL must use the same "
                        "canonical origin as the native new-api data backend"
                    )
            try:
                canonical_tenant_id = str(UUID(self.relay_tenant_id))
            except ValueError as exc:
                raise ValueError("RELAY_TENANT_ID must be a canonical UUID") from exc
            if canonical_tenant_id != self.relay_tenant_id:
                raise ValueError("RELAY_TENANT_ID must be a canonical UUID")
            operations_token = self.relay_operations_token or ""
            if (
                len(operations_token.encode("utf-8")) < 32
                or operations_token != operations_token.strip()
                or _is_known_placeholder(operations_token)
            ):
                raise ValueError(
                    "RELAY_OPERATIONS_TOKEN must be a non-placeholder secret of at least 32 UTF-8 bytes"
                )
            approval_key_id = self.relay_reconciliation_approval_key_id or ""
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", approval_key_id):
                raise ValueError("RELAY_RECONCILIATION_APPROVAL_KEY_ID is invalid")
            approval_secret = self.relay_reconciliation_approval_secret or ""
            if (
                len(approval_secret.encode("utf-8")) < 32
                or approval_secret != approval_secret.strip()
                or _is_known_placeholder(approval_secret)
            ):
                raise ValueError(
                    "RELAY_RECONCILIATION_APPROVAL_SECRET must be a "
                    "non-placeholder secret of at least 32 UTF-8 bytes"
                )
            if hmac.compare_digest(operations_token, approval_secret):
                raise ValueError(
                    "RELAY_RECONCILIATION_APPROVAL_SECRET must be distinct "
                    "from RELAY_OPERATIONS_TOKEN"
                )
        decoded_encryption_key: bytes | None = None
        if self.require_channel_cost_signature and not self.channel_cost_signing_secret:
            raise ValueError(
                "CHANNEL_COST_SIGNING_SECRET is required when channel-cost signatures are required"
            )
        if (
            self.relay_telemetry_signing_secret
            and len(self.relay_telemetry_signing_secret.encode("utf-8")) < 32
        ):
            raise ValueError(
                "RELAY_TELEMETRY_SIGNING_SECRET must contain at least 32 UTF-8 bytes"
            )
        provider_alert_values = (
            self.provider_alert_signing_secret,
            self.provider_alert_forward_webhook_url,
            self.provider_alert_forward_signing_secret,
        )
        if any(provider_alert_values) and not all(provider_alert_values):
            raise ValueError(
                "PROVIDER_ALERT_SIGNING_SECRET, PROVIDER_ALERT_FORWARD_WEBHOOK_URL, "
                "and PROVIDER_ALERT_FORWARD_SIGNING_SECRET must be configured together"
            )
        if all(provider_alert_values):
            inbound_secret = self.provider_alert_signing_secret or ""
            outbound_secret = self.provider_alert_forward_signing_secret or ""
            if len(inbound_secret.encode("utf-8")) < 32:
                raise ValueError(
                    "PROVIDER_ALERT_SIGNING_SECRET must contain at least 32 UTF-8 bytes"
                )
            if len(outbound_secret.encode("utf-8")) < 32:
                raise ValueError(
                    "PROVIDER_ALERT_FORWARD_SIGNING_SECRET must contain at least 32 UTF-8 bytes"
                )
            if hmac.compare_digest(inbound_secret, outbound_secret):
                raise ValueError(
                    "Provider alert inbound and downstream signing secrets must be independent"
                )
            if not _validate_provider_alert_forward_url(
                self.provider_alert_forward_webhook_url or "",
                production=protected_runtime,
            ):
                raise ValueError("PROVIDER_ALERT_FORWARD_WEBHOOK_URL is invalid")
        download_secrets = (
            self.download_completion_edge_gateway_signing_secret,
            self.download_completion_obs_access_log_signing_secret,
        )
        if bool(download_secrets[0]) != bool(download_secrets[1]):
            raise ValueError(
                "Both download-completion source signing secrets must be configured together"
            )
        if (
            download_secrets[0]
            and download_secrets[1]
            and download_secrets[0] == download_secrets[1]
        ):
            raise ValueError(
                "Download-completion sources must use independent signing secrets"
            )
        gateway_values = (
            self.download_gateway_registration_url,
            self.download_gateway_public_base_url,
            self.download_gateway_service_token,
            self.download_gateway_registration_signing_secret,
        )
        if any(gateway_values) and not all(gateway_values):
            raise ValueError(
                "Download Gateway registration URL, public base URL, service token, "
                "and signing secret must be configured together"
            )
        encryption_key = self.download_gateway_attempt_encryption_key_base64
        if all(gateway_values) and not encryption_key:
            raise ValueError(
                "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64 is required "
                "with Download Gateway configuration"
            )
        if encryption_key:
            try:
                decoded_encryption_key = base64.b64decode(
                    encryption_key,
                    validate=True,
                )
            except (binascii.Error, ValueError) as exc:
                raise ValueError(
                    "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64 must be valid base64"
                ) from exc
            if len(decoded_encryption_key) != 32:
                raise ValueError(
                    "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64 must decode to 32 bytes"
                )
            if (
                base64.b64encode(decoded_encryption_key).decode("ascii")
                != encryption_key
            ):
                raise ValueError(
                    "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64 must use canonical base64"
                )
        if (
            self.download_gateway_registration_retry_cap_seconds
            < self.download_gateway_registration_retry_base_seconds
        ):
            raise ValueError(
                "DOWNLOAD_GATEWAY_REGISTRATION_RETRY_CAP_SECONDS must be greater "
                "than or equal to DOWNLOAD_GATEWAY_REGISTRATION_RETRY_BASE_SECONDS"
            )
        if self.download_gateway_registration_worker_enabled and not all(
            gateway_values
        ):
            raise ValueError(
                "Download Gateway registration worker requires complete Download "
                "Gateway configuration"
            )
        if all(gateway_values):
            if self.download_gateway_registration_lease_seconds <= (
                self.download_gateway_timeout_seconds
                + self.download_gateway_registration_lease_margin_seconds
            ):
                raise ValueError(
                    "DOWNLOAD_GATEWAY_REGISTRATION_LEASE_SECONDS must be strictly "
                    "greater than DOWNLOAD_GATEWAY_TIMEOUT_SECONDS plus "
                    "DOWNLOAD_GATEWAY_REGISTRATION_LEASE_MARGIN_SECONDS"
                )
            required_source_ttl = (
                self.download_gateway_ticket_ttl_seconds
                + self.download_gateway_source_ttl_margin_seconds
            )
            if self.relay_artifact_signed_url_ttl_seconds < required_source_ttl:
                raise ValueError(
                    "RELAY_ARTIFACT_SIGNED_URL_TTL_SECONDS must be at least "
                    "DOWNLOAD_GATEWAY_TICKET_TTL_SECONDS plus "
                    "DOWNLOAD_GATEWAY_SOURCE_TTL_MARGIN_SECONDS"
                )
        if (
            self.download_gateway_registration_url
            and not _validate_download_gateway_url(
                self.download_gateway_registration_url,
                production=protected_runtime,
                registration=True,
            )
        ):
            raise ValueError(
                "DOWNLOAD_GATEWAY_REGISTRATION_URL must be the exact, "
                "credential-free download-ticket registration endpoint"
            )
        if (
            self.download_gateway_public_base_url
            and not _validate_download_gateway_url(
                self.download_gateway_public_base_url,
                production=protected_runtime,
                registration=False,
            )
        ):
            raise ValueError(
                "DOWNLOAD_GATEWAY_PUBLIC_BASE_URL must be a credential-free root URL"
            )
        oauth_urls = (
            self.publishing_oauth_callback_url,
            self.publishing_oauth_success_url,
        )
        if any(oauth_urls) and not all(oauth_urls):
            raise ValueError(
                "PUBLISHING_OAUTH_CALLBACK_URL and PUBLISHING_OAUTH_SUCCESS_URL "
                "must be configured together"
            )
        for oauth_url in oauth_urls:
            if oauth_url and not _validate_publishing_oauth_url(
                oauth_url,
                production=protected_runtime,
            ):
                raise ValueError(
                    "Publishing OAuth URLs must be credential-free HTTPS URLs "
                    "(development also permits loopback HTTP) without query or fragment"
                )

        if protected_runtime and self.process_role != "platform-api":
            self._validate_production_process_minimum(configured_backend_ids)
            return self

        if protected_runtime:
            if (
                configured_backend_ids != {NEW_API_RELAY_BACKEND_ID}
                or self.relay_default_backend_id != NEW_API_RELAY_BACKEND_ID
                or self.relay_default_contract_revision
                != NEW_API_RELAY_CONTRACT_REVISION
                or any(legacy_relay_identity)
                or self.relay_legacy_compatibility_enabled
            ):
                raise ValueError(
                    "Protected Platform requires exactly one native new-api Relay "
                    "backend at new-api-v1 / generations.v1"
                )
            for backend_id, backend in self.relay_backends.items():
                if not _validate_https_url(backend.base_url):
                    raise ValueError(
                        f"Production Relay backend {backend_id!r} must use a "
                        "credential-free HTTPS root URL"
                    )
                backend_api_key = backend.api_key.get_secret_value()
                if (
                    _is_known_placeholder(backend.client_id)
                    or len(backend_api_key.encode("utf-8"))
                    < _MINIMUM_PRODUCTION_SECRET_BYTES
                    or backend_api_key != backend_api_key.strip()
                    or _is_known_placeholder(backend_api_key)
                ):
                    raise ValueError(
                        f"Production Relay backend {backend_id!r} credentials "
                        "must be non-placeholder and API keys at least 32 UTF-8 bytes"
                    )
            if self.development_header_auth_enabled:
                raise ValueError(
                    "Production DEVELOPMENT_HEADER_AUTH_ENABLED cannot be enabled"
                )
            if not all(provider_alert_values):
                raise ValueError(
                    "Production requires a signed provider alert receiver and downstream"
                )
            if not all(gateway_values):
                raise ValueError(
                    "Production requires complete Download Gateway configuration"
                )
            if not self.download_gateway_registration_worker_enabled:
                raise ValueError(
                    "Production requires DOWNLOAD_GATEWAY_REGISTRATION_WORKER_ENABLED"
                )
            if self.relay_allow_legacy_artifact_download_response is True:
                raise ValueError(
                    "Production cannot allow legacy Relay artifact download responses"
                )
            if decoded_encryption_key is None or (
                decoded_encryption_key in {bytes(32), bytes(range(32))}
                or len(set(decoded_encryption_key)) < 16
            ):
                raise ValueError(
                    "Production DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64 "
                    "must be a random, non-development AES-256 key"
                )
            if self.publishing_mock_enabled:
                raise ValueError("Production PUBLISHING_MOCK_ENABLED cannot be enabled")
            if self.publishing_worker_enabled and (
                not self.publishing_adapters.strip()
                or not self.publishing_media_resolver.strip()
            ):
                raise ValueError(
                    "Production publishing worker requires at least one "
                    "PUBLISHING_ADAPTERS factory and PUBLISHING_MEDIA_RESOLVER"
                )
            api_adapter_specs = {
                item.strip()
                for item in self.publishing_adapters.split(",")
                if item.strip()
            }
            if set(self.publishing_plugin_credentials) != {"adapters"} or set(
                self.publishing_plugin_credentials.get("adapters", {})
            ) != api_adapter_specs:
                raise ValueError(
                    "Production Platform API publishing adapter specs must exactly "
                    "match its credential manifest"
                )
            if self.channel_cost_signature_required is False:
                raise ValueError(
                    "Production CHANNEL_COST_SIGNATURE_REQUIRED cannot be disabled"
                )
            database_scheme = urlsplit(self.database_url).scheme
            if database_scheme != _PRODUCTION_DATABASE_SCHEME:
                raise ValueError(
                    "Production DATABASE_URL must use postgresql+psycopg://"
                )
            if self.auto_create_tables:
                raise ValueError("AUTO_CREATE_TABLES must be disabled in production")
            if not self.cors_origins:
                raise ValueError(
                    "CORS_ORIGINS must be explicitly configured in production"
                )
            invalid_origins = [
                origin
                for origin in self.cors_origins
                if not _validate_https_origin(origin)
            ]
            if invalid_origins:
                raise ValueError(
                    "Every production CORS_ORIGINS entry must be an exact "
                    "credential-free HTTPS origin without a path, query, "
                    "fragment, or wildcard"
                )
            if self.enable_bootstrap:
                raise ValueError("ENABLE_BOOTSTRAP must be disabled in production")
            for setting_name, value in (
                ("JWT_ISSUER", self.jwt_issuer),
                ("JWT_AUDIENCE", self.jwt_audience),
            ) if self.auth_legacy_bearer_enabled else ():
                normalized = value or ""
                if (
                    not normalized
                    or normalized != normalized.strip()
                    or any(character.isspace() for character in normalized)
                    or _is_known_placeholder(normalized)
                ):
                    raise ValueError(
                        f"Production {setting_name} must be a non-placeholder "
                        "value without whitespace"
                    )
            if not self.platform_owner_user_ids or any(
                not value
                or value != value.strip()
                or any(character.isspace() for character in value)
                for value in self.platform_owner_user_ids
            ):
                raise ValueError(
                    "Production PLATFORM_OWNER_USER_IDS must contain at least "
                    "one explicit identity-provider subject"
                )
            if len(set(self.platform_owner_user_ids)) != len(
                self.platform_owner_user_ids
            ):
                raise ValueError(
                    "Production PLATFORM_OWNER_USER_IDS must not contain duplicates"
                )
            normalized_admin_amr = [
                value.casefold() for value in self.platform_admin_required_amr
            ]
            if (
                not normalized_admin_amr
                or len(set(normalized_admin_amr)) != len(normalized_admin_amr)
                or any(
                    value not in PHISHING_RESISTANT_PLATFORM_ADMIN_AMR
                    for value in normalized_admin_amr
                )
            ):
                raise ValueError(
                    "Production PLATFORM_ADMIN_REQUIRED_AMR must contain at "
                    "least one unique phishing-resistant method from: "
                    + ", ".join(sorted(PHISHING_RESISTANT_PLATFORM_ADMIN_AMR))
                )
            self.platform_admin_required_amr = normalized_admin_amr
            required_production_secrets = [
                ("INTERNAL_SERVICE_TOKEN", self.internal_service_token),
                (
                    "DOWNLOAD_EDGE_COMPLETION_SERVICE_TOKEN",
                    self.download_edge_completion_service_token,
                ),
                ("CHANNEL_COST_SIGNING_SECRET", self.channel_cost_signing_secret),
                (
                    "RELAY_TELEMETRY_SIGNING_SECRET",
                    self.relay_telemetry_signing_secret,
                ),
                ("PROVIDER_ALERT_SIGNING_SECRET", self.provider_alert_signing_secret),
                (
                    "PROVIDER_ALERT_FORWARD_SIGNING_SECRET",
                    self.provider_alert_forward_signing_secret,
                ),
                (
                    "DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET",
                    self.download_completion_edge_gateway_signing_secret,
                ),
                (
                    "DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET",
                    self.download_completion_obs_access_log_signing_secret,
                ),
                (
                    "DOWNLOAD_GATEWAY_SERVICE_TOKEN",
                    self.download_gateway_service_token,
                ),
                (
                    "DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET",
                    self.download_gateway_registration_signing_secret,
                ),
                (
                    "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64",
                    self.download_gateway_attempt_encryption_key_base64,
                ),
                ("JWT_SIGNING_SECRET", self.jwt_signing_secret),
            ]
            for setting_name, secret in required_production_secrets:
                secret_value = secret or ""
                if len(secret_value.encode("utf-8")) < _MINIMUM_PRODUCTION_SECRET_BYTES:
                    raise ValueError(
                        f"Production {setting_name} must contain at least "
                        f"{_MINIMUM_PRODUCTION_SECRET_BYTES} UTF-8 bytes"
                    )
                if _is_known_placeholder(secret_value):
                    raise ValueError(
                        f"Production {setting_name} must not use a known " "placeholder"
                    )
            if self.input_asset_store != "huawei_obs":
                raise ValueError("Production INPUT_ASSET_STORE must be huawei_obs")
            endpoint = self.huawei_obs_endpoint or ""
            if not _is_official_huawei_obs_endpoint(endpoint):
                raise ValueError(
                    "Production HUAWEI_OBS_ENDPOINT must be an official "
                    "credential-free Huawei OBS HTTPS root URL"
                )
            bucket = self.huawei_obs_bucket or ""
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
                raise ValueError("Production HUAWEI_OBS_BUCKET is invalid")
            for setting_name, credential in (
                ("HUAWEI_OBS_ACCESS_KEY_ID", self.huawei_obs_access_key_id),
                (
                    "HUAWEI_OBS_SECRET_ACCESS_KEY",
                    self.huawei_obs_secret_access_key,
                ),
            ):
                credential_value = credential or ""
                if (
                    not credential_value
                    or credential_value != credential_value.strip()
                    or any(character.isspace() for character in credential_value)
                    or _is_known_placeholder(credential_value)
                ):
                    raise ValueError(
                        f"Production {setting_name} must be a non-placeholder "
                        "value without whitespace"
                    )
            security_token = self.huawei_obs_security_token
            if security_token is not None and (
                not security_token
                or security_token != security_token.strip()
                or any(character.isspace() for character in security_token)
                or _is_known_placeholder(security_token)
            ):
                raise ValueError(
                    "Production HUAWEI_OBS_SECURITY_TOKEN must be a "
                    "non-placeholder value without whitespace when configured"
                )
            if (
                len((self.huawei_obs_secret_access_key or "").encode("utf-8"))
                < _MINIMUM_PRODUCTION_SECRET_BYTES
            ):
                raise ValueError(
                    "Production HUAWEI_OBS_SECRET_ACCESS_KEY must contain at "
                    f"least {_MINIMUM_PRODUCTION_SECRET_BYTES} UTF-8 bytes"
                )
        elif self.cors_origins is None:
            self.cors_origins = [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        if self.input_asset_relay_base_url is None:
            self.input_asset_relay_base_url = self.input_asset_public_base_url
        if self.input_asset_store == "filesystem":
            if not _validate_http_root_url(self.input_asset_public_base_url):
                raise ValueError(
                    "INPUT_ASSET_PUBLIC_BASE_URL must be a credential-free "
                    "HTTP(S) root URL"
                )
            if not _validate_http_root_url(self.input_asset_relay_base_url or ""):
                raise ValueError(
                    "INPUT_ASSET_RELAY_BASE_URL must be a credential-free "
                    "HTTP(S) root URL"
                )
            if len(self.input_asset_signing_secret.encode("utf-8")) < 16:
                raise ValueError(
                    "INPUT_ASSET_SIGNING_SECRET must contain at least 16 "
                    "UTF-8 bytes for filesystem storage"
                )
        callback_url = self.relay_callback_public_url or ""
        callback_secrets = {
            backend_id: secret.get_secret_value()
            for backend_id, secret in self.relay_callback_signing_secrets.items()
        }
        callback_secret = self.relay_callback_signing_secret or ""
        if callback_secret:
            if LEGACY_RELAY_BACKEND_ID in callback_secrets:
                raise ValueError(
                    "Configure legacy-default-v1 callback signing secret only once"
                )
            callback_secrets[LEGACY_RELAY_BACKEND_ID] = callback_secret
        if bool(callback_url) != bool(callback_secrets):
            raise ValueError(
                "RELAY_CALLBACK_PUBLIC_URL and "
                "Relay callback signing secrets must be configured together"
            )
        if callback_url:
            if not _validate_relay_callback_url(
                callback_url, production=protected_runtime
            ):
                raise ValueError(
                    "RELAY_CALLBACK_PUBLIC_URL must be the exact, "
                    "credential-free callback endpoint"
                )
            expected_callback_backends = configured_backend_ids or {
                self.relay_default_backend_id
            }
            missing_callback_backends = (
                expected_callback_backends - callback_secrets.keys()
            )
            if missing_callback_backends:
                raise ValueError(
                    "Relay callback signing secret is missing for backend(s): "
                    + ", ".join(sorted(missing_callback_backends))
                )
            unknown_callback_backends = (
                callback_secrets.keys() - expected_callback_backends
            )
            if unknown_callback_backends:
                raise ValueError(
                    "Relay callback signing secret references unconfigured backend(s): "
                    + ", ".join(sorted(unknown_callback_backends))
                )
            for backend_id, secret in callback_secrets.items():
                if re.fullmatch(RELAY_BACKEND_ID_PATTERN, backend_id) is None:
                    raise ValueError(
                        "RELAY_CALLBACK_SIGNING_SECRETS contains invalid backend id"
                    )
                if len(secret.encode("utf-8")) < 32:
                    raise ValueError(
                        "Relay callback signing secrets must contain at least 32 "
                        "UTF-8 bytes"
                    )
                if protected_runtime and _is_known_placeholder(secret):
                    raise ValueError(
                        "Production Relay callback signing secrets must not use "
                        "a known placeholder"
                    )
        if protected_runtime:
            independent_secrets = {
                "INTERNAL_SERVICE_TOKEN": self.internal_service_token,
                "DOWNLOAD_EDGE_COMPLETION_SERVICE_TOKEN": (
                    self.download_edge_completion_service_token
                ),
                "CHANNEL_COST_SIGNING_SECRET": self.channel_cost_signing_secret,
                "RELAY_TELEMETRY_SIGNING_SECRET": (self.relay_telemetry_signing_secret),
                "PROVIDER_ALERT_SIGNING_SECRET": self.provider_alert_signing_secret,
                "PROVIDER_ALERT_FORWARD_SIGNING_SECRET": (
                    self.provider_alert_forward_signing_secret
                ),
                "DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET": (
                    self.download_completion_edge_gateway_signing_secret
                ),
                "DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET": (
                    self.download_completion_obs_access_log_signing_secret
                ),
                "DOWNLOAD_GATEWAY_SERVICE_TOKEN": (self.download_gateway_service_token),
                "DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET": (
                    self.download_gateway_registration_signing_secret
                ),
                "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64": (
                    self.download_gateway_attempt_encryption_key_base64
                ),
                "JWT_SIGNING_SECRET": self.jwt_signing_secret,
            }
            for backend_id, backend in self.relay_backends.items():
                independent_secrets[f"RELAY_BACKENDS[{backend_id}].API_KEY"] = (
                    backend.api_key.get_secret_value()
                )
            if self.relay_operations_token:
                independent_secrets["RELAY_OPERATIONS_TOKEN"] = (
                    self.relay_operations_token
                )
            if self.relay_reconciliation_approval_secret:
                independent_secrets["RELAY_RECONCILIATION_APPROVAL_SECRET"] = (
                    self.relay_reconciliation_approval_secret
                )
            for backend_id, secret in callback_secrets.items():
                independent_secrets[
                    f"RELAY_CALLBACK_SIGNING_SECRETS[{backend_id}]"
                ] = secret
            seen: dict[str, str] = {}
            for setting_name, secret in independent_secrets.items():
                assert secret is not None
                previous = seen.get(secret)
                if previous is not None:
                    raise ValueError(
                        f"Production {setting_name} must be distinct from {previous}"
                    )
                seen[secret] = setting_name
            if decoded_encryption_key is not None:
                for setting_name, secret in independent_secrets.items():
                    if setting_name == "DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64":
                        continue
                    assert secret is not None
                    if decoded_encryption_key == secret.encode("utf-8"):
                        raise ValueError(
                            "Production DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64 "
                            f"must be distinct from {setting_name}"
                        )
        return self

    @property
    def require_channel_cost_signature(self) -> bool:
        if self.channel_cost_signature_required is None:
            return (
                (
                    self.environment in {"production", "staging"}
                    or protected_platform_runtime_requested()
                )
                and self.process_role == "platform-api"
            )
        return self.channel_cost_signature_required

    @property
    def protected_runtime(self) -> bool:
        """Return the security boundary, independent of the business label."""

        return self.environment in {"production", "staging"} or (
            protected_platform_runtime_requested()
        )

    @property
    def allow_legacy_relay_artifact_download_response(self) -> bool:
        if (
            self.environment in {"production", "staging"}
            or protected_platform_runtime_requested()
        ):
            return False
        return self.relay_allow_legacy_artifact_download_response is True

    @property
    def download_gateway_configured(self) -> bool:
        return all(
            (
                self.download_gateway_registration_url,
                self.download_gateway_public_base_url,
                self.download_gateway_service_token,
                self.download_gateway_registration_signing_secret,
                self.download_gateway_attempt_encryption_key_base64,
            )
        )


@lru_cache
def get_settings(expected_process_role: str | None = None) -> Settings:
    _validate_protected_platform_api_browser_site_environment()
    protected_values = load_platform_process_secret_settings()
    if protected_values:
        actual_role = protected_values["process_role"]
        if expected_process_role is None or actual_role != expected_process_role:
            raise RuntimeError("Protected Platform process role does not match its entrypoint")
        settings_values = dict(protected_values)
        if actual_role not in {
            "platform-api",
            "download-gateway-registration-worker",
        }:
            # This name is intentionally part of the global release identity
            # audience as well as the API/Gateway business configuration. The
            # receipt loader above must see the release value, but unrelated
            # least-privilege processes must not inherit it as one member of a
            # partial Download Gateway credential set.
            settings_values["download_gateway_public_base_url"] = None
        try:
            return Settings(_env_file=None, **settings_values)
        except Exception:
            # Pydantic's default ValidationError repr includes input values.
            # A protected startup failure must never copy bundle values into a
            # traceback or orchestrator log.
            raise RuntimeError("Protected Platform runtime settings are invalid") from None
    return Settings()
