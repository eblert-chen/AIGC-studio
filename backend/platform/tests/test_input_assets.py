from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from platform_api.asset_storage import (
    HuaweiObsInputAssetStore,
    InputAssetStorageError,
)
from platform_api.config import Settings
from platform_api.models import InputAsset, RelaySubmissionOutbox, TaskInputAsset
from platform_api.services.input_assets import _content_signature_matches
from .conftest import bootstrap
from .test_relay_boundary import accepted_response
from .test_wallet_and_tasks import seed_model

PNG_BYTES = b"\x89PNG\r\n\x1a\nprivate-input"


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        ("image/png", PNG_BYTES),
        ("image/jpeg", b"\xff\xd8\xff\xe0jpeg"),
        ("image/gif", b"GIF89aimage"),
        ("image/webp", b"RIFF\x08\x00\x00\x00WEBPimage"),
        ("image/avif", b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifmif1"),
        ("image/heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1"),
        ("video/mp4", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"),
        ("video/quicktime", b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "),
        ("video/mpeg", b"\x00\x00\x01\xb3mpeg-video"),
        ("video/webm", b"\x1a\x45\xdf\xa3webm-video"),
        ("audio/aac", b"\xff\xf1aac-audio"),
        ("audio/flac", b"fLaCflac-audio"),
        ("audio/mp4", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00isomM4A "),
        ("audio/mpeg", b"ID3mp3-audio"),
        ("audio/ogg", b"OggSogg-audio"),
        ("audio/wav", b"RIFF\x08\x00\x00\x00WAVEaudio"),
        ("audio/webm", b"\x1a\x45\xdf\xa3webm-audio"),
    ],
)
def test_supported_input_asset_signatures_are_recognized(
    tmp_path,
    content_type,
    content,
):
    path = tmp_path / "asset.bin"
    path.write_bytes(content)
    assert _content_signature_matches(path, content_type)


def test_truncated_iso_bmff_header_is_rejected(tmp_path):
    path = tmp_path / "truncated.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    assert not _content_signature_matches(path, "video/mp4")


def upload_asset(
    client,
    tenant,
    headers,
    *,
    filename: str = "face.png",
    content: bytes = PNG_BYTES,
    content_type: str = "image/png",
    media_type: str = "image",
    idempotency_key: str = "asset-upload-0001",
):
    return client.post(
        f"/api/v1/companies/{tenant['company_id']}/assets",
        headers={**headers, "Idempotency-Key": idempotency_key},
        files={"file": (filename, content, content_type)},
        data={"media_type": media_type},
    )


def test_upload_list_short_signed_access_disable_and_idempotency(
    app, client, tenant, tenant_headers
):
    uploaded = upload_asset(client, tenant, tenant_headers)
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["company_id"] == tenant["company_id"]
    assert asset["uploaded_by_user_id"] == tenant["user_id"]
    assert asset["original_filename"] == "face.png"
    assert asset["media_type"] == "image"
    assert asset["content_type"] == "image/png"
    assert asset["size_bytes"] == len(PNG_BYTES)
    assert len(asset["sha256"]) == 64
    assert asset["status"] == "active"
    assert "object_key" not in asset
    assert "storage_backend" not in asset

    replay = upload_asset(client, tenant, tenant_headers)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == asset["id"]
    with app.state.session_factory() as session:
        assert len(session.scalars(select(InputAsset)).all()) == 1

    conflict = upload_asset(
        client,
        tenant,
        tenant_headers,
        content=PNG_BYTES + b"different",
    )
    assert conflict.status_code == 409

    listed = client.get(
        f"/api/v1/companies/{tenant['company_id']}/assets",
        headers=tenant_headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [asset["id"]]

    preview = client.get(
        f"/api/v1/companies/{tenant['company_id']}/assets/{asset['id']}/preview",
        headers=tenant_headers,
    )
    assert preview.status_code == 200, preview.text
    preview_url = preview.json()["url"]
    assert preview_url.startswith("http://testserver/api/v1/input-assets/")
    opened = client.get(preview_url)
    assert opened.status_code == 200
    assert opened.content == PNG_BYTES
    assert opened.headers["content-type"].startswith("image/png")
    assert opened.headers["cache-control"] == "private, no-store"
    assert opened.headers["x-content-type-options"] == "nosniff"
    assert opened.headers["content-disposition"].startswith("inline;")

    tampered = httpx.URL(preview_url).copy_set_param("expires", "4102444800")
    assert client.get(str(tampered)).status_code == 404

    download = client.get(
        f"/api/v1/companies/{tenant['company_id']}/assets/{asset['id']}/download",
        headers=tenant_headers,
    )
    downloaded = client.get(download.json()["url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-disposition"].startswith("attachment;")

    disabled = client.delete(
        f"/api/v1/companies/{tenant['company_id']}/assets/{asset['id']}",
        headers=tenant_headers,
    )
    assert disabled.status_code == 204
    assert client.get(preview_url).status_code == 404
    assert (
        client.get(
            f"/api/v1/companies/{tenant['company_id']}/assets",
            headers=tenant_headers,
        ).json()
        == []
    )
    disabled_list = client.get(
        f"/api/v1/companies/{tenant['company_id']}/assets",
        headers=tenant_headers,
        params={"status": "disabled"},
    )
    assert [item["id"] for item in disabled_list.json()] == [asset["id"]]


def test_assets_are_company_isolated_and_permissions_are_granular(
    client, tenant, tenant_headers
):
    asset = upload_asset(
        client,
        tenant,
        tenant_headers,
        idempotency_key="asset-isolation-0001",
    ).json()
    other = bootstrap(client, "asset-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    assert (
        client.get(
            f"/api/v1/companies/{other['company_id']}/assets",
            headers=other_headers,
        ).json()
        == []
    )
    hidden = client.get(
        f"/api/v1/companies/{other['company_id']}/assets/{asset['id']}/preview",
        headers=other_headers,
    )
    assert hidden.status_code == 404
    forged = client.get(
        f"/api/v1/companies/{tenant['company_id']}/assets",
        headers=other_headers,
    )
    assert forged.status_code == 403

    member_response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/members",
        headers=tenant_headers,
        json={
            "email": "asset-operator@example.com",
            "display_name": "Asset Operator",
        },
    )
    assert member_response.status_code == 201
    member = member_response.json()
    member_headers = {
        "X-Company-ID": tenant["company_id"],
        "X-User-ID": member["user_id"],
    }
    assert [role["system_key"] for role in member["roles"]] == ["operator"]
    allowed = upload_asset(
        client,
        tenant,
        member_headers,
        idempotency_key="asset-member-default-operator",
    )
    assert allowed.status_code == 201, allowed.text
    override = client.put(
        (
            f"/api/v1/companies/{tenant['company_id']}/members/"
            f"{member['membership_id']}/permission"
        ),
        headers=tenant_headers,
        json={"permission_code": "assets.manage", "effect": "deny"},
    )
    assert override.status_code == 200
    denied = upload_asset(
        client,
        tenant,
        member_headers,
        idempotency_key="asset-member-denied",
    )
    assert denied.status_code == 403
    assert (
        client.delete(
            (
                f"/api/v1/companies/{tenant['company_id']}/members/"
                f"{member['membership_id']}/permission/assets.manage"
            ),
            headers=tenant_headers,
        ).status_code
        == 204
    )
    allowed_again = upload_asset(
        client,
        tenant,
        member_headers,
        idempotency_key="asset-member-allowed",
    )
    assert allowed_again.status_code == 201, allowed_again.text


def test_upload_rejects_oversize_unsupported_and_mismatched_media(
    app, client, tenant, tenant_headers
):
    previous_limit = app.state.settings.input_asset_max_bytes
    app.state.settings.input_asset_max_bytes = 4
    try:
        too_large = upload_asset(
            client,
            tenant,
            tenant_headers,
            content=b"12345",
            idempotency_key="asset-too-large",
        )
    finally:
        app.state.settings.input_asset_max_bytes = previous_limit
    assert too_large.status_code == 413
    unsupported = upload_asset(
        client,
        tenant,
        tenant_headers,
        filename="payload.svg",
        content=b"<svg/>",
        content_type="image/svg+xml",
        idempotency_key="asset-unsupported",
    )
    assert unsupported.status_code == 422
    mismatch = upload_asset(
        client,
        tenant,
        tenant_headers,
        media_type="video",
        idempotency_key="asset-mismatch",
    )
    assert mismatch.status_code == 422
    invalid_key = upload_asset(
        client,
        tenant,
        tenant_headers,
        idempotency_key="has spaces",
    )
    assert invalid_key.status_code == 422


@pytest.mark.parametrize(
    ("claimed_type", "media_type", "content"),
    [
        ("image/png", "image", b"<html><script>alert(1)</script></html>"),
        ("image/jpeg", "image", PNG_BYTES),
        ("video/mp4", "video", b"not-an-mp4-container"),
        ("audio/mpeg", "audio", b"not-an-mp3-stream"),
    ],
)
def test_upload_rejects_bytes_that_do_not_match_claimed_content_type(
    client,
    tenant,
    tenant_headers,
    claimed_type,
    media_type,
    content,
):
    response = upload_asset(
        client,
        tenant,
        tenant_headers,
        filename="spoofed.bin",
        content=content,
        content_type=claimed_type,
        media_type=media_type,
        idempotency_key=f"signature-spoof-{media_type}-{claimed_type.replace('/', '-')}",
    )
    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Uploaded bytes do not match the declared content type"
    )


class CaptureRelayClient:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, payload, *, idempotency_key, request_id=None):
        self.calls.append((payload, idempotency_key, request_id))
        return accepted_response("77777777-7777-4777-8777-777777777777")


def test_task_keeps_private_ids_and_dispatches_fresh_restricted_urls(
    app, client, tenant, tenant_headers, internal_headers
):
    asset = upload_asset(
        client,
        tenant,
        tenant_headers,
        idempotency_key="asset-task-input",
    ).json()
    model_id = seed_model(
        app,
        tenant["company_id"],
        capability_key="image-to-video",
        capability_config={
            "durations": [5, 10],
            "ratios": ["16:9", "9:16"],
            "input_media_types": ["image"],
            "max_images": 1,
        },
    )
    recharge = client.post(
        f"/api/v1/companies/{tenant['company_id']}/wallet/recharge",
        headers=tenant_headers,
        json={
            "amount_cents": 1000,
            "idempotency_key": "asset-task-recharge",
        },
    )
    assert recharge.status_code == 200
    task_response = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers={**tenant_headers, "X-Request-ID": "asset-task-request"},
        json={
            "model_id": model_id,
            "idempotency_key": "asset-task-create",
            "request_payload": {
                "mode": "image_to_video",
                "prompt": "Animate this private image",
                "duration_seconds": 5,
                "assets": [{"asset_id": asset["id"], "media_type": "image"}],
            },
        },
    )
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    assert task["request_payload"]["assets"] == [
        {"asset_id": asset["id"], "media_type": "image"}
    ]
    assert "url" not in str(task["request_payload"])

    with app.state.session_factory() as session:
        link = session.scalar(
            select(TaskInputAsset).where(TaskInputAsset.task_id == task["id"])
        )
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == task["id"]
            )
        )
        assert link is not None and link.asset_id == asset["id"]
        assert outbox is not None
        assert outbox.relay_payload["metadata"]["_platform_input_assets"] == [
            {"asset_id": asset["id"], "media_type": "image"}
        ]
        assert outbox.relay_payload["inputs"]["assets"] == []

    capture = CaptureRelayClient()
    app.state.relay_client = capture
    dispatched = client.post("/internal/relay/dispatch-once", headers=internal_headers)
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "sent"
    payload, _, request_id = capture.calls[0]
    assert str(payload.inputs.assets[0].url).startswith(
        "http://platform-internal:8000/"
    )
    assert "_platform_input_assets" not in payload.metadata
    assert request_id == "asset-task-request"

    still_in_use = client.delete(
        f"/api/v1/companies/{tenant['company_id']}/assets/{asset['id']}",
        headers=tenant_headers,
    )
    assert still_in_use.status_code == 409

    other = bootstrap(client, "asset-task-other")
    other_headers = {
        "X-Company-ID": other["company_id"],
        "X-User-ID": other["user_id"],
    }
    cross_company = client.post(
        f"/api/v1/companies/{other['company_id']}/tasks",
        headers=other_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "asset-cross-company-task",
            "request_payload": {
                "prompt": "must not see asset",
                "duration_seconds": 5,
                "assets": [{"asset_id": asset["id"], "media_type": "image"}],
            },
        },
    )
    assert cross_company.status_code == 404

    raw_url = client.post(
        f"/api/v1/companies/{tenant['company_id']}/tasks",
        headers=tenant_headers,
        json={
            "model_id": model_id,
            "idempotency_key": "asset-raw-url-task",
            "request_payload": {
                "prompt": "reject caller URL",
                "duration_seconds": 5,
                "assets": [
                    {"url": "https://attacker.example/file", "media_type": "image"}
                ],
            },
        },
    )
    assert raw_url.status_code == 409


