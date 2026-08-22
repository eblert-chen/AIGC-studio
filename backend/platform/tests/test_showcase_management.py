from __future__ import annotations

from datetime import timedelta
import hashlib
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import select

from platform_api.asset_storage import (
    InputAssetStorageError,
    validate_input_object_key,
    validate_showcase_object_key,
)
from platform_api.models import (
    AuditLog,
    AuthSession,
    ExternalIdentity,
    GenerationTask,
    ModelDefinition,
    ShowcaseChannel,
    ShowcaseMedia,
    ShowcasePublicationEvent,
    ShowcaseRelease,
    TaskArtifact,
    TaskStatus,
    utcnow,
)
from platform_api.platform_owner_identity import is_platform_owner_identity
from platform_api.relay_client import RelaySignedDownload
from platform_api.services.personal import PersonalWorkspaceService
from platform_api.services.authentication import CSRF_HEADER_NAME
from platform_api.services.errors import DomainError
from platform_api.services.showcase_media import sanitize_showcase_media

from .conftest import bootstrap
from .test_platform_admin import bootstrap_admin
from .test_production_auth_lifecycle import _auth_app, _login


def _png_bytes(color: tuple[int, int, int], *, author: str) -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Author", author)
    Image.new("RGB", (4, 3), color).save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


PNG_ONE = _png_bytes((220, 30, 60), author="private-owner@example.com")
PNG_TWO = _png_bytes((20, 120, 220), author="private-owner-two@example.com")


