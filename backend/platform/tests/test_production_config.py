from __future__ import annotations

import base64
import pytest
from pydantic import ValidationError

from platform_api.config import Settings

VALID_PRODUCTION_SETTINGS = {
    "environment": "production",
    "database_url": ("postgresql+psycopg://platform:password@db.example.com/platform"),
    "auto_create_tables": False,
    "enable_bootstrap": False,
    "cors_origins": ["https://app.example.com"],
    "relay_backends": {
        "new-api-v1": {
            "base_url": "https://relay.example.com",
            "client_id": "platform-production-service",
            "api_key": "relay-api-key-32-bytes-minimum!!",
            "contract_revision": "generations.v1",
        }
    },
    "relay_default_backend_id": "new-api-v1",
    "relay_default_contract_revision": "generations.v1",
    "internal_service_token": "internal-token-32-bytes-minimum!!",
    "download_edge_completion_service_token": (
        "edge-completion-service-token-32-bytes!!"
    ),
    "channel_cost_signing_secret": "channel-cost-signing-secret-32-bytes!!",
    "relay_telemetry_signing_secret": ("relay-telemetry-signing-secret-32-bytes!!"),
    "provider_alert_signing_secret": (
        "provider-alert-inbound-signing-secret-32-bytes!!"
    ),
    "provider_alert_forward_webhook_url": (
        "https://alerts.example.com/platform/provider"
    ),
    "provider_alert_forward_signing_secret": (
        "provider-alert-outbound-signing-secret-32-bytes!!"
    ),
    "download_completion_edge_gateway_signing_secret": (
        "edge-download-signing-secret-32-bytes!!"
    ),
    "download_completion_obs_access_log_signing_secret": (
        "obs-download-signing-secret-32-bytes!!!"
    ),
    "download_gateway_registration_url": (
        "https://download-gateway.example.com/internal/v1/download-tickets"
    ),
    "download_gateway_public_base_url": "https://downloads.example.com",
    "download_gateway_service_token": "gateway-service-token-32-bytes-minimum!!",
    "download_gateway_registration_signing_secret": (
        "gateway-registration-signing-32-bytes!!"
    ),
    "download_gateway_attempt_encryption_key_base64": (
        "sA7lnL/q4z+Wf+0koSVf8J/8lGUx8ZO8PBACk6WcD8c="
    ),
    "download_gateway_registration_worker_enabled": True,
    "jwt_signing_secret": "jwt-signing-secret-32-bytes-minimum!!",
    "jwt_issuer": "ai-video-platform",
    "jwt_audience": "ai-video-web",
    "oidc_enabled": True,
    "oidc_self_signup_enabled": False,
    "oidc_issuer": "https://idp.example.com/",
    "oidc_authorization_endpoint": "https://idp.example.com/oauth2/authorize",
    "oidc_token_endpoint": "https://idp.example.com/oauth2/token",
    "oidc_jwks_uri": "https://idp.example.com/.well-known/jwks.json",
    "oidc_client_id": "ai-video-platform",
    "oidc_redirect_uri": "https://platform.example.com/api/v1/auth/callback",
    "frontend_origin": "https://app.example.com",
    "account_management_url": "https://idp.example.com/account",
    "platform_owner_user_ids": ["production-owner-subject"],
    "input_asset_store": "huawei_obs",
    "input_asset_public_base_url": "https://platform.example.com",
    "huawei_obs_access_key_id": "production-obs-access-key",
    "huawei_obs_secret_access_key": "production-obs-secret-access-key-32-bytes!!",
    "huawei_obs_endpoint": "https://obs.cn-north-4.myhuaweicloud.com",
    "huawei_obs_bucket": "ai-video-input-assets",
}


def production_settings(**overrides: object) -> Settings:
    values = {**VALID_PRODUCTION_SETTINGS, **overrides}
    return Settings(**values)


