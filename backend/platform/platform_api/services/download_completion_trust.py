from __future__ import annotations

from sqlalchemy import and_

from ..models import DownloadCompletion, DownloadCompletionSource


def verified_download_completion_clause():
    """Return the complete evidence predicate used by customer-facing reports."""

    return and_(
        DownloadCompletion.verification_version == 1,
        DownloadCompletion.source.in_(
            (
                DownloadCompletionSource.EDGE_GATEWAY,
                DownloadCompletionSource.OBS_ACCESS_LOG,
            )
        ),
        DownloadCompletion.artifact_sha256.is_not(None),
        DownloadCompletion.expected_size_bytes == DownloadCompletion.bytes_sent,
        DownloadCompletion.http_status == 200,
        DownloadCompletion.transfer_scope == "full_body",
        DownloadCompletion.source_evidence.is_not(None),
        DownloadCompletion.signed_event_id.is_not(None),
        DownloadCompletion.signed_event_timestamp.is_not(None),
        DownloadCompletion.signed_payload_sha256.is_not(None),
        DownloadCompletion.verified_at.is_not(None),
    )
