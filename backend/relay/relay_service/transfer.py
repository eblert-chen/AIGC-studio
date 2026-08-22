from __future__ import annotations

import asyncio
from datetime import timedelta

from .artifacts import ArtifactStore, ArtifactStoreError
from .downloader import ArtifactDownloadError, ArtifactDownloader
from .errors import public_async_error
from .models import (
    GeneratedAsset,
    JobStatus,
    PublicAsyncErrorCode,
    utc_now,
)
from .queue import WorkQueue
from .repository import JobRepository


class _ArtifactTransferClaimLost(RuntimeError):
    """A stale transfer worker must not write its job snapshot."""


class ArtifactTransferService:
    def __init__(
        self,
        repository: JobRepository,
        queue: WorkQueue,
        downloader: ArtifactDownloader,
        store: ArtifactStore,
        *,
        max_attempts: int = 3,
        claim_lease_seconds: float = 120.0,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("claim_lease_seconds must be positive")
        self.repository = repository
        self.queue = queue
        self.downloader = downloader
        self.store = store
        self.max_attempts = max_attempts
        self.claim_lease = timedelta(seconds=claim_lease_seconds)
        self.claim_heartbeat_seconds = min(
            max(claim_lease_seconds / 3, 0.01),
            claim_lease_seconds / 2,
        )

    async def _renew_claim(self, claim, stop: asyncio.Event, lost: asyncio.Event):
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.claim_heartbeat_seconds
                )
                return
            except TimeoutError:
                pass
            try:
                renewed = await self.repository.renew_artifact_transfer_claim(
                    claim.job.id,
                    token=claim.token,
                    lease=self.claim_lease,
                )
            except Exception:
                lost.set()
                return
            if not renewed:
                lost.set()
                return

    @staticmethod
    def _require_claim(lost: asyncio.Event) -> None:
        if lost.is_set():
            raise _ArtifactTransferClaimLost(
                "Artifact transfer claim is no longer owned"
            )

    async def process_next(self):
        delivery = await self.queue.dequeue()
        if delivery is None:
            return None
        claim = await self.repository.claim_artifact_transfer(
            delivery.item.job_id,
            lease=self.claim_lease,
        )
        if claim is None:
            latest = await self.repository.get(delivery.item.job_id)
            if latest is None or latest.status != JobStatus.TRANSFERRING:
                await self.queue.ack(delivery)
            # A TRANSFERRING delivery remains pending while another worker owns
            # the durable claim. Redis can reclaim it if that owner crashes.
            return latest

        stop = asyncio.Event()
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._renew_claim(claim, stop, lost))
        try:
            return await self._process_claimed(
                claim.job,
                delivery,
                token=claim.token,
                lost=lost,
            )
        finally:
            stop.set()
            await heartbeat

    async def _process_claimed(self, job, delivery, *, token, lost: asyncio.Event):

        try:
            for source in job.transfer_sources:
                if source.artifact is not None:
                    continue
                self._require_claim(lost)
                downloaded = await self.downloader.download(str(source.source_url))
                try:
                    self._require_claim(lost)
                    expected_prefix = (
                        "video/" if source.media_type == "video" else "image/"
                    )
                    if not downloaded.content_type.startswith(expected_prefix):
                        raise ArtifactDownloadError(
                            "Artifact MIME does not match provider media type"
                        )
                    stored = await self.store.put_content(
                        source.object_key,
                        downloaded.content,
                        content_type=downloaded.content_type,
                        size_bytes=downloaded.size_bytes,
                        sha256=downloaded.sha256,
                    )
                    self._require_claim(lost)
                    if (
                        stored.size_bytes != downloaded.size_bytes
                        or stored.sha256 != downloaded.sha256
                    ):
                        raise ArtifactStoreError(
                            "Stored artifact integrity metadata does not match"
                        )
                    source.artifact = GeneratedAsset(
                        asset_id=source.asset_id,
                        object_key=source.object_key,
                        media_type=source.media_type,
                        content_type=downloaded.content_type,
                        size_bytes=downloaded.size_bytes,
                        sha256=downloaded.sha256,
                    )
                    # Persist each completed object so a partial retry resumes
                    # without downloading or writing successful objects again.
                    job.updated_at = utc_now()
                    saved = await self.repository.save_artifact_transfer_progress(
                        job, token=token
                    )
                    if not saved:
                        raise _ArtifactTransferClaimLost(
                            "Artifact transfer progress claim was lost"
                        )
                finally:
                    downloaded.close()

            self._require_claim(lost)
            job.outputs = [
                source.artifact
                for source in job.transfer_sources
                if source.artifact is not None
            ]
            if len(job.outputs) != len(job.transfer_sources):
                raise ArtifactStoreError("Artifact transfer plan is incomplete")
            if len(job.outputs) != job.output.count:
                raise ArtifactStoreError(
                    "Stored artifact count does not match the generation request"
                )
            job.status = JobStatus.SUCCEEDED
            job.progress = 100
            job.error = None
            job.updated_at = utc_now()
            finished = await self.repository.finish_artifact_transfer(job, token=token)
            if not finished:
                raise _ArtifactTransferClaimLost(
                    "Artifact transfer completion claim was lost"
                )
        except _ArtifactTransferClaimLost:
            # The current claim owner (or a terminal state) is authoritative.
            # Leave this delivery pending so Redis acknowledgement/reclaim can
            # converge without a stale state write.
            return await self.repository.get(job.id)
        except (ArtifactDownloadError, ArtifactStoreError, TimeoutError) as exc:
            return await self._handle_failure(
                job,
                delivery,
                type(exc).__name__,
                token=token,
                lost=lost,
            )
        except Exception as exc:
            return await self._handle_failure(
                job,
                delivery,
                type(exc).__name__,
                token=token,
                lost=lost,
            )
        # Keep queue acknowledgement outside the transfer exception handler.
        # If ack fails after the terminal state commits, reclaiming the delivery
        # will observe succeeded and only retry the ack.
        await self.queue.ack(delivery)
        return job

    async def _handle_failure(
        self,
        job,
        delivery,
        failure_type: str,
        *,
        token,
        lost: asyncio.Event,
    ):
        if lost.is_set():
            return await self.repository.get(job.id)
        if delivery.item.attempt < self.max_attempts:
            job.status = JobStatus.TRANSFERRING
            job.outputs = []
            job.error = public_async_error(
                PublicAsyncErrorCode.ARTIFACT_TRANSFER_RETRYING,
                retryable=True,
                details={
                    "attempt": delivery.item.attempt,
                    "max_attempts": self.max_attempts,
                    "failure_type": failure_type,
                },
            )
            job.updated_at = utc_now()
            finished = await self.repository.finish_artifact_transfer(job, token=token)
            if not finished:
                return await self.repository.get(job.id)
            await self.queue.nack(delivery)
            return job
        job.status = JobStatus.FAILED
        job.outputs = []
        job.error = public_async_error(
            PublicAsyncErrorCode.ARTIFACT_TRANSFER_FAILED,
            details={
                "attempts": delivery.item.attempt,
                "failure_type": failure_type,
            },
        )
        job.updated_at = utc_now()
        finished = await self.repository.finish_artifact_transfer(job, token=token)
        if not finished:
            return await self.repository.get(job.id)
        await self.queue.ack(delivery)
        return job