def protected_staging_settings(**overrides: object) -> Settings:
    values = {
        **VALID_PRODUCTION_SETTINGS,
        "environment": "staging",
        **overrides,
    }
    return Settings(**values)


def relay_operations_settings(origin: str) -> dict[str, str]:
    return {
        "relay_operations_base_url": origin,
        "relay_tenant_id": "51bdf7c4-93a6-4b7f-92fc-c35df04f443f",
        "relay_operations_token": "operations-token-32-bytes-minimum-value!!",
        "relay_reconciliation_approval_key_id": "platform-approval-v1",
        "relay_reconciliation_approval_secret": (
            "approval-signing-secret-32-bytes-minimum!!"
        ),
    }


def test_development_identity_and_bootstrap_defaults_fail_closed():
    settings = Settings()

    assert settings.development_header_auth_enabled is False
    assert settings.enable_bootstrap is False
    assert settings.bootstrap_token is None


def test_protected_relay_identity_and_contract_are_code_owned() -> None:
    with pytest.raises(ValidationError, match="new-api-v1 / generations.v1"):
        production_settings(
            relay_backends={
                "new-api-v2": {
                    "base_url": "https://relay.example.com",
                    "client_id": "platform-production-service",
                    "api_key": "relay-api-key-32-bytes-minimum!!",
                    "contract_revision": "generations.v1",
                }
            },
            relay_default_backend_id="new-api-v2",
        )
    with pytest.raises(ValidationError, match="new-api-v1 / generations.v1"):
        production_settings(
            relay_backends={
                "new-api-v1": {
                    "base_url": "https://relay.example.com",
                    "client_id": "platform-production-service",
                    "api_key": "relay-api-key-32-bytes-minimum!!",
                    "contract_revision": "generations.v2",
                }
            },
            relay_default_contract_revision="generations.v2",
        )


def test_protected_operations_and_data_plane_share_one_canonical_origin() -> None:
    settings = production_settings(
        **relay_operations_settings("https://RELAY.EXAMPLE.COM:443")
    )
    assert settings.relay_operations_base_url == "https://RELAY.EXAMPLE.COM:443"

    with pytest.raises(ValidationError, match="same canonical origin"):
        production_settings(
            **relay_operations_settings("https://relay-operations.example.com")
        )


def test_development_bootstrap_requires_a_strong_explicit_token():
    with pytest.raises(ValidationError, match="BOOTSTRAP_TOKEN"):
        Settings(enable_bootstrap=True)
    with pytest.raises(ValidationError, match="BOOTSTRAP_TOKEN"):
        Settings(enable_bootstrap=True, bootstrap_token="too-short")

    settings = Settings(
        enable_bootstrap=True,
        bootstrap_token="local-bootstrap-secret-2026-08-14-aa",
    )
    assert settings.enable_bootstrap is True


def test_production_rejects_development_header_authentication():
    with pytest.raises(
        ValidationError,
        match="DEVELOPMENT_HEADER_AUTH_ENABLED",
    ):
        production_settings(development_header_auth_enabled=True)


def test_protected_platform_api_requires_oidc():
    with pytest.raises(ValidationError, match="OIDC_ENABLED"):
        production_settings(oidc_enabled=False)


def test_protected_platform_api_requires_explicit_self_signup_policy():
    values = dict(VALID_PRODUCTION_SETTINGS)
    values.pop("oidc_self_signup_enabled")
    with pytest.raises(ValidationError, match="OIDC_SELF_SIGNUP_ENABLED"):
        Settings(**values)


@pytest.mark.parametrize(
    "account_management_url",
    [
        None,
        "",
        "http://idp.example.com/account",
        "https://user:password@idp.example.com/account",
        "https://idp.example.com/account?tenant=one",
        "https://idp.example.com/account#security",
    ],
)
def test_protected_platform_api_requires_a_fixed_https_account_management_url(
    account_management_url: str | None,
) -> None:
    with pytest.raises(ValidationError, match="ACCOUNT_MANAGEMENT_URL"):
        production_settings(account_management_url=account_management_url)