def _upload(client, headers, *, key: str, content: bytes, filename: str) -> dict:
    response = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers={**headers, "Idempotency-Key": key},
        files={"file": (filename, content, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _item(media_id: str, *, title: str, hero: bool, order: int, version: int) -> dict:
    return {
        "expected_draft_version": version,
        "media_id": media_id,
        "title": title,
        "section": "video",
        "category": "风格艺术",
        "alt_text": f"{title}的无障碍说明",
        "public_prompt": "public prompt only",
        "aspect_ratio": "16:9",
        "is_hero": hero,
        "sort_order": order,
    }


def test_owner_identity_is_scoped_to_the_exact_oidc_issuer() -> None:
    values = {"owner-subject"}
    assert is_platform_owner_identity(
        issuer="https://id.example.com",
        subject="owner-subject",
        configured_issuer="https://id.example.com",
        configured_subjects=values,
    )
    assert not is_platform_owner_identity(
        issuer="https://evil.example.com",
        subject="owner-subject",
        configured_issuer="https://id.example.com",
        configured_subjects=values,
    )


def test_protected_showcase_requires_exact_owner_strong_amr_recent_step_up_and_csrf(
    monkeypatch,
    tmp_path,
) -> None:
    app, engine, provider = _auth_app(
        input_asset_filesystem_root=str(tmp_path / "showcase-media")
    )
    storage_calls = 0
    original_put = app.state.showcase_media_store.put_file

    def counted_put(*args, **kwargs):
        nonlocal storage_calls
        storage_calls += 1
        return original_put(*args, **kwargs)

    monkeypatch.setattr(app.state.showcase_media_store, "put_file", counted_put)
    try:
        with app.state.session_factory() as session:
            assert session.scalar(select(ShowcaseRelease)) is None
        with TestClient(app, base_url="https://testserver") as browser:
                _, callback = _login(browser, provider)
                assert browser.get(callback, follow_redirects=False).status_code == 303
                auth_state = browser.get("/api/v1/auth/session").json()
                csrf = auth_state["csrf_token"]
                app.state.settings.environment = "production"
                assert browser.get("/api/v1/platform-admin/showcase").status_code == 200

                upload_headers = {"Idempotency-Key": "protected-showcase-upload"}
                upload_file = {"file": ("owner.png", PNG_ONE, "image/png")}
                missing_csrf = browser.post(
                    "/api/v1/platform-admin/showcase/media",
                    headers=upload_headers,
                    files=upload_file,
                )
                assert missing_csrf.status_code == 403
                wrong_origin = browser.post(
                    "/api/v1/platform-admin/showcase/media",
                    headers={
                        **upload_headers,
                        "Origin": "https://evil.example.test",
                        CSRF_HEADER_NAME: csrf,
                    },
                    files=upload_file,
                )
                assert wrong_origin.status_code == 403

                with app.state.session_factory.begin() as session:
                    auth_session = session.scalar(select(AuthSession))
                    assert auth_session is not None
                    auth_session.amr = ["pwd"]
                assert browser.get(
                    "/api/v1/platform-admin/showcase"
                ).status_code == 403

                with app.state.session_factory.begin() as session:
                    auth_session = session.scalar(select(AuthSession))
                    assert auth_session is not None
                    auth_session.amr = ["webauthn"]
                    auth_session.auth_time = utcnow() - timedelta(seconds=301)
                stale = browser.post(
                    "/api/v1/platform-admin/showcase/media",
                    headers={
                        **upload_headers,
                        "Origin": "https://frontend.example.test",
                        CSRF_HEADER_NAME: csrf,
                    },
                    files=upload_file,
                )
                assert stale.status_code == 403
                assert stale.headers["x-auth-required"] == "step-up"

                with app.state.session_factory.begin() as session:
                    auth_session = session.scalar(select(AuthSession))
                    identity = session.scalar(select(ExternalIdentity))
                    assert auth_session is not None and identity is not None
                    auth_session.auth_time = utcnow()
                    identity.issuer = "https://wrong-issuer.example.test"
                assert browser.get(
                    "/api/v1/platform-admin/showcase"
                ).status_code == 403

                with app.state.session_factory.begin() as session:
                    identity = session.scalar(select(ExternalIdentity))
                    assert identity is not None
                    identity.issuer = "https://identity.example.test"
                    identity.subject = "not-the-platform-owner"
                assert browser.get(
                    "/api/v1/platform-admin/showcase"
                ).status_code == 403

                with app.state.session_factory.begin() as session:
                    identity = session.scalar(select(ExternalIdentity))
                    assert identity is not None
                    identity.subject = "owner-subject"
                assert storage_calls == 0
                with app.state.session_factory() as session:
                    assert session.scalar(select(ShowcaseMedia)) is None
                accepted = browser.post(
                    "/api/v1/platform-admin/showcase/media",
                    headers={
                        **upload_headers,
                        "Origin": "https://frontend.example.test",
                        CSRF_HEADER_NAME: csrf,
                    },
                    files=upload_file,
                )
                assert accepted.status_code == 201, accepted.text
                assert storage_calls == 1
                with app.state.session_factory() as session:
                    assert session.scalar(select(ShowcaseRelease)) is None
    finally:
        app.state.oidc_http_client.close()
        engine.dispose()


def test_showcase_owner_only_draft_release_etag_media_and_rollback(app, client) -> None:
    owner_id, owner_headers = bootstrap_admin(client, "showcase-owner")
    _, delegated_headers = bootstrap_admin(client, "showcase-delegated")
    assert client.get(
        "/api/v1/platform-admin/showcase", headers=delegated_headers
    ).status_code == 403

    first_media = _upload(
        client,
        owner_headers,
        key="showcase-media-one",
        content=PNG_ONE,
        filename="one.png",
    )
    second_media = _upload(
        client,
        owner_headers,
        key="showcase-media-two",
        content=PNG_TWO,
        filename="two.png",
    )
    assert first_media["sha256"] != hashlib.sha256(PNG_ONE).hexdigest()
    assert "object_key" not in first_media
    assert first_media["content_url"].startswith(
        "/api/v1/platform-admin/showcase/media/"
    )
    assert client.get(
        f"/api/v1/showcase/media/{first_media['id']}/content"
    ).status_code == 404
    owner_preview = client.get(first_media["content_url"], headers=owner_headers)
    assert owner_preview.status_code == 200
    assert owner_preview.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(owner_preview.content)) as cleaned:
        assert "Author" not in cleaned.info
    assert client.get(
        first_media["content_url"], headers=delegated_headers
    ).status_code == 403

    hero = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=owner_headers,
        json=_item(
            first_media["id"], title="第一版头图", hero=True, order=0, version=0
        ),
    )
    assert hero.status_code == 201, hero.text
    normal = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=owner_headers,
        json=_item(
            second_media["id"], title="第一版案例", hero=False, order=1, version=1
        ),
    )
    assert normal.status_code == 201, normal.text

    invalid_order = client.put(
        "/api/v1/platform-admin/showcase/order",
        headers=owner_headers,
        json={
            "expected_draft_version": 2,
            "item_ids": [hero.json()["item"]["id"]],
        },
    )
    assert invalid_order.status_code == 422
    ordered = client.put(
        "/api/v1/platform-admin/showcase/order",
        headers=owner_headers,
        json={
            "expected_draft_version": 2,
            "item_ids": [normal.json()["item"]["id"], hero.json()["item"]["id"]],
        },
    )
    assert ordered.status_code == 200, ordered.text
    assert ordered.json()["draft_version"] == 3

    first_release = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**owner_headers, "Idempotency-Key": "showcase-release-one"},
        json={
            "expected_draft_version": 3,
            "expected_publication_version": 0,
            "release_note": "first release",
        },
    )
    assert first_release.status_code == 200, first_release.text
    assert first_release.json()["version"] == 1
    assert first_release.json()["publication_version"] == 1
    assert first_release.json()["item_count"] == 2
    assert first_release.json()["published_by_user_id"] == owner_id
    assert client.get(
        "/api/v1/platform-admin/showcase", headers=owner_headers
    ).json()["has_unpublished_changes"] is False

    home = client.get("/api/v1/showcase/home")
    assert home.status_code == 200, home.text
    assert home.headers["cache-control"] == "public, max-age=15, must-revalidate"
    assert home.json()["hero"]["title"] == "第一版头图"
    assert [item["title"] for item in home.json()["items"]] == ["第一版案例"]
    assert "task_id" not in home.text
    etag = home.headers["etag"]
    not_modified = client.get(
        "/api/v1/showcase/home", headers={"If-None-Match": etag}
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified.headers["cache-control"] == (
        "public, max-age=15, must-revalidate"
    )
    assert not_modified.headers["vary"] == "Accept-Encoding"

    content = client.get(f"/api/v1/showcase/media/{first_media['id']}/content")
    assert content.status_code == 200
    assert content.content == owner_preview.content
    assert content.headers["cache-control"] == "public, max-age=15, must-revalidate"

    hero_body = _item(
        first_media["id"], title="第二版头图", hero=True, order=1, version=3
    )
    updated = client.put(
        f"/api/v1/platform-admin/showcase/items/{hero.json()['item']['id']}",
        headers=owner_headers,
        json=hero_body,
    )
    assert updated.status_code == 200, updated.text
    assert client.get(
        "/api/v1/platform-admin/showcase", headers=owner_headers
    ).json()["has_unpublished_changes"] is True
    # Draft edits never leak into the current immutable release.
    assert client.get("/api/v1/showcase/home").json()["hero"]["title"] == "第一版头图"

    second_release = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**owner_headers, "Idempotency-Key": "showcase-release-two"},
        json={
            "expected_draft_version": 4,
            "expected_publication_version": 1,
            "release_note": "second release",
        },
    )
    assert second_release.status_code == 200, second_release.text
    assert client.get(
        "/api/v1/platform-admin/showcase", headers=owner_headers
    ).json()["has_unpublished_changes"] is False
    assert client.get("/api/v1/showcase/home").json()["hero"]["title"] == "第二版头图"

    rolled_back = client.post(
        f"/api/v1/platform-admin/showcase/releases/{first_release.json()['id']}/rollback",
        headers={**owner_headers, "Idempotency-Key": "showcase-rollback-one"},
        json={
            "expected_draft_version": 4,
            "expected_publication_version": 2,
            "release_note": "restore first",
        },
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["version"] == 3
    assert rolled_back.json()["publication_version"] == 3
    assert rolled_back.json()["source_release_id"] == first_release.json()["id"]
    assert client.get(
        "/api/v1/platform-admin/showcase", headers=owner_headers
    ).json()["has_unpublished_changes"] is True
    assert client.get("/api/v1/showcase/home").json()["hero"]["title"] == "第一版头图"

    replay = client.post(
        f"/api/v1/platform-admin/showcase/releases/{first_release.json()['id']}/rollback",
        headers={**owner_headers, "Idempotency-Key": "showcase-rollback-one"},
        json={
            "expected_draft_version": 4,
            "expected_publication_version": 2,
            "release_note": "restore first",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == rolled_back.json()["id"]
    with app.state.session_factory() as session:
        assert len(session.scalars(select(ShowcaseRelease)).all()) == 3
        actions = set(
            session.scalars(
                select(AuditLog.action).where(AuditLog.actor_user_id == owner_id)
            ).all()
        )
        assert {
            "showcase.media.create",
            "showcase.draft_item.create",
            "showcase.draft_items.reorder",
            "showcase.release.publish",
            "showcase.release.rollback",
        }.issubset(actions)


def test_publish_rejects_missing_or_multiple_hero(client) -> None:
    _, headers = bootstrap_admin(client, "showcase-hero-count")
    media = _upload(
        client,
        headers,
        key="showcase-no-hero-media",
        content=PNG_ONE,
        filename="no-hero.png",
    )
    created = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=_item(media["id"], title="没有头图", hero=False, order=0, version=0),
    )
    assert created.status_code == 201
    rejected = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-no-hero-release"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "must reject",
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "invalid_showcase_hero_count"


def test_publish_rejects_a_non_video_hero(client) -> None:
    _, headers = bootstrap_admin(client, "showcase-hero-section")
    media = _upload(
        client,
        headers,
        key="showcase-template-hero-media",
        content=PNG_ONE,
        filename="template-hero.png",
    )
    body = _item(media["id"], title="模板头图", hero=True, order=0, version=0)
    body["section"] = "template"
    created = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=body,
    )
    assert created.status_code == 201, created.text
    rejected = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-template-hero-release"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "must reject",
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "invalid_showcase_hero_section"


def test_setting_a_new_hero_atomically_replaces_the_previous_hero(client) -> None:
    _, headers = bootstrap_admin(client, "showcase-hero-replacement")
    first_media = _upload(
        client,
        headers,
        key="showcase-hero-replace-one",
        content=PNG_ONE,
        filename="first.png",
    )
    second_media = _upload(
        client,
        headers,
        key="showcase-hero-replace-two",
        content=PNG_TWO,
        filename="second.png",
    )
    first = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=_item(first_media["id"], title="旧头图", hero=True, order=0, version=0),
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=_item(second_media["id"], title="新头图", hero=True, order=1, version=1),
    )
    assert second.status_code == 201, second.text
    state = client.get("/api/v1/platform-admin/showcase", headers=headers)
    assert state.status_code == 200, state.text
    active = [item for item in state.json()["items"] if item["retired_at"] is None]
    heroes = [item for item in active if item["is_hero"]]
    assert [item["id"] for item in heroes] == [second.json()["item"]["id"]]
    old = next(item for item in active if item["id"] == first.json()["item"]["id"])
    assert old["is_hero"] is False
    assert state.json()["draft_version"] == 2


