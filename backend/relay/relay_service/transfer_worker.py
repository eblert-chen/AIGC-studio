from __future__ import annotations

import asyncio
import logging
import signal

from .artifacts import (
    FilesystemArtifactStore,
    HuaweiObsArtifactStore,
    InMemoryArtifactStore,
)
from .config import RelaySettings
from .downloader import DownloadPolicy, SafeHttpsDownloader
from .queue import RedisWorkQueue
from .sql_repository import SqlAlchemyJobRepository
from .transfer import ArtifactTransferService
from .worker import consume


async def run() -> None:
    settings = RelaySettings.from_environment()
    if settings.runtime_mode != "production":
        raise RuntimeError("Transfer worker requires production mode")
    assert settings.database_url is not None
    assert settings.redis_url is not None
    repository = SqlAlchemyJobRepository.from_url(settings.database_url)
    queue = RedisWorkQueue(
        settings.redis_url,
        stream="relay:artifact:transfer",
        group="relay-transfer-workers",
    )
    if settings.artifact_store == "huawei_obs":
        store = HuaweiObsArtifactStore.from_environment()
    elif settings.artifact_store == "filesystem":
        assert settings.artifact_filesystem_root is not None
        assert settings.artifact_public_base_url is not None
        assert settings.artifact_signing_secret is not None
        store = FilesystemArtifactStore(
            settings.artifact_filesystem_root,
            settings.artifact_public_base_url,
            settings.artifact_signing_secret,
        )
    else:
        store = InMemoryArtifactStore()
    downloader = SafeHttpsDownloader(
        DownloadPolicy(
            max_bytes=settings.artifact_max_bytes,
            timeout_seconds=settings.artifact_timeout_seconds,
        )
    )
    service = ArtifactTransferService(
        repository,
        queue,
        downloader,
        store,
        max_attempts=settings.transfer_max_attempts,
        claim_lease_seconds=(settings.artifact_transfer_claim_lease_seconds),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await consume(service, stop)
    finally:
        close_operations = [queue.close(), repository.dispose()]
        close_store = getattr(store, "close", None)
        if close_store:
            close_operations.append(close_store())
        await asyncio.gather(*close_operations, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