def test_protected_staging_uses_the_same_fail_closed_defaults_as_production():
    settings = protected_staging_settings()

    assert settings.require_channel_cost_signature is True
    assert settings.allow_legacy_relay_artifact_download_response is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"development_header_auth_enabled": True}, "DEVELOPMENT_HEADER_AUTH_ENABLED"),
        ({"auto_create_tables": True}, "AUTO_CREATE_TABLES"),
        (
            {
                "enable_bootstrap": True,
                "bootstrap_token": "staging-bootstrap-canary-32-bytes-unique",
            },
            "ENABLE_BOOTSTRAP",
        ),
        ({"channel_cost_signature_required": False}, "CHANNEL_COST_SIGNATURE_REQUIRED"),
        (
            {"relay_allow_legacy_artifact_download_response": True},
            "legacy Relay artifact",
        ),
    ],
)
def test_protected_staging_rejects_development_safety_switches(
    overrides: dict[str, object], message: str
):
    with pytest.raises(ValidationError, match=message):
        protected_staging_settings(**overrides)


def test_protected_staging_rejects_http_callback_gateway_and_oauth_urls():
    with pytest.raises(ValidationError, match="RELAY_CALLBACK_PUBLIC_URL"):
        protected_staging_settings(
            relay_callback_public_url=(
                "http://platform.example.com/internal/relay-callbacks"
            ),
            relay_callback_signing_secrets={
                "new-api-v1": "staging-callback-secret-32-bytes-unique"
            },
        )

    with pytest.raises(ValidationError, match="DOWNLOAD_GATEWAY_REGISTRATION_URL"):
        protected_staging_settings(
            download_gateway_registration_url=(
                "http://download-gateway.example.com/internal/v1/download-tickets"
            )
        )

    with pytest.raises(ValidationError, match="Publishing OAuth URLs"):
        protected_staging_settings(
            publishing_oauth_callback_url=(
                "http://platform.example.com/api/v1/publishing/oauth/callback"
            ),
            publishing_oauth_success_url="https://app.example.com/publishing",
        )


@pytest.mark.parametrize(
    "authentication_methods",
    [
        ["pwd"],
        ["otp"],
        ["sms"],
        ["webauthn", "pwd"],
        ["WEBAUTHN", "webauthn"],
    ],
)
def test_production_admin_amr_is_a_fixed_phishing_resistant_allowlist(
    authentication_methods: list[str],
):
    with pytest.raises(ValidationError, match="PLATFORM_ADMIN_REQUIRED_AMR"):
        production_settings(
            platform_admin_required_amr=authentication_methods,
        )

    settings = production_settings(platform_admin_required_amr=["WebAuthn"])
    assert settings.platform_admin_required_amr == ["webauthn"]


@pytest.mark.parametrize(
    "owner_ids",
    [[], [""], [" owner-subject"], ["owner-subject", "owner-subject"]],
)
def test_production_requires_explicit_unique_platform_owner_subjects(owner_ids):
    with pytest.raises(ValidationError, match="PLATFORM_OWNER_USER_IDS"):
        production_settings(platform_owner_user_ids=owner_ids)


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///platform.db",
        "postgresql://platform:password@db.example.com/platform",
        "postgresql+psycopg2://platform:password@db.example.com/platform",
        "postgresql+asyncpg://platform:password@db.example.com/platform",
        "mysql+pymysql://platform:password@db.example.com/platform",
    ],
)
def test_production_rejects_non_sync_psycopg_database_urls(
    database_url: str,
):
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(database_url=database_url)


def test_production_accepts_installed_psycopg_driver():
    database_url = "postgresql+psycopg://platform:password@db.example.com/platform"
    settings = production_settings(database_url=database_url)
    assert settings.database_url == database_url
    assert settings.require_channel_cost_signature is True