def test_public_etag_changes_when_release_metadata_changes_for_same_manifest(client) -> None:
    owner_id, headers = bootstrap_admin(client, "showcase-etag-release")
    media = _upload(
        client,
        headers,
        key="showcase-etag-media",
        content=PNG_ONE,
        filename="etag.png",
    )
    item = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=_item(media["id"], title="同一内容", hero=True, order=0, version=0),
    )
    assert item.status_code == 201, item.text
    first = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-etag-release-one"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "first",
        },
    )
    assert first.status_code == 200, first.text
    first_home = client.get("/api/v1/showcase/home")
    first_etag = first_home.headers["etag"]

    stale_second = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-etag-release-stale"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "stale second tab",
        },
    )
    assert stale_second.status_code == 409
    second = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-etag-release-two"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 1,
            "release_note": "same manifest",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["item_count"] == 1
    assert second.json()["published_by_user_id"] == owner_id
    second_home = client.get(
        "/api/v1/showcase/home",
        headers={"If-None-Match": first_etag},
    )
    assert second_home.status_code == 200
    assert second_home.headers["etag"] != first_etag
    assert second_home.json()["release_id"] == second.json()["id"]

    stale_unpublish = client.post(
        "/api/v1/platform-admin/showcase/unpublish",
        headers={**headers, "Idempotency-Key": "showcase-unpublish-stale"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 1,
            "release_note": "stale takedown",
        },
    )
    assert stale_unpublish.status_code == 409
    unpublished = client.post(
        "/api/v1/platform-admin/showcase/unpublish",
        headers={**headers, "Idempotency-Key": "showcase-unpublish-one"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 2,
            "release_note": "privacy takedown",
        },
    )
    assert unpublished.status_code == 200, unpublished.text
    assert unpublished.json()["previous_release_id"] == second.json()["id"]
    assert unpublished.json()["publication_version"] == 3
    assert unpublished.json()["unpublished_at"]
    empty = client.get(
        "/api/v1/showcase/home",
        headers={"If-None-Match": second_home.headers["etag"]},
    )
    assert empty.status_code == 200
    assert empty.json() == {
        "release_id": None,
        "version": 0,
        "published_at": None,
        "hero": None,
        "items": [],
    }
    assert client.get(
        f"/api/v1/showcase/media/{media['id']}/content"
    ).status_code == 404
    admin_state = client.get("/api/v1/platform-admin/showcase", headers=headers)
    assert admin_state.status_code == 200
    assert admin_state.json()["current_release"] is None
    assert admin_state.json()["has_unpublished_changes"] is True
    assert len(admin_state.json()["releases"]) == 2
    replay = client.post(
        "/api/v1/platform-admin/showcase/unpublish",
        headers={**headers, "Idempotency-Key": "showcase-unpublish-one"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 2,
            "release_note": "privacy takedown",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == unpublished.json()["id"]
    conflict = client.post(
        "/api/v1/platform-admin/showcase/unpublish",
        headers={**headers, "Idempotency-Key": "showcase-unpublish-one"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 2,
            "release_note": "different request",
        },
    )
    assert conflict.status_code == 409
    old_publish_replay = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-etag-release-one"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "first",
        },
    )
    assert old_publish_replay.status_code == 200
    assert old_publish_replay.json()["id"] == first.json()["id"]
    assert old_publish_replay.json()["publication_version"] == 1
    assert client.get("/api/v1/showcase/home").json()["release_id"] is None

    republished = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-etag-release-three"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 3,
            "release_note": "republish after takedown",
        },
    )
    assert republished.status_code == 200, republished.text
    assert republished.json()["publication_version"] == 4
    second_unpublish = client.post(
        "/api/v1/platform-admin/showcase/unpublish",
        headers={**headers, "Idempotency-Key": "showcase-unpublish-two"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 4,
            "release_note": "second takedown",
        },
    )
    assert second_unpublish.status_code == 200, second_unpublish.text
    assert second_unpublish.json()["publication_version"] == 5
    event_state = client.get("/api/v1/platform-admin/showcase", headers=headers)
    assert event_state.status_code == 200
    assert event_state.json()["publication_version"] == 5
    assert [event["id"] for event in event_state.json()["publication_events"]] == [
        second_unpublish.json()["id"],
        unpublished.json()["id"],
    ]
    assert event_state.json()["last_unpublished_event"]["id"] == (
        second_unpublish.json()["id"]
    )
    with client.app.state.session_factory() as session:
        assert len(session.scalars(select(ShowcasePublicationEvent)).all()) == 2


