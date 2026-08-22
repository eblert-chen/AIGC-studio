from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import PersonalDownloadRecord, new_id, utcnow
from ..relay_client import RelayArtifactStorageBinding


class PersonalDownloadRecordService:
    """Persist issuance evidence without retaining a bearer-style signed URL."""

    @staticmethod
    def append(
        session: Session,
        *,
        workspace_id: str,
        task_id: str,
        asset_id: str,
        requested_by_user_id: str,
        expires_seconds: int,
        request_id: str,
        storage_binding: RelayArtifactStorageBinding,
    ) -> PersonalDownloadRecord:
        record = PersonalDownloadRecord(
            id=new_id(),
            workspace_id=workspace_id,
            task_id=task_id,
            asset_id=asset_id,
            requested_by_user_id=requested_by_user_id,
            expires_seconds=expires_seconds,
            expires_at=storage_binding.expires_at,
            request_id=request_id,
            storage_provider=storage_binding.provider,
            storage_endpoint_host=storage_binding.endpoint_host,
            storage_bucket=storage_binding.bucket,
            storage_object_key=storage_binding.object_key,
            storage_version_id=None,
            source_url_sha256=storage_binding.url_sha256,
            relay_issued_at=storage_binding.issued_at,
            relay_expires_at=storage_binding.expires_at,
            created_at=utcnow(),
        )
        session.add(record)
        session.flush()
        return record