def test_huawei_obs_adapter_forces_private_upload_and_https_signing(
    monkeypatch, tmp_path
):
    captured = {}

    class PutObjectHeader:
        def __init__(self, **kwargs):
            captured["headers"] = kwargs

    monkeypatch.setitem(
        __import__("sys").modules,
        "obs",
        SimpleNamespace(PutObjectHeader=PutObjectHeader),
    )

    digest = hashlib.sha256(PNG_BYTES).hexdigest()

    class FakeObsClient:
        def putFile(self, bucket, key, path, metadata, headers):
            captured.update(
                {
                    "bucket": bucket,
                    "key": key,
                    "path": path,
                    "metadata": metadata,
                    "upload": headers,
                }
            )
            return SimpleNamespace(status=200)

        def getObjectMetadata(self, bucket, key):
            captured["head"] = {"bucket": bucket, "key": key}
            return SimpleNamespace(
                status=200,
                body=SimpleNamespace(
                    contentLength=len(PNG_BYTES),
                    contentType="image/png",
                    metadata={
                        "x-obs-meta-sha256": digest,
                        "x-obs-meta-size-bytes": str(len(PNG_BYTES)),
                    },
                ),
            )

        def createSignedUrl(self, **kwargs):
            captured["sign"] = kwargs
            return SimpleNamespace(
                signedUrl=(
                    "https://private-input-bucket."
                    "obs.cn-north-4.myhuaweicloud.com/"
                    f"{kwargs['objectKey']}?Signature=test"
                )
            )

    source = tmp_path / "input.png"
    source.write_bytes(PNG_BYTES)
    store = HuaweiObsInputAssetStore(
        FakeObsClient(),
        "private-input-bucket",
        endpoint_host="obs.cn-north-4.myhuaweicloud.com",
    )
    key = (
        "inputs/11111111-1111-4111-8111-111111111111/"
        "22222222-2222-4222-8222-222222222222"
    )
    store.put_file(
        key,
        Path(source),
        content_type="image/png",
        size_bytes=len(PNG_BYTES),
        sha256=digest,
    )
    assert captured["headers"] == {
        "contentType": "image/png",
        "acl": "private",
        "cacheControl": "private, no-store",
    }
    assert captured["metadata"] == {
        "sha256": digest,
        "size-bytes": str(len(PNG_BYTES)),
    }
    assert captured["head"] == {"bucket": "private-input-bucket", "key": key}
    signed = store.signed_url(
        key,
        expires_seconds=300,
        original_filename="face.png",
        disposition="inline",
    )
    assert signed.startswith(
        "https://private-input-bucket.obs.cn-north-4.myhuaweicloud.com/"
    )
    assert captured["sign"]["expires"] == 300
    assert "response-content-disposition" in captured["sign"]["queryParams"]


