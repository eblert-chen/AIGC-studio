from __future__ import annotations

import hashlib

import pytest
from PIL import EpsImagePlugin, Image
from sqlalchemy import select

from platform_api.models import ShowcaseMedia
from platform_api.services import showcase_media
from platform_api.services.errors import DomainError

from .test_platform_admin import bootstrap_admin


_IMAGE_CASES = [
    ("image/png", "PNG", b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg", "JPEG", b"\xff\xd8\xff"),
    ("image/webp", "WEBP", b"RIFF\x00\x00\x00\x00WEBP"),
]
_DISGUISED_EPS = (
    b"%!PS-Adobe-3.0 EPSF-3.0\n"
    b"%%BoundingBox: 0 0 1 1\n"
    b"%%EndComments\n"
    b"%%BeginBinary: -100\n"
)


@pytest.mark.parametrize("content_type, image_format, signature", _IMAGE_CASES)
@pytest.mark.parametrize("trusted_generated_artifact", [False, True])
def test_showcase_rejects_disguised_eps_before_any_decoder_or_output(
    tmp_path, monkeypatch, content_type, image_format, signature,
    trusted_generated_artifact,
) -> None:
    source = tmp_path / "private-project-image.upload"
    source.write_bytes(_DISGUISED_EPS)

    def forbidden(*args, **kwargs):
        pytest.fail("A rejected MIME/signature pair must not reach a decoder or output")

    monkeypatch.setattr(showcase_media.Image, "open", forbidden)
    monkeypatch.setattr(showcase_media, "_output_path", forbidden)
    with pytest.raises(DomainError) as rejected:
        showcase_media.sanitize_showcase_media(
            source,
            content_type=content_type,
            max_bytes=64 * 1024,
            trusted_generated_artifact=trusted_generated_artifact,
        )
    assert rejected.value.status_code == 422
    assert rejected.value.code == "invalid_showcase_image"
    assert str(source) not in rejected.value.message
    assert source.read_bytes() == _DISGUISED_EPS


@pytest.mark.parametrize("content_type, image_format, signature", _IMAGE_CASES)
@pytest.mark.parametrize("trusted_generated_artifact", [False, True])
def test_showcase_accepted_images_use_only_the_declared_decoder(
    tmp_path, monkeypatch, content_type, image_format, signature,
    trusted_generated_artifact,
) -> None:
    source = tmp_path / "image.upload"
    with Image.new("RGB", (4, 3), (80, 140, 200)) as image:
        image.save(source, format=image_format)
    original_open = Image.open
    decoder_calls = []

    def restricted_open(path, *args, **kwargs):
        decoder_calls.append(kwargs.get("formats"))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(showcase_media.Image, "open", restricted_open)
    sanitized = showcase_media.sanitize_showcase_media(
        source,
        content_type=content_type,
        max_bytes=64 * 1024,
        trusted_generated_artifact=trusted_generated_artifact,
    )
    try:
        assert decoder_calls == [[image_format]]
        assert sanitized.content_type == content_type
        assert sanitized.size_bytes == sanitized.path.stat().st_size
        assert sanitized.sha256 == hashlib.sha256(sanitized.path.read_bytes()).hexdigest()
        with original_open(sanitized.path, formats=[image_format]) as decoded:
            decoded.load()
            assert decoded.format == image_format
            assert decoded.size == (4, 3)
    finally:
        sanitized.path.unlink(missing_ok=True)


@pytest.mark.parametrize("content_type, image_format, signature", _IMAGE_CASES)
def test_showcase_valid_signature_is_not_sufficient_to_accept_invalid_bytes(
    tmp_path, content_type, image_format, signature,
) -> None:
    source = tmp_path / "corrupted.upload"
    source.write_bytes(signature + b"not-a-decodable-image")
    with pytest.raises(DomainError) as rejected:
        showcase_media.sanitize_showcase_media(
            source,
            content_type=content_type,
            max_bytes=64 * 1024,
            trusted_generated_artifact=False,
        )
    assert rejected.value.status_code == 422
    assert rejected.value.code == "invalid_showcase_image"
    assert str(source) not in rejected.value.message


def test_showcase_restricted_decoder_does_not_probe_eps_even_if_signature_gate_misclassifies(
    tmp_path, monkeypatch,
) -> None:
    source = tmp_path / "misclassified.upload"
    source.write_bytes(_DISGUISED_EPS)
    monkeypatch.setattr(showcase_media, "_content_signature_matches", lambda *args: True)

    def forbidden(*args, **kwargs):
        pytest.fail("Pillow must never probe the EPS decoder for declared PNG media")

    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", forbidden)
    with pytest.raises(DomainError) as rejected:
        showcase_media.sanitize_showcase_media(
            source,
            content_type="image/png",
            max_bytes=64 * 1024,
            trusted_generated_artifact=False,
        )
    assert rejected.value.status_code == 422
    assert rejected.value.code == "invalid_showcase_image"


@pytest.mark.parametrize("error_type", [OSError, SyntaxError, ValueError])
def test_showcase_decoder_failures_are_generic_and_remove_partial_output(
    tmp_path, monkeypatch, error_type,
) -> None:
    source = tmp_path / "private-image.upload"
    source.write_bytes(_IMAGE_CASES[0][2] + b"corrupted")
    output = tmp_path / "partial-output.png"
    output.touch()
    monkeypatch.setattr(showcase_media, "_output_path", lambda suffix: output)

    def fail_decode(*args, **kwargs):
        raise error_type("private-project-id and secret storage location")

    monkeypatch.setattr(showcase_media.Image, "open", fail_decode)
    with pytest.raises(DomainError) as rejected:
        showcase_media.sanitize_showcase_media(
            source,
            content_type="image/png",
            max_bytes=64 * 1024,
            trusted_generated_artifact=False,
        )
    assert rejected.value.status_code == 422
    assert rejected.value.code == "invalid_showcase_image"
    assert rejected.value.message == "Showcase image could not be safely decoded"
    assert not output.exists()
    assert source.exists()


def test_owner_showcase_upload_rejects_disguised_eps_without_storage_or_database_writes(
    app, client, monkeypatch,
) -> None:
    _, headers = bootstrap_admin(client, "showcase-image-decode-safety")

    def forbidden(*args, **kwargs):
        pytest.fail("Rejected upload must not reach Pillow or object storage")

    monkeypatch.setattr(showcase_media.Image, "open", forbidden)
    monkeypatch.setattr(app.state.showcase_media_store, "put_file", forbidden)
    response = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers={**headers, "Idempotency-Key": "disguised-eps-rejected"},
        files={"file": ("private-project.png", _DISGUISED_EPS, "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_showcase_image"
    assert "private-project" not in response.text
    assert "BeginBinary" not in response.text
    with app.state.session_factory() as session:
        assert session.scalar(select(ShowcaseMedia)) is None


@pytest.mark.parametrize("error_type", [OSError, SyntaxError, ValueError])
def test_owner_showcase_decoder_error_response_never_exposes_internal_details(
    app, client, monkeypatch, error_type,
) -> None:
    _, headers = bootstrap_admin(client, "showcase-image-decode-error")

    def fail_decode(*args, **kwargs):
        raise error_type("private-project-id and secret storage location")

    def forbidden(*args, **kwargs):
        pytest.fail("Failed decode must not write object storage")

    monkeypatch.setattr(showcase_media.Image, "open", fail_decode)
    monkeypatch.setattr(app.state.showcase_media_store, "put_file", forbidden)
    response = client.post(
        "/api/v1/platform-admin/showcase/media",
        headers={**headers, "Idempotency-Key": "decode-error-rejected"},
        files={
            "file": (
                "private-project.png", _IMAGE_CASES[0][2] + b"corrupted", "image/png",
            ),
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_showcase_image"
    assert "private-project" not in response.text
    assert "secret storage" not in response.text
    with app.state.session_factory() as session:
        assert session.scalar(select(ShowcaseMedia)) is None