def test_publish_fails_before_pointer_switch_when_media_storage_is_unavailable(
    app, client, monkeypatch
) -> None:
    _, headers = bootstrap_admin(client, "showcase-storage-gate")
    media = _upload(
        client,
        headers,
        key="showcase-storage-media",
        content=PNG_ONE,
        filename="storage.png",
    )
    created = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=_item(media["id"], title="存储门禁", hero=True, order=0, version=0),
    )
    assert created.status_code == 201, created.text

    def unavailable(*_, **__):
        raise InputAssetStorageError("simulated OBS object loss")

    monkeypatch.setattr(app.state.showcase_media_store, "verify_object", unavailable)
    rejected = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-storage-release"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "must not switch",
        },
    )
    assert rejected.status_code == 503
    assert rejected.json()["code"] == "showcase_storage_unavailable"
    with app.state.session_factory() as session:
        channel = session.get(ShowcaseChannel, "home")
        assert channel is not None
        assert channel.current_release_id is None
        assert session.scalar(select(ShowcaseRelease)) is None


def test_media_deduplication_never_reports_success_for_an_unbound_key(client) -> None:
    _, headers = bootstrap_admin(client, "showcase-media-idempotency")
    first = _upload(
        client,
        headers,
        key="showcase-content-original",
        content=PNG_ONE,
        filename="original.png",
    )
    duplicate = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers={**headers, "Idempotency-Key": "showcase-content-new-key"},
        files={"file": ("duplicate.png", PNG_ONE, "image/png")},
    )
    assert duplicate.status_code == 409
    second = _upload(
        client,
        headers,
        key="showcase-content-new-key",
        content=PNG_TWO,
        filename="second.png",
    )
    assert second["id"] != first["id"]
    replay = _upload(
        client,
        headers,
        key="showcase-content-new-key",
        content=PNG_TWO,
        filename="second-again.png",
    )
    assert replay["id"] == second["id"]