def test_production_cannot_disable_channel_cost_event_signatures():
    with pytest.raises(ValidationError, match="CHANNEL_COST_SIGNATURE_REQUIRED"):
        production_settings(channel_cost_signature_required=False)


def test_development_channel_cost_signature_policy_is_explicit():
    assert Settings().require_channel_cost_signature is False
    assert (
        Settings(
            channel_cost_signature_required=True,
            channel_cost_signing_secret="development-channel-cost-secret",
        ).require_channel_cost_signature
        is True
    )


def test_required_channel_cost_signatures_need_dedicated_secret():
    with pytest.raises(ValidationError, match="CHANNEL_COST_SIGNING_SECRET"):
        Settings(channel_cost_signature_required=True)


def test_provider_alert_bridge_is_all_or_nothing_and_required_in_production():
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(provider_alert_signing_secret="a" * 32)
    with pytest.raises(ValidationError, match="signed provider alert receiver"):
        production_settings(
            provider_alert_signing_secret=None,
            provider_alert_forward_webhook_url=None,
            provider_alert_forward_signing_secret=None,
        )


def test_provider_alert_bridge_requires_independent_secrets():
    with pytest.raises(ValidationError, match="must be independent"):
        production_settings(
            provider_alert_signing_secret="same-provider-alert-secret-32-bytes!",
            provider_alert_forward_signing_secret=(
                "same-provider-alert-secret-32-bytes!"
            ),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://alerts.example.com/platform/provider",
        "HTTPS://alerts.example.com/platform/provider",
        "https://ALERTS.example.com/platform/provider",
        "https://alerts.example.com./platform/provider",
        "https://alerts.example.com/platform/provider?token=in-url",
        "https://127.0.0.1/platform/provider",
        "https://alerts.internal/platform/provider",
    ],
)
def test_production_provider_alert_bridge_rejects_unsafe_or_unnormalized_url(url):
    with pytest.raises(ValidationError, match="FORWARD_WEBHOOK_URL"):
        production_settings(provider_alert_forward_webhook_url=url)


def test_download_completion_source_secrets_are_all_or_nothing_and_distinct():
    with pytest.raises(ValidationError, match="configured together"):
        Settings(download_completion_edge_gateway_signing_secret="edge-secret")
    with pytest.raises(ValidationError, match="independent signing secrets"):
        Settings(
            download_completion_edge_gateway_signing_secret="same-secret",
            download_completion_obs_access_log_signing_secret="same-secret",
        )


def test_download_gateway_configuration_is_all_or_nothing_and_required_in_production():
    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            download_gateway_registration_url=(
                "http://download-gateway:8090/internal/v1/download-tickets"
            )
        )
    with pytest.raises(ValidationError, match="requires complete Download Gateway"):
        production_settings(
            download_gateway_registration_url=None,
            download_gateway_public_base_url=None,
            download_gateway_service_token=None,
            download_gateway_registration_signing_secret=None,
        )


@pytest.mark.parametrize(
    "decoded_key",
    [bytes(32), bytes(range(32)), b"A" * 32],
)
def test_production_rejects_known_or_low_entropy_gateway_attempt_keys(decoded_key):
    with pytest.raises(ValidationError, match="random, non-development AES-256"):
        production_settings(
            download_gateway_attempt_encryption_key_base64=(
                base64.b64encode(decoded_key).decode("ascii")
            )
        )


def test_gateway_attempt_key_must_be_distinct_after_base64_decoding():
    shared = "0123456789abcdef0123456789ABCDEF"
    with pytest.raises(ValidationError, match="must be distinct"):
        production_settings(
            internal_service_token=shared,
            download_gateway_attempt_encryption_key_base64=(
                base64.b64encode(shared.encode("utf-8")).decode("ascii")
            ),
        )


