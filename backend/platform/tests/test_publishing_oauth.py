from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

from sqlalchemy import select

from platform_api.models import PublisherConnection, PublisherOAuthSession
from platform_api.publishing_adapters import (
    PublicationReceipt,
    PublisherOAuthGrant,
    build_publisher_registry,
)

from .test_publishing import _disable_auto_publish, _enable_auto_publish


class OAuthPublisherAdapter:
    provider = "douyin"
    oauth_display_name = "抖音"
    production_ready = True
    is_mock = False

    def submit(self, request) -> PublicationReceipt:  # pragma: no cover - contract only
        return PublicationReceipt(external_post_id=f"post-{request.job_id}")

    def build_authorization_url(self, *, state: str, redirect_uri: str) -> str:
        return "https://oauth.publisher.example/authorize?" + urlencode(
            {"state": state, "redirect_uri": redirect_uri, "client_id": "public-client"}
        )

    def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> PublisherOAuthGrant:
        assert code == "one-time-code"
        assert redirect_uri.endswith("/api/v1/publishing/oauth/callback")
        return PublisherOAuthGrant(
            external_account_id="douyin-account-42",
            display_name="品牌官方账号",
            credential_reference="secret-store://publishing/douyin/account-42",
        )


def _configure_oauth(app) -> None:
    app.state.settings.publishing_oauth_callback_url = (
        "https://platform.example/api/v1/publishing/oauth/callback"
    )
    app.state.settings.publishing_oauth_success_url = (
        "https://studio.example/publishing"
    )
    app.state.publisher_registry = build_publisher_registry(
        environment="development",
        adapters=[OAuthPublisherAdapter()],
    )


def test_publisher_oauth_is_entitled_one_time_and_keeps_tokens_server_side(
    app, client, tenant, tenant_headers
):
    _configure_oauth(app)
    company_id = tenant["company_id"]
    providers_url = (
        f"/api/v1/companies/{company_id}/publishing/connections/oauth/providers"
    )
    start_url = (
        f"/api/v1/companies/{company_id}/publishing/connections/oauth/start"
    )

    providers = client.get(providers_url, headers=tenant_headers)
    assert providers.status_code == 200
    assert providers.json() == [{"provider": "douyin", "display_name": "抖音"}]

    denied = client.post(
        start_url,
        headers=tenant_headers,
        json={"provider": "douyin"},
    )
    assert denied.status_code == 403

    _enable_auto_publish(app, company_id)
    started = client.post(
        start_url,
        headers=tenant_headers,
        json={"provider": "douyin"},
    )
    assert started.status_code == 200, started.text
    authorization_url = started.json()["authorization_url"]
    query = parse_qs(urlsplit(authorization_url).query)
    state = query["state"][0]
    assert query["redirect_uri"] == [app.state.settings.publishing_oauth_callback_url]

    with app.state.session_factory() as session:
        oauth_session = session.scalar(select(PublisherOAuthSession))
        assert oauth_session is not None
        assert oauth_session.state_sha256 != state
        assert state not in oauth_session.state_sha256

    callback = client.get(
        "/api/v1/publishing/oauth/callback",
        params={"state": state, "code": "one-time-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == (
        "https://studio.example/publishing?publishing_oauth=connected&provider=douyin"
    )

    replay = client.get(
        "/api/v1/publishing/oauth/callback",
        params={"state": state, "code": "one-time-code"},
        follow_redirects=False,
    )
    assert replay.status_code == 303
    assert "publishing_oauth=failed" in replay.headers["location"]
    assert "invalid_or_expired_state" in replay.headers["location"]

    connections = client.get(
        f"/api/v1/companies/{company_id}/publishing/connections",
        headers=tenant_headers,
    )
    assert connections.status_code == 200
    assert len(connections.json()) == 1
    assert connections.json()[0]["display_name"] == "品牌官方账号"
    assert "config" not in connections.json()[0]

    with app.state.session_factory() as session:
        connection = session.scalar(select(PublisherConnection))
        assert connection is not None
        assert connection.config == {
            "credential_reference": "secret-store://publishing/douyin/account-42"
        }


def test_publisher_oauth_provider_denial_consumes_state(
    app, client, tenant, tenant_headers
):
    _configure_oauth(app)
    _enable_auto_publish(app, tenant["company_id"])
    started = client.post(
        f"/api/v1/companies/{tenant['company_id']}/publishing/connections/oauth/start",
        headers=tenant_headers,
        json={"provider": "douyin"},
    )
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]

    denied = client.get(
        "/api/v1/publishing/oauth/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert "reason=provider_denied" in denied.headers["location"]

    replay = client.get(
        "/api/v1/publishing/oauth/callback",
        params={"state": state, "code": "one-time-code"},
        follow_redirects=False,
    )
    assert "invalid_or_expired_state" in replay.headers["location"]


def test_publisher_oauth_rechecks_entitlement_before_creating_connection(
    app, client, tenant, tenant_headers
):
    _configure_oauth(app)
    _enable_auto_publish(app, tenant["company_id"])
    started = client.post(
        f"/api/v1/companies/{tenant['company_id']}/publishing/connections/oauth/start",
        headers=tenant_headers,
        json={"provider": "douyin"},
    )
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    _disable_auto_publish(app, tenant["company_id"])

    callback = client.get(
        "/api/v1/publishing/oauth/callback",
        params={"state": state, "code": "one-time-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "reason=authorization_revoked" in callback.headers["location"]
    with app.state.session_factory() as session:
        assert session.scalar(select(PublisherConnection)) is None