def test_showcase_and_input_asset_storage_keyspaces_are_mutually_exclusive() -> None:
    input_key = (
        "inputs/11111111-1111-4111-8111-111111111111/"
        "22222222-2222-4222-8222-222222222222"
    )
    showcase_key = f"showcase/media/{'a' * 64}"
    validate_input_object_key(input_key)
    validate_showcase_object_key(showcase_key)
    with pytest.raises(InputAssetStorageError):
        validate_input_object_key(showcase_key)
    with pytest.raises(InputAssetStorageError):
        validate_showcase_object_key(input_key)


def test_unknown_showcase_category_is_rejected_at_the_api_boundary(client) -> None:
    _, headers = bootstrap_admin(client, "showcase-category")
    media = _upload(
        client,
        headers,
        key="showcase-category-media",
        content=PNG_ONE,
        filename="category.png",
    )
    body = _item(media["id"], title="未知分类", hero=True, order=0, version=0)
    body["category"] = "未注册分类"
    rejected = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=body,
    )
    assert rejected.status_code == 422


def test_direct_video_upload_is_rejected_until_the_verified_artifact_path(client) -> None:
    _, headers = bootstrap_admin(client, "showcase-direct-video")
    rejected = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers={**headers, "Idempotency-Key": "showcase-direct-video-key"},
        files={
            "file": (
                "private-client-name.mp4",
                b"\x00\x00\x00\x18ftypmp42not-a-real-video",
                "video/mp4",
            )
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "direct_showcase_video_disabled"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_verified_video_derivative_removes_source_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source-with-private-metadata.mp4"
    secret = "private-client-project-9842"
    created = subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:d=0.2",
            "-metadata",
            f"comment={secret}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr.decode(errors="replace")
    assert secret.encode() in source.read_bytes()
    sanitized = sanitize_showcase_media(
        source,
        content_type="video/mp4",
        max_bytes=10 * 1024 * 1024,
        trusted_generated_artifact=True,
    )
    try:
        output = sanitized.path.read_bytes()
        assert sanitized.content_type == "video/mp4"
        assert output.startswith(b"\x00\x00")
        assert b"ftyp" in output[:64]
        assert secret.encode() not in output
    finally:
        sanitized.path.unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_video_sanitizer_rejects_external_playlist_protocols(tmp_path: Path) -> None:
    playlist = tmp_path / "external-reference.mp4"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\n"
        "https://metadata-leak.example.test/private.mp4\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    with pytest.raises(DomainError) as rejected:
        sanitize_showcase_media(
            playlist,
            content_type="video/mp4",
            max_bytes=10 * 1024 * 1024,
            trusted_generated_artifact=True,
        )
    assert rejected.value.code == "invalid_showcase_video"


def test_obs_preview_uses_a_server_generated_filename_without_owner_pii(
    app, client
) -> None:
    _, headers = bootstrap_admin(client, "showcase-obs-filename")
    media = _upload(
        client,
        headers,
        key="showcase-obs-filename-media",
        content=PNG_ONE,
        filename="客户王女士-未公开.png",
    )
    created = client.post(
        "/api/v1/platform-admin/showcase/items",
        headers=headers,
        json=_item(media["id"], title="OBS案例", hero=True, order=0, version=0),
    )
    assert created.status_code == 201, created.text
    published = client.post(
        "/api/v1/platform-admin/showcase/publish",
        headers={**headers, "Idempotency-Key": "showcase-obs-filename-release"},
        json={
            "expected_draft_version": 1,
            "expected_publication_version": 0,
            "release_note": "OBS filename contract",
        },
    )
    assert published.status_code == 200, published.text
    captured: list[dict[str, object]] = []

    class FakeObsStore:
        kind = "huawei_obs"

        def signed_url(
            self,
            object_key,
            *,
            original_filename,
            expires_seconds,
            **_,
        ):
            captured.append(
                {
                    "object_key": object_key,
                    "original_filename": original_filename,
                    "expires_seconds": expires_seconds,
                }
            )
            return "https://showcase.example.invalid/object?Signature=test"

    app.state.showcase_media_store = FakeObsStore()
    response = client.get(
        media["content_url"],
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 307
    public = client.get(
        f"/api/v1/showcase/media/{media['id']}/content",
        follow_redirects=False,
    )
    assert public.status_code == 307
    assert [call["expires_seconds"] for call in captured] == [300, 300]
    assert all(
        str(call["original_filename"]).startswith("showcase-")
        and str(call["original_filename"]).endswith(".png")
        and "王女士" not in str(call["original_filename"])
        for call in captured
    )


def _seed_scoped_artifact(
    app,
    *,
    user_id: str,
    scope_id: str,
    personal: bool,
    status: TaskStatus,
    relay_job: bool,
    suffix: str,
    content: bytes | None = None,
    content_type: str = "video/mp4",
    media_type: str = "video",
) -> str:
    content = content or f"artifact-{suffix}".encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    with app.state.session_factory.begin() as session:
        model = ModelDefinition(
            slug=f"showcase-artifact-{suffix}-{uuid.uuid4().hex[:8]}",
            display_name="Showcase artifact fixture",
            provider_key="fixture",
            billing_mode="per_item",
            capability_version=1,
            active=True,
        )
        session.add(model)
        session.flush()
        task = GenerationTask(
            company_id=None if personal else scope_id,
            personal_workspace_id=scope_id if personal else None,
            user_id=user_id,
            model_id=model.id,
            idempotency_key=f"showcase-artifact-task-{suffix}",
            request_fingerprint=hashlib.sha256(suffix.encode()).hexdigest(),
            status=status,
            request_payload={"mode": "text_to_video"},
            quote_cents=None if personal else 1,
            quote_points=1 if personal else None,
            pricing_snapshot={},
            capability_snapshot={},
            reserved_cents=0,
            reserved_points=0,
            actual_cost_cents=None,
            actual_cost_points=None,
            relay_job_id=str(uuid.uuid4()) if relay_job else None,
            output_artifacts=[],
        )
        session.add(task)
        session.flush()
        artifact = TaskArtifact(
            company_id=None if personal else scope_id,
            personal_workspace_id=scope_id if personal else None,
            task_id=task.id,
            asset_id=f"artifact-{suffix}",
            position=0,
            media_type=media_type,
            content_type=content_type,
            size_bytes=len(content),
            sha256=digest,
        )
        session.add(artifact)
        session.flush()
        return artifact.id


def test_artifact_import_hides_company_other_user_non_success_and_unbound_rows(
    app, client
) -> None:
    owner_id, headers = bootstrap_admin(client, "showcase-artifact-owner")
    other_user_id, _ = bootstrap_admin(client, "showcase-artifact-other")
    company = bootstrap(client, "showcase-artifact-company")
    with app.state.session_factory.begin() as session:
        owner_workspace = PersonalWorkspaceService.ensure(session, user_id=owner_id)
        other_workspace = PersonalWorkspaceService.ensure(
            session,
            user_id=other_user_id,
        )
        owner_workspace_id = owner_workspace.id
        other_workspace_id = other_workspace.id
    forbidden_ids = [
        _seed_scoped_artifact(
            app,
            user_id=owner_id,
            scope_id=company["company_id"],
            personal=False,
            status=TaskStatus.SUCCEEDED,
            relay_job=True,
            suffix="company",
        ),
        _seed_scoped_artifact(
            app,
            user_id=other_user_id,
            scope_id=other_workspace_id,
            personal=True,
            status=TaskStatus.SUCCEEDED,
            relay_job=True,
            suffix="other-user",
        ),
        _seed_scoped_artifact(
            app,
            user_id=owner_id,
            scope_id=owner_workspace_id,
            personal=True,
            status=TaskStatus.FAILED,
            relay_job=True,
            suffix="failed",
        ),
        _seed_scoped_artifact(
            app,
            user_id=owner_id,
            scope_id=owner_workspace_id,
            personal=True,
            status=TaskStatus.SUCCEEDED,
            relay_job=False,
            suffix="no-relay-job",
        ),
    ]
    bodies = []
    for index, artifact_id in enumerate(forbidden_ids):
        response = client.post(
            "/api/v1/platform-admin/showcase/media",
            headers={
                **headers,
                "Idempotency-Key": f"showcase-forbidden-artifact-{index}",
            },
            data={"source_task_artifact_id": artifact_id},
        )
        assert response.status_code == 404
        bodies.append(response.json())
    assert bodies == [bodies[0]] * len(bodies)


def test_verified_personal_artifact_is_sanitized_and_replays_without_relay_copy(
    app, client, monkeypatch
) -> None:
    owner_id, headers = bootstrap_admin(client, "showcase-artifact-valid")
    with app.state.session_factory.begin() as session:
        workspace = PersonalWorkspaceService.ensure(session, user_id=owner_id)
        workspace_id = workspace.id
    artifact_id = _seed_scoped_artifact(
        app,
        user_id=owner_id,
        scope_id=workspace_id,
        personal=True,
        status=TaskStatus.SUCCEEDED,
        relay_job=True,
        suffix="valid-personal-image",
        content=PNG_ONE,
        content_type="image/png",
        media_type="image",
    )

    class Relay:
        def __init__(self):
            self.calls = 0

        def get_artifact_download(self, *_, **__):
            self.calls += 1
            return RelaySignedDownload.model_validate(
                {
                    "api_version": "v1",
                    "schema_version": 1,
                    "url": "http://127.0.0.1:8100/private-artifact",
                    "expires_seconds": 300,
                }
            )

    class Source:
        def copy_to(self, target, *, max_bytes):
            assert len(PNG_ONE) <= max_bytes
            target.write(PNG_ONE)
            return len(PNG_ONE), hashlib.sha256(PNG_ONE).hexdigest()

    relay = Relay()
    app.state.relay_client = relay
    monkeypatch.setattr(
        "platform_api.routers.showcase.HttpArtifactContentSource",
        lambda *_, **__: Source(),
    )
    request_headers = {
        **headers,
        "Idempotency-Key": "showcase-valid-artifact-import",
    }
    first = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers=request_headers,
        data={"source_task_artifact_id": artifact_id},
    )
    assert first.status_code == 201, first.text
    assert first.json()["source_task_artifact_id"] == artifact_id
    assert first.json()["sha256"] != hashlib.sha256(PNG_ONE).hexdigest()
    second = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers=request_headers,
        data={"source_task_artifact_id": artifact_id},
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert relay.calls == 1