def test_gateway_lease_and_source_ttl_safety_boundaries_apply_in_all_environments():
    gateway = {
        "download_gateway_registration_url": (
            "http://download-gateway:8090/internal/v1/download-tickets"
        ),
        "download_gateway_public_base_url": "http://downloads.example.test",
        "download_gateway_service_token": "development-gateway-token",
        "download_gateway_registration_signing_secret": (
            "development-gateway-signing-secret"
        ),
        "download_gateway_attempt_encryption_key_base64": (
            "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
        ),
    }
    with pytest.raises(ValidationError, match="strictly greater"):
        Settings(
            **gateway,
            download_gateway_timeout_seconds=20,
            download_gateway_registration_lease_margin_seconds=10,
            download_gateway_registration_lease_seconds=30,
        )
    accepted = Settings(
        **gateway,
        download_gateway_timeout_seconds=20,
        download_gateway_registration_lease_margin_seconds=10,
        download_gateway_registration_lease_seconds=31,
        download_gateway_ticket_ttl_seconds=300,
        download_gateway_source_ttl_margin_seconds=60,
        relay_artifact_signed_url_ttl_seconds=360,
    )
    assert accepted.download_gateway_registration_lease_seconds == 31
    with pytest.raises(ValidationError, match="must be at least"):
        Settings(
            **gateway,
            download_gateway_ticket_ttl_seconds=300,
            download_gateway_source_ttl_margin_seconds=60,
            relay_artifact_signed_url_ttl_seconds=359,
        )
    with pytest.raises(ValidationError, match="greater than or equal to 30"):
        Settings(**gateway, download_gateway_ticket_ttl_seconds=29)
    with pytest.raises(ValidationError, match="greater than or equal to 30"):
        Settings(**gateway, download_gateway_source_ttl_margin_seconds=29)


@pytest.mark.parametrize(
    "registration_url",
    [
        "http://download-gateway.example.com/internal/v1/download-tickets",
        "https://127.0.0.1/internal/v1/download-tickets",
        "https://download-gateway.internal/internal/v1/download-tickets",
        "https://user:secret@download-gateway.example.com/internal/v1/download-tickets",
        "https://download-gateway.example.com/other",
        "https://download-gateway.example.com/internal/v1/download-tickets?token=x",
    ],
)
def test_production_rejects_unsafe_gateway_registration_urls(
    registration_url: str,
):
    with pytest.raises(ValidationError, match="DOWNLOAD_GATEWAY_REGISTRATION_URL"):
        production_settings(download_gateway_registration_url=registration_url)


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://downloads.example.com",
        "https://127.0.0.1",
        "https://downloads.internal",
        "https://user:secret@downloads.example.com",
        "https://downloads.example.com/tickets",
        "https://downloads.example.com?tenant=one",
    ],
)
def test_production_rejects_unsafe_gateway_public_origins(
    public_base_url: str,
):
    with pytest.raises(ValidationError, match="DOWNLOAD_GATEWAY_PUBLIC_BASE_URL"):
        production_settings(download_gateway_public_base_url=public_base_url)


def test_legacy_relay_download_response_is_disabled_unless_explicitly_enabled():
    assert Settings().allow_legacy_relay_artifact_download_response is False
    assert (
        Settings(
            relay_legacy_compatibility_enabled=True,
            relay_allow_legacy_artifact_download_response=True,
        ).allow_legacy_relay_artifact_download_response
        is True
    )
    with pytest.raises(ValidationError, match="cannot allow legacy"):
        production_settings(relay_allow_legacy_artifact_download_response=True)