def test_huawei_obs_adapter_uses_signed_head_when_sdk_drops_custom_metadata(
    monkeypatch, tmp_path
):
    class PutObjectHeader:
        def __init__(self, **_):
            pass

    monkeypatch.setitem(
        __import__("sys").modules,
        "obs",
        SimpleNamespace(PutObjectHeader=PutObjectHeader),
    )
    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    key = (
        "inputs/11111111-1111-4111-8111-111111111111/"
        "22222222-2222-4222-8222-222222222222"
    )

    class FakeObsClient:
        def putFile(self, *_):
            return SimpleNamespace(status=200)

        def getObjectMetadata(self, *_):
            return SimpleNamespace(
                status=200,
                body=SimpleNamespace(
                    contentLength=len(PNG_BYTES),
                    contentType="image/png",
                ),
            )

        def createSignedUrl(self, **kwargs):
            assert kwargs["method"] == "HEAD"
            assert kwargs["expires"] == 60
            return SimpleNamespace(
                signedUrl=(
                    "https://private-input-bucket."
                    "obs.cn-north-4.myhuaweicloud.com/"
                    f"{kwargs['objectKey']}?Signature=head-test"
                )
            )

    class HeadResponse:
        headers = {
            "Content-Length": str(len(PNG_BYTES)),
            "Content-Type": "image/png",
            "x-obs-meta-sha256": digest,
            "x-obs-meta-size-bytes": str(len(PNG_BYTES)),
        }

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "platform_api.asset_storage.urlopen",
        lambda request, timeout: HeadResponse(),
    )
    source = tmp_path / "input.png"
    source.write_bytes(PNG_BYTES)
    store = HuaweiObsInputAssetStore(
        FakeObsClient(),
        "private-input-bucket",
        endpoint_host="obs.cn-north-4.myhuaweicloud.com",
    )
    stored = store.put_file(
        key,
        source,
        content_type="image/png",
        size_bytes=len(PNG_BYTES),
        sha256=digest,
    )
    assert stored.sha256 == digest


