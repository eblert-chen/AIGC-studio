from __future__ import annotations

import inspect
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from platform_api.dependencies import get_db
from platform_api.main import create_app

from .conftest import TEST_BOOTSTRAP_TOKEN, bootstrap
from .test_input_assets import PNG_BYTES, upload_asset


_PREFIX = "/api/v1/companies/{company_id}/assets"
_ROUTES = [
    ("upload_input_asset", "POST", _PREFIX, 201, "assets.manage", True),
    ("list_input_assets", "GET", _PREFIX, 200, "assets.read", True),
    ("preview_input_asset", "GET", _PREFIX + "/{asset_id}/preview", 200, "assets.read", True),
    ("download_input_asset", "GET", _PREFIX + "/{asset_id}/download", 200, "assets.read", True),
    ("disable_input_asset", "DELETE", _PREFIX + "/{asset_id}", 204, "assets.manage", True),
    ("read_signed_input_asset", "GET", "/api/v1/input-assets/{asset_id}/content", 200, None, False),
]


def test_company_input_asset_router_preserves_route_and_dependency_contract(app):
    expected = {(path, method) for _, method, path, _, _, _ in _ROUTES}
    routes = [
        route for route in app.routes
        if isinstance(route, APIRoute)
        and any((route.path, method) in expected for method in route.methods)
    ]
    assert len(routes) == len(_ROUTES)
    assert [route.name for route in routes] == [item[0] for item in _ROUTES]
    for route, (name, method, path, status, permission, visible) in zip(routes, _ROUTES, strict=True):
        assert route.endpoint.__module__ == "platform_api.routers.company_input_assets"
        assert route.name == name
        assert route.path == path
        assert route.methods == {method}
        assert (route.status_code or 200) == status
        assert route.include_in_schema is visible
        assert route.unique_id == re.sub(r"\W", "_", name + path) + "_" + method.lower()
        database = [item for item in route.dependant.dependencies if item.call is get_db]
        assert len(database) == 1
        assert database[0].scope == "function"
        access = [item for item in route.dependant.dependencies if item.call is not get_db]
        if permission is None:
            # Provider-facing delivery continues to use the signed capability,
            # not a browser tenant header or a newly introduced public grant.
            assert access == []
        else:
            assert len(access) == 1
            assert inspect.getclosurevars(access[0].call).nonlocals == {
                "permission_code": permission,
            }


def test_company_input_asset_factory_keeps_each_apps_limits_store_and_signer_isolated(
    app, client, tenant, tenant_headers, tmp_path,
):
    second_engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    second_settings = app.state.settings.model_copy(update={
        "input_asset_filesystem_root": str(tmp_path / "second-app-assets"),
        "input_asset_max_bytes": len(PNG_BYTES) - 1,
        "input_asset_signed_url_seconds": 37,
        "input_asset_signing_secret": "second-app-input-asset-signing-secret",
    })
    second_app = create_app(settings=second_settings, engine=second_engine)
    try:
        with TestClient(second_app, headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN}) as second:
            second_tenant = bootstrap(second, "input-router-second-app")
            second_headers = {
                "X-Company-ID": second_tenant["company_id"],
                "X-User-ID": second_tenant["user_id"],
            }
            first_upload = upload_asset(client, tenant, tenant_headers)
            assert first_upload.status_code == 201, first_upload.text
            rejected = upload_asset(second, second_tenant, second_headers)
            assert rejected.status_code == 413, rejected.text
            second_settings.input_asset_max_bytes = 64 * 1024
            second_upload = upload_asset(second, second_tenant, second_headers)
            assert second_upload.status_code == 201, second_upload.text

            first_preview = client.get(
                f"/api/v1/companies/{tenant['company_id']}/assets/{first_upload.json()['id']}/preview",
                headers=tenant_headers,
            )
            second_preview = second.get(
                f"/api/v1/companies/{second_tenant['company_id']}/assets/{second_upload.json()['id']}/preview",
                headers=second_headers,
            )
            assert first_preview.status_code == second_preview.status_code == 200
            assert first_preview.json()["expires_seconds"] == app.state.settings.input_asset_signed_url_seconds
            assert second_preview.json()["expires_seconds"] == 37
            first_url = first_preview.json()["url"]
            second_url = second_preview.json()["url"]
            for browser, url in [(client, first_url), (second, second_url)]:
                opened = browser.get(url)
                assert opened.status_code == 200, opened.text
                assert opened.content == PNG_BYTES
                assert opened.headers["cache-control"] == "private, no-store"
                assert opened.headers["x-content-type-options"] == "nosniff"
                assert opened.headers["content-type"].startswith("image/png")
                assert opened.headers["content-disposition"].startswith("inline;")

            # Retarget a valid second-app signature to an existing first-app
            # asset. Even when the DB row exists, the wrong signer cannot serve it.
            first_parts, second_parts = urlsplit(first_url), urlsplit(second_url)
            query = parse_qs(second_parts.query)
            swapped = urlunsplit((
                first_parts.scheme, first_parts.netloc, first_parts.path,
                urlencode({key: values[0] for key, values in query.items()}), "",
            ))
            assert client.get(swapped).status_code == 404
            assert second.get(first_url).status_code == 404
            assert client.get(second_url).status_code == 404
    finally:
        second_engine.dispose()