def test_production_rejects_reused_security_secrets():
    with pytest.raises(ValidationError, match="must be distinct"):
        production_settings(
            channel_cost_signing_secret=(
                VALID_PRODUCTION_SETTINGS["internal_service_token"]
            )
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        production_settings(
            download_edge_completion_service_token=(
                VALID_PRODUCTION_SETTINGS["internal_service_token"]
            )
        )


@pytest.mark.parametrize(
    "cors_origins",
    [
        [],
        ["*"],
        ["https://*.example.com"],
        ["http://app.example.com"],
        ["https://user:password@app.example.com"],
        ["https://app.example.com/"],
        ["https://app.example.com/path"],
        ["https://app.example.com?tenant=one"],
        ["https://app.example.com#fragment"],
        [" https://app.example.com"],
    ],
)
def test_production_rejects_unsafe_or_non_origin_cors_entries(
    cors_origins: list[str],
):
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        production_settings(cors_origins=cors_origins)


def test_production_accepts_only_the_exact_frontend_origin():
    settings = production_settings(cors_origins=["https://app.example.com"])
    assert settings.cors_origins == ["https://app.example.com"]


def test_production_accepts_same_schemeful_site_with_multi_label_public_suffix():
    settings = production_settings(
        cors_origins=["https://app.customer.co.uk"],
        frontend_origin="https://app.customer.co.uk",
        oidc_redirect_uri=(
            "https://platform.customer.co.uk/api/v1/auth/callback"
        ),
        input_asset_public_base_url="https://platform.customer.co.uk",
    )
    assert settings.frontend_origin == "https://app.customer.co.uk"


@pytest.mark.parametrize(
    ("frontend_origin", "platform_origin"),
    [
        ("https://app.example.net", "https://platform.example.com"),
        ("https://tenant-a.github.io", "https://tenant-b.github.io"),
    ],
)
def test_production_rejects_cross_schemeful_site_browser_origins(
    frontend_origin: str,
    platform_origin: str,
):
    with pytest.raises(ValidationError, match="schemeful site"):
        production_settings(
            cors_origins=[frontend_origin],
            frontend_origin=frontend_origin,
            oidc_redirect_uri=platform_origin + "/api/v1/auth/callback",
            input_asset_public_base_url=platform_origin,
        )


@pytest.mark.parametrize(
    "cors_origins",
    [
        ["https://app.example.com", "https://admin.example.com:8443"],
        ["https://app.example.com", "https://app.example.com"],
    ],
)
def test_production_rejects_extra_or_duplicate_browser_origins(
    cors_origins: list[str],
):
    with pytest.raises(ValidationError, match="single FRONTEND_ORIGIN"):
        production_settings(cors_origins=cors_origins)


@pytest.mark.parametrize(
    "field_name",
    [
        "oidc_enabled",
        "oidc_self_signup_enabled",
        "auth_legacy_bearer_enabled",
    ],
)
@pytest.mark.parametrize(
    "value",
    [1, 0, "1", "0", "on", "yes", "TRUE", " false", "true ", ""],
)
def test_browser_auth_boolean_environment_values_are_strict(
    field_name: str,
    value: object,
):
    with pytest.raises(ValidationError, match="booleans accept only true or false"):
        production_settings(**{field_name: value})


def test_browser_auth_boolean_environment_values_accept_exact_lowercase():
    settings = production_settings(
        oidc_enabled="true",
        oidc_self_signup_enabled="false",
        auth_legacy_bearer_enabled="false",
    )
    assert settings.oidc_enabled is True
    assert settings.oidc_self_signup_enabled is False
    assert settings.auth_legacy_bearer_enabled is False


@pytest.mark.parametrize(
    "relay_backend_url",
    [
        "http://relay.example.com",
        "relay.example.com",
        "https://",
        "ftp://relay.example.com",
        "https://user:password@relay.example.com",
        "https://relay.example.com/",
        "https://relay.example.com/v1",
        "https://relay.example.com?tenant=one",
        "https://relay.example.com#fragment",
        " https://relay.example.com",
    ],
)
def test_production_rejects_non_root_or_unsafe_relay_url(
    relay_backend_url: str,
):
    with pytest.raises(ValidationError, match="Relay backend"):
        production_settings(
            relay_backends={
                "new-api-v1": {
                    "base_url": relay_backend_url,
                    "client_id": "platform-production-service",
                    "api_key": "relay-api-key-32-bytes-minimum!!",
                    "contract_revision": "generations.v1",
                }
            }
        )


@pytest.mark.parametrize(
    "relay_client_id",
    [
        "",
        "   ",
        "client",
        "change-me",
        "your-client-id",
        "platform service",
        " platform-service",
    ],
)
def test_production_rejects_placeholder_or_whitespace_client_ids(
    relay_client_id: str,
):
    with pytest.raises(ValidationError, match="RELAY_CLIENT_ID"):
        production_settings(relay_client_id=relay_client_id)


@pytest.mark.parametrize(
    ("setting_name", "value"),
    [
        ("relay_api_key", "short"),
        ("relay_api_key", "change-me-" + "x" * 40),
        (
            "relay_api_key",
            "REPLACE_WITH_RANDOM_RELAY_API_KEY_AT_LEAST_32_BYTES",
        ),
        ("internal_service_token", "short"),
        ("internal_service_token", "replace-me-" + "x" * 40),
        (
            "internal_service_token",
            "REPLACE_WITH_RANDOM_INTERNAL_TOKEN_AT_LEAST_32_BYTES",
        ),
    ],
)
def test_production_rejects_short_or_placeholder_secrets(
    setting_name: str,
    value: str,
):
    with pytest.raises(ValidationError):
        production_settings(**{setting_name: value})


def test_production_secret_length_is_measured_in_utf8_bytes():
    settings = production_settings(
        relay_backends={
            "new-api-v1": {
                "base_url": "https://relay.example.com",
                "client_id": "platform-production-service",
                "api_key": "密" * 11,
                "contract_revision": "generations.v1",
            }
        },
        internal_service_token="令" * 11,
    )
    assert (
        len(
            settings.relay_backends["new-api-v1"]
            .api_key.get_secret_value()
            .encode("utf-8")
        )
        == 33
    )
    assert len(settings.internal_service_token.encode("utf-8")) == 33


def test_development_keeps_local_defaults_and_short_credentials_compatible():
    settings = Settings(
        environment="development",
        database_url="sqlite:///platform.db",
        cors_origins=["http://localhost:5173"],
        relay_base_url="http://relay-api:8000",
        relay_client_id="client",
        relay_api_key="secret",
        relay_legacy_compatibility_enabled=True,
        internal_service_token="internal",
    )
    assert settings.database_url == "sqlite:///platform.db"
    assert settings.cors_origins == ["http://localhost:5173"]


def test_production_requires_huawei_obs_for_private_input_assets():
    with pytest.raises(ValidationError, match="INPUT_ASSET_STORE"):
        production_settings(input_asset_store="filesystem")


@pytest.mark.parametrize(
    "overrides",
    [
        {"huawei_obs_access_key_id": None},
        {"huawei_obs_secret_access_key": None},
        {"huawei_obs_secret_access_key": "short"},
        {"huawei_obs_endpoint": None},
        {"huawei_obs_endpoint": "http://obs.example.com"},
        {"huawei_obs_endpoint": "https://user:secret@obs.example.com"},
        {"huawei_obs_endpoint": "https://obs.example.com"},
        {"huawei_obs_bucket": None},
        {"huawei_obs_bucket": "INVALID_BUCKET"},
    ],
)
def test_production_rejects_incomplete_or_unsafe_huawei_obs_config(
    overrides: dict[str, object],
):
    with pytest.raises(ValidationError):
        production_settings(**overrides)


@pytest.mark.parametrize(
    "token",
    ["", " token", "temporary token", "replace-with-temporary-token"],
)
def test_production_rejects_unsafe_optional_huawei_obs_security_token(token):
    with pytest.raises(ValidationError, match="HUAWEI_OBS_SECURITY_TOKEN"):
        production_settings(huawei_obs_security_token=token)


def test_production_accepts_normalized_optional_huawei_obs_security_token():
    settings = production_settings(
        huawei_obs_security_token="temporary-security-token-value"
    )
    assert settings.huawei_obs_security_token == "temporary-security-token-value"


@pytest.mark.parametrize(
    "setting_name",
    ["input_asset_public_base_url", "input_asset_relay_base_url"],
)
def test_filesystem_input_asset_base_urls_reject_credentials_and_paths(
    setting_name: str,
):
    with pytest.raises(ValidationError, match=setting_name.upper()):
        Settings(
            environment="development",
            input_asset_store="filesystem",
            input_asset_signing_secret="development-signing-secret",
            **{setting_name: "http://user:password@example.com/private"},
        )


def test_relay_callback_configuration_is_all_or_nothing():
    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            relay_callback_public_url=(
                "http://platform-api:8000/internal/relay-callbacks"
            )
        )
    with pytest.raises(ValidationError, match="configured together"):
        Settings(relay_callback_signing_secrets={"new-api-v1": "x" * 32})


