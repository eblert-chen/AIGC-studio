from __future__ import annotations

import asyncio
import hashlib
import sys
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from relay_service.artifacts import (
    ArtifactConfigurationError,
    ArtifactSignatureError,
    ArtifactStoreError,
    FilesystemArtifactStore,
    HuaweiObsArtifactStore,
    InMemoryArtifactStore,
    validate_output_key,
)
from relay_service.config import RelaySettings
from relay_service.downloader import (
    ArtifactDownloadError,
    ArtifactSecurityError,
    DownloadedArtifact,
    DownloadPolicy,
    SafeHttpsDownloader,
)
from relay_service.main import create_app
from relay_service.models import (
    GenerationInputs,
    GenerationJob,
    GenerationMode,
    JobStatus,
    OutputOptions,
    TransferSource,
    WorkItem,
)
from relay_service.queue import InMemoryWorkQueue
from relay_service.repository import InMemoryJobRepository
from relay_service.transfer import ArtifactTransferService


def downloaded(data: bytes, content_type: str = "video/mp4"):
    return DownloadedArtifact(
        content=BytesIO(data),
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def transfer_job(
    source_count: int = 1,
    *,
    media_type: str = "video",
    content_type: str = "video/mp4",
) -> GenerationJob:
    tenant_id = uuid4()
    job_id = uuid4()
    sources = []
    for index in range(source_count):
        asset_id = uuid4()
        sources.append(
            TransferSource(
                asset_id=asset_id,
                source_url=f"https://provider.example.test/{index}",
                media_type=media_type,
                declared_content_type=content_type,
                object_key=f"outputs/{tenant_id}/{job_id}/{asset_id}",
            )
        )
    return GenerationJob(
        id=job_id,
        tenant_id=tenant_id,
        model="mock.video.v1",
        mode=(
            GenerationMode.TEXT_TO_IMAGE
            if media_type == "image"
            else GenerationMode.TEXT_TO_VIDEO
        ),
        inputs=GenerationInputs(prompt="transfer"),
        output=OutputOptions(count=source_count),
        status=JobStatus.TRANSFERRING,
        progress=95,
        transfer_sources=sources,
    )


@pytest.mark.parametrize(
    ("media_type", "content_type", "payload"),
    [
        ("image", "image/png", b"generated-image"),
        ("video", "video/mp4", b"generated-video"),
    ],
)
def test_image_and_video_outputs_are_persisted_before_success(
    media_type: str,
    content_type: str,
    payload: bytes,
) -> None:
    class TypedDownloader:
        async def download(self, _: str):
            return downloaded(payload, content_type)

    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        job = transfer_job(
            media_type=media_type,
            content_type=content_type,
        )
        await repository.create_idempotent(job, f"{media_type}-transfer", "hash")
        await queue.enqueue(WorkItem(job_id=job.id))
        store = InMemoryArtifactStore()
        service = ArtifactTransferService(
            repository,
            queue,
            TypedDownloader(),
            store,
        )

        completed = await service.process_next()

        assert completed.status == JobStatus.SUCCEEDED
        assert completed.progress == 100
        assert len(completed.outputs) == 1
        artifact = completed.outputs[0]
        assert artifact.media_type == media_type
        assert artifact.content_type == content_type
        assert artifact.size_bytes == len(payload)
        assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
        assert store.objects[artifact.object_key] == payload
        assert store.content_types[artifact.object_key] == content_type

    asyncio.run(scenario())


def test_downloader_rejects_private_dns_redirects_and_oversize() -> None:
    async def private_dns(_: str):
        return ["127.0.0.1"]

    downloader = SafeHttpsDownloader(
        DownloadPolicy(max_bytes=10),
        resolve_host=private_dns,
    )
    with pytest.raises(ArtifactSecurityError):
        asyncio.run(
            downloader.download("https://provider.example.test/output.mp4")
        )
    with pytest.raises(ArtifactSecurityError):
        downloader._validate_status(302)
    with pytest.raises(ArtifactDownloadError):
        downloader._assert_size(11)
    with pytest.raises(ArtifactSecurityError):
        downloader._validate_url("https://127.0.0.1:8443/output.mp4")


def test_huawei_obs_missing_optional_sdk_fails_without_echoing_secret(
    monkeypatch,
) -> None:
    secret = "never-echo-this-secret"
    monkeypatch.setenv("HUAWEI_OBS_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setenv("HUAWEI_OBS_SECRET_ACCESS_KEY", secret)
    monkeypatch.setenv(
        "HUAWEI_OBS_ENDPOINT",
        "https://obs.cn-north-4.myhuaweicloud.com",
    )
    monkeypatch.setenv("HUAWEI_OBS_BUCKET", "relay-test-bucket")
    monkeypatch.setitem(sys.modules, "obs", None)

    with pytest.raises(ArtifactConfigurationError) as error:
        HuaweiObsArtifactStore.from_environment()
    assert "optional" in str(error.value)
    assert secret not in str(error.value)


def test_huawei_obs_upload_is_private_and_head_verified_before_success(
    monkeypatch,
) -> None:
    data = b"verified-obs-artifact"
    digest = hashlib.sha256(data).hexdigest()

    class Result:
        status = 200

    class MetadataBody:
        contentLength = len(data)
        contentType = "video/mp4"
        metadata = {"sha256": digest}

    class MetadataResult:
        status = 200
        body = MetadataBody()
        header = [("x-obs-meta-sha256", digest)]

    class FakeObsClient:
        def __init__(self) -> None:
            self.put_calls = []
            self.head_calls = []

        def putContent(self, bucket, key, **kwargs):
            self.put_calls.append((bucket, key, kwargs))
            return Result()

        def getObjectMetadata(self, bucket, key):
            self.head_calls.append((bucket, key))
            return MetadataResult()

    class PutObjectHeader:
        def __init__(self, **values) -> None:
            self.__dict__.update(values)

    monkeypatch.setitem(
        sys.modules, "obs", SimpleNamespace(PutObjectHeader=PutObjectHeader)
    )

    client = FakeObsClient()
    store = HuaweiObsArtifactStore(client, "private-artifacts")
    object_key = (
        "outputs/00000000-0000-4000-8000-000000000001/"
        "00000000-0000-4000-8000-000000000002/"
        "00000000-0000-4000-8000-000000000003"
    )

    stored = asyncio.run(
        store.put_content(
            object_key,
            BytesIO(data),
            content_type="video/mp4",
            size_bytes=len(data),
            sha256=digest,
        )
    )

    assert stored.size_bytes == len(data)
    assert stored.sha256 == digest
    assert client.head_calls == [("private-artifacts", object_key)]
    _, _, kwargs = client.put_calls[0]
    assert kwargs["metadata"] == {"sha256": digest}
    assert kwargs["headers"].acl == "private"


def test_huawei_obs_upload_uses_signed_head_when_sdk_drops_custom_metadata(
    monkeypatch,
) -> None:
    data = b"verified-obs-artifact-with-sdk-head-gap"
    digest = hashlib.sha256(data).hexdigest()
    object_key = (
        "outputs/00000000-0000-4000-8000-000000000001/"
        "00000000-0000-4000-8000-000000000002/"
        "00000000-0000-4000-8000-000000000003"
    )

    class FakeObsClient:
        def putContent(self, *_args, **_kwargs):
            return SimpleNamespace(status=200)

        def getObjectMetadata(self, *_args):
            return SimpleNamespace(
                status=200,
                body=SimpleNamespace(
                    contentLength=len(data),
                    contentType="video/mp4",
                ),
                header=[],
            )

        def createSignedUrl(self, **kwargs):
            assert kwargs["method"] == "HEAD"
            return SimpleNamespace(
                signedUrl=(
                    "https://private-artifacts."
                    "obs.cn-north-4.myhuaweicloud.com/"
                    f"{kwargs['objectKey']}?Signature=head-test"
                )
            )

    class PutObjectHeader:
        def __init__(self, **values) -> None:
            self.__dict__.update(values)

    class HeadResponse:
        headers = {"x-obs-meta-sha256": digest}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setitem(
        sys.modules, "obs", SimpleNamespace(PutObjectHeader=PutObjectHeader)
    )
    monkeypatch.setattr(
        "relay_service.artifacts.urlopen",
        lambda request, timeout: HeadResponse(),
    )
    store = HuaweiObsArtifactStore(
        FakeObsClient(),
        "private-artifacts",
        endpoint_host="obs.cn-north-4.myhuaweicloud.com",
    )
    stored = asyncio.run(
        store.put_content(
            object_key,
            BytesIO(data),
            content_type="video/mp4",
            size_bytes=len(data),
            sha256=digest,
        )
    )
    assert stored.sha256 == digest


def test_huawei_obs_upload_fails_closed_when_head_metadata_mismatches(
    monkeypatch,
) -> None:
    data = b"expected-content"
    digest = hashlib.sha256(data).hexdigest()

    class Result:
        status = 200

    class MetadataBody:
        contentLength = len(data) + 1
        contentType = "video/mp4"
        metadata = {"sha256": digest}

    class MetadataResult:
        status = 200
        body = MetadataBody()
        header = []

    class FakeObsClient:
        def putContent(self, *_args, **_kwargs):
            return Result()

        def getObjectMetadata(self, *_args):
            return MetadataResult()

    class PutObjectHeader:
        def __init__(self, **values) -> None:
            self.__dict__.update(values)

    monkeypatch.setitem(
        sys.modules, "obs", SimpleNamespace(PutObjectHeader=PutObjectHeader)
    )

    store = HuaweiObsArtifactStore(FakeObsClient(), "private-artifacts")
    object_key = (
        "outputs/00000000-0000-4000-8000-000000000001/"
        "00000000-0000-4000-8000-000000000002/"
        "00000000-0000-4000-8000-000000000003"
    )

    with pytest.raises(ArtifactStoreError, match="did not match"):
        asyncio.run(
            store.put_content(
                object_key,
                BytesIO(data),
                content_type="video/mp4",
                size_bytes=len(data),
                sha256=digest,
            )
        )


def test_partial_failure_resumes_without_retransferring_completed_asset() -> None:
    class FakeDownloader:
        def __init__(self):
            self.calls: dict[str, int] = {}

        async def download(self, url: str):
            self.calls[url] = self.calls.get(url, 0) + 1
            return downloaded(url.encode())

    class FailSecondOnceStore(InMemoryArtifactStore):
        def __init__(self, failing_key: str):
            super().__init__()
            self.failing_key = failing_key
            self.failed = False

        async def put_content(self, object_key, content, **kwargs):
            if object_key == self.failing_key and not self.failed:
                self.failed = True
                raise ArtifactStoreError("temporary store failure")
            return await super().put_content(object_key, content, **kwargs)

    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        job = transfer_job(2)
        await repository.create_idempotent(job, "transfer", "hash")
        await queue.enqueue(WorkItem(job_id=job.id))
        downloader = FakeDownloader()
        store = FailSecondOnceStore(job.transfer_sources[1].object_key)
        service = ArtifactTransferService(
            repository, queue, downloader, store, max_attempts=3
        )

        retrying = await service.process_next()
        assert retrying.status == "transferring"
        assert retrying.error.code == "ARTIFACT_TRANSFER_RETRYING"
        first_url = str(job.transfer_sources[0].source_url)
        second_url = str(job.transfer_sources[1].source_url)
        assert downloader.calls[first_url] == 1
        assert downloader.calls[second_url] == 1

        completed = await service.process_next()
        assert completed.status == "succeeded"
        assert len(completed.outputs) == 2
        assert downloader.calls[first_url] == 1
        assert downloader.calls[second_url] == 2
        assert store.put_counts[job.transfer_sources[0].object_key] == 1

        # A duplicate delivery after success is acknowledged without a new put.
        await queue.enqueue(WorkItem(job_id=job.id))
        duplicate = await service.process_next()
        assert duplicate.status == "succeeded"
        assert store.put_counts[job.transfer_sources[0].object_key] == 1
        assert await queue.depth() == 0

    asyncio.run(scenario())


def test_transfer_failure_is_terminal_after_bounded_retries() -> None:
    class OversizeDownloader:
        async def download(self, url: str):
            raise ArtifactDownloadError("Artifact exceeds maximum size")

    async def scenario() -> None:
        repository = InMemoryJobRepository()
        queue = InMemoryWorkQueue()
        job = transfer_job()
        await repository.create_idempotent(job, "oversize", "hash")
        await queue.enqueue(WorkItem(job_id=job.id))
        service = ArtifactTransferService(
            repository,
            queue,
            OversizeDownloader(),
            InMemoryArtifactStore(),
            max_attempts=2,
        )

        first = await service.process_next()
        assert first.status == "transferring"
        final = await service.process_next()
        assert final.status == "failed"
        assert final.error.code == "ARTIFACT_TRANSFER_FAILED"
        assert final.outputs == []
        assert await queue.depth() == 0

    asyncio.run(scenario())


def test_filesystem_store_is_shared_across_instances_and_downloads_without_api_key(
    tmp_path,
) -> None:
    now = [1_800_000_000]
    secret = "development-signing-secret-with-32-bytes-minimum"
    first = FilesystemArtifactStore(
        tmp_path,
        "http://testserver",
        secret,
        clock=lambda: now[0],
    )
    second = FilesystemArtifactStore(
        tmp_path,
        "http://testserver",
        secret,
        clock=lambda: now[0],
    )
    tenant_id, job_id, asset_id = uuid4(), uuid4(), uuid4()
    object_key = f"outputs/{tenant_id}/{job_id}/{asset_id}"
    data = b"shared-across-relay-processes"
    digest = hashlib.sha256(data).hexdigest()

    async def write_and_sign() -> str:
        await first.put_content(
            object_key,
            BytesIO(data),
            content_type="video/mp4",
            size_bytes=len(data),
            sha256=digest,
        )
        # A separately constructed store sees the same file and metadata.
        return await second.signed_download_url(
            object_key, expires_seconds=120
        )

    signed_url = asyncio.run(write_and_sign())
    parsed = urlsplit(signed_url)
    app = create_app(
        artifact_store=second,
        settings=RelaySettings(
            artifact_store="filesystem",
            artifact_filesystem_root=str(tmp_path),
            artifact_public_base_url="http://testserver",
            artifact_signing_secret=secret,
        ),
        process_in_background=False,
    )
    response = TestClient(app).get(f"{parsed.path}?{parsed.query}")

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["etag"] == f'"sha256-{digest}"'


def test_filesystem_signature_rejects_expired_and_tampered_tokens(
    tmp_path,
) -> None:
    now = [1_800_000_000]
    store = FilesystemArtifactStore(
        tmp_path,
        "https://relay.example.test",
        "development-signing-secret-with-32-bytes-minimum",
        clock=lambda: now[0],
    )
    object_key = f"outputs/{uuid4()}/{uuid4()}/{uuid4()}"
    data = b"signed-content"

    async def scenario() -> None:
        await store.put_content(
            object_key,
            BytesIO(data),
            content_type="video/mp4",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        signed_url = await store.signed_download_url(
            object_key, expires_seconds=10
        )
        query = parse_qs(urlsplit(signed_url).query)
        expires = int(query["expires"][0])
        signature = query["signature"][0]

        opened = await store.open_signed(object_key, expires, signature)
        try:
            assert opened.content.read() == data
        finally:
            opened.close()

        with pytest.raises(ArtifactSignatureError):
            await store.open_signed(
                object_key,
                expires,
                f"{signature[:-1]}{'0' if signature[-1] != '0' else '1'}",
            )
        other_key = f"outputs/{uuid4()}/{uuid4()}/{uuid4()}"
        with pytest.raises(ArtifactSignatureError):
            await store.open_signed(other_key, expires, signature)
        now[0] = expires
        with pytest.raises(ArtifactSignatureError):
            await store.open_signed(object_key, expires, signature)

    asyncio.run(scenario())


def test_filesystem_store_verifies_declared_and_stored_integrity(
    tmp_path,
) -> None:
    now = 1_800_000_000
    store = FilesystemArtifactStore(
        tmp_path,
        "http://127.0.0.1:8100",
        "development-signing-secret-with-32-bytes-minimum",
        clock=lambda: now,
    )
    object_key = f"outputs/{uuid4()}/{uuid4()}/{uuid4()}"
    data = b"integrity-checked"
    digest = hashlib.sha256(data).hexdigest()

    async def scenario() -> None:
        with pytest.raises(ArtifactStoreError, match="integrity"):
            await store.put_content(
                object_key,
                BytesIO(data),
                content_type="video/mp4",
                size_bytes=len(data) + 1,
                sha256=digest,
            )
        await store.put_content(
            object_key,
            BytesIO(data),
            content_type="video/mp4",
            size_bytes=len(data),
            sha256=digest,
        )
        signed_url = await store.signed_download_url(object_key)
        query = parse_qs(urlsplit(signed_url).query)
        artifact_path, _ = store._paths(object_key)
        artifact_path.write_bytes(b"tampered-after-storage")
        with pytest.raises(ArtifactStoreError, match="integrity"):
            await store.open_signed(
                object_key,
                int(query["expires"][0]),
                query["signature"][0],
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "object_key",
    [
        "../outputs/file",
        "outputs/../../etc/passwd",
        "outputs\\tenant\\job\\asset",
        "outputs/not-a-uuid/not-a-uuid/not-a-uuid",
        f"outputs/{uuid4()}/{uuid4()}/../{uuid4()}",
        f"outputs/{str(uuid4()).upper()}/{uuid4()}/{uuid4()}",
    ],
)
def test_output_key_rejects_path_traversal_and_noncanonical_ids(
    object_key: str,
) -> None:
    with pytest.raises(ArtifactStoreError):
        validate_output_key(object_key)


def test_filesystem_store_cannot_be_enabled_in_production(tmp_path) -> None:
    settings = RelaySettings(
        environment="production",
        runtime_mode="production",
        database_url="postgresql+asyncpg://db/relay",
        redis_url="redis://queue",
        artifact_store="filesystem",
        artifact_filesystem_root=str(tmp_path),
        artifact_public_base_url="https://relay.example.test",
        artifact_signing_secret=(
            "production-still-cannot-use-filesystem-store"
        ),
    )
    with pytest.raises(RuntimeError, match="development-only"):
        settings.validate()