def test_huawei_obs_adapter_rejects_remote_integrity_or_signed_url_drift(
    monkeypatch, tmp_path
):
    class PutObjectHeader:
        def __init__(self, **_):
            pass

    monkeypatch.setitem(
        __import__("sys").modules,
        "obs",
        SimpleNamespace(PutObjectHeader=PutObjectHeader),
    )
    key = (
        "inputs/11111111-1111-4111-8111-111111111111/"
        "22222222-2222-4222-8222-222222222222"
    )
    source = tmp_path / "input.png"
    source.write_bytes(PNG_BYTES)
    digest = hashlib.sha256(PNG_BYTES).hexdigest()

    class DriftedObsClient:
        def putFile(self, *_):
            return SimpleNamespace(status=200)

        def getObjectMetadata(self, *_):
            return SimpleNamespace(
                status=200,
                body=SimpleNamespace(
                    contentLength=len(PNG_BYTES) + 1,
                    contentType="image/png",
                    metadata={
                        "sha256": digest,
                        "size-bytes": str(len(PNG_BYTES)),
                    },
                ),
            )

        def createSignedUrl(self, **_):
            return SimpleNamespace(
                signedUrl=f"https://attacker.example/{key}?Signature=test"
            )

    store = HuaweiObsInputAssetStore(
        DriftedObsClient(),
        "private-input-bucket",
        endpoint_host="obs.cn-north-4.myhuaweicloud.com",
    )
    with pytest.raises(InputAssetStorageError, match="did not match"):
        store.put_file(
            key,
            source,
            content_type="image/png",
            size_bytes=len(PNG_BYTES),
            sha256=digest,
        )
    with pytest.raises(InputAssetStorageError, match="configured object"):
        store.signed_url(
            key,
            expires_seconds=300,
            original_filename="face.png",
            disposition="inline",
        )


def test_huawei_obs_adapter_passes_optional_temporary_security_token(
    monkeypatch,
):
    captured = {}

    class ObsClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(
        __import__("sys").modules,
        "obs",
        SimpleNamespace(ObsClient=ObsClient),
    )
    settings = Settings(
        input_asset_store="huawei_obs",
        huawei_obs_access_key_id="temporary-access-key",
        huawei_obs_secret_access_key="temporary-secret-key",
        huawei_obs_security_token="temporary-security-token",
        huawei_obs_endpoint="https://obs.cn-north-4.myhuaweicloud.com/",
        huawei_obs_bucket="private-input-bucket",
    )
    store = HuaweiObsInputAssetStore.from_settings(settings)
    assert captured["security_token"] == "temporary-security-token"
    assert store._endpoint_host == "obs.cn-north-4.myhuaweicloud.com"