@pytest.mark.parametrize(
    "url",
    [
        "http://platform.example.com/internal/relay-callbacks",
        "https://127.0.0.1/internal/relay-callbacks",
        "https://platform.internal/internal/relay-callbacks",
        "https://user:secret@platform.example.com/internal/relay-callbacks",
        "https://platform.example.com/other",
        "https://platform.example.com/internal/relay-callbacks?secret=value",
    ],
)
def test_production_rejects_unsafe_relay_callback_urls(url: str):
    with pytest.raises(ValidationError, match="RELAY_CALLBACK_PUBLIC_URL"):
        production_settings(
            relay_callback_public_url=url,
            relay_callback_signing_secrets={
                "new-api-v1": "callback-production-secret-32-bytes!!"
            },
        )


def test_production_accepts_exact_https_relay_callback_configuration():
    settings = production_settings(
        relay_callback_public_url=(
            "https://platform.example.com/internal/relay-callbacks"
        ),
        relay_callback_signing_secrets={
            "new-api-v1": "callback-production-secret-32-bytes!!"
        },
    )
    assert settings.relay_callback_max_age_seconds == 300


def test_production_rejects_callback_secret_reused_as_its_backend_api_key():
    reused_secret = "new-backend-api-and-callback-secret-32-bytes!!"
    with pytest.raises(ValidationError, match="distinct"):
        production_settings(
            relay_backends={
                "new-api-v1": {
                    "base_url": "https://relay-new.example.com",
                    "client_id": "platform-new-api",
                    "api_key": reused_secret,
                    "contract_revision": "generations.v1",
                }
            },
            relay_callback_public_url=(
                "https://platform.example.com/internal/relay-callbacks"
            ),
            relay_callback_signing_secrets={"new-api-v1": reused_secret},
        )


def test_production_rejects_multiple_relay_backends():
    shared_secret = "shared-backend-callback-secret-32-bytes!!"
    with pytest.raises(ValidationError, match="exactly one native new-api"):
        production_settings(
            relay_backends={
                "new-api-v1": {
                    "base_url": "https://relay-new.example.com",
                    "client_id": "platform-new-api",
                    "api_key": "new-backend-api-key-32-bytes-minimum!!",
                    "contract_revision": "generations.v1",
                },
                "new-api-v2": {
                    "base_url": "https://relay-new.example.com",
                    "client_id": "platform-new-api-v2",
                    "api_key": "second-backend-api-key-32-bytes-minimum!!",
                    "contract_revision": "generations.v1",
                },
            },
            relay_callback_public_url=(
                "https://platform.example.com/internal/relay-callbacks"
            ),
            relay_callback_signing_secrets={
                "new-api-v1": shared_secret,
                "new-api-v2": shared_secret,
            },
        )
