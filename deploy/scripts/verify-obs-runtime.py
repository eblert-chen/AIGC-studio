from __future__ import annotations

import asyncio
from io import BytesIO
import hashlib
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen
from uuid import uuid4

sys.path.insert(0, "/app")


PAYLOAD = b"ai-video internal pilot OBS runtime verification\n"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def verify_signed_get(url: str) -> None:
    with urlopen(url, timeout=15) as response:
        downloaded = response.read()
    if downloaded != PAYLOAD:
        raise RuntimeError("signed GET content did not match the uploaded bytes")


def assert_delete_denied(client, bucket: str, object_key: str) -> None:
    response = client.deleteObject(bucket, object_key)
    status = getattr(response, "status", 500)
    if isinstance(status, int) and status < 300:
        raise RuntimeError("runtime OBS credential unexpectedly allowed DeleteObject")


def verify_platform() -> str:
    from platform_api.asset_storage import HuaweiObsInputAssetStore
    from platform_api.config import get_settings

    store = HuaweiObsInputAssetStore.from_settings(get_settings())
    object_key = f"inputs/{uuid4()}/{uuid4()}"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "probe.bin"
        source.write_bytes(PAYLOAD)
        try:
            stored = store.put_file(
                object_key,
                source,
                content_type="application/octet-stream",
                size_bytes=len(PAYLOAD),
                sha256=DIGEST,
            )
        except Exception:
            remote = store._client.getObjectMetadata(store.bucket, object_key)
            body = getattr(remote, "body", None)
            metadata = getattr(body, "metadata", None)
            body_fields = sorted(
                key for key in getattr(body, "__dict__", {})
                if not key.startswith("_")
            )
            raw_headers = getattr(remote, "header", None) or []
            metadata_keys = sorted(str(key).lower() for key in (metadata or {}))
            header_keys = sorted(
                str(item[0]).lower()
                for item in raw_headers
                if isinstance(item, (tuple, list)) and len(item) == 2
                and str(item[0]).lower().startswith("x-obs-meta-")
            )
            if isinstance(raw_headers, dict):
                header_keys = sorted(
                    str(key).lower() for key in raw_headers
                    if str(key).lower().startswith("x-obs-meta-")
                )
            print(
                "OBS_SAFE_HEAD_DIAGNOSTIC "
                f"size={getattr(body, 'contentLength', None)} "
                f"content_type={getattr(body, 'contentType', None)} "
                f"body_fields={body_fields} metadata_keys={metadata_keys} "
                f"header_type={type(raw_headers).__name__} header_keys={header_keys}"
            )
            raise
    if stored.size_bytes != len(PAYLOAD) or stored.sha256 != DIGEST:
        raise RuntimeError("Platform OBS integrity result did not match")
    verify_signed_get(
        store.signed_url(
            object_key,
            expires_seconds=60,
            original_filename="probe.bin",
            disposition="attachment",
        )
    )
    assert_delete_denied(store._client, store.bucket, object_key)
    return object_key


async def verify_relay() -> str:
    from relay_service.artifacts import HuaweiObsArtifactStore

    store = HuaweiObsArtifactStore.from_environment()
    object_key = f"outputs/{uuid4()}/{uuid4()}/{uuid4()}"
    stored = await store.put_content(
        object_key,
        BytesIO(PAYLOAD),
        content_type="application/octet-stream",
        size_bytes=len(PAYLOAD),
        sha256=DIGEST,
    )
    if stored.size_bytes != len(PAYLOAD) or stored.sha256 != DIGEST:
        raise RuntimeError("Relay OBS integrity result did not match")
    verify_signed_get(await store.signed_download_url(object_key, expires_seconds=60))
    assert_delete_denied(store._client, store.bucket, object_key)
    return object_key


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"platform", "relay"}:
        raise SystemExit("usage: verify-obs-runtime.py platform|relay")
    role = sys.argv[1]
    object_key = verify_platform() if role == "platform" else asyncio.run(verify_relay())
    print(f"OBS_RUNTIME_PASS role={role} object_key={object_key} sha256={DIGEST}")


if __name__ == "__main__":
    main()
