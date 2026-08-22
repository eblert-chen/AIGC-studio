from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
import re
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Company,
    DownloadCompletion,
    DownloadCompletionSource,
    DownloadGatewayRegistrationAttempt,
    DownloadGatewayRegistrationStatus,
    DownloadRecord,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    ModelDefinition,
    TaskArtifact,
    TaskStatus,
    User,
    new_id,
    utcnow,
)
from ..download_gateway import DownloadGatewayTicket
from ..relay_client import RelayArtifactStorageBinding
from .download_completion_trust import verified_download_completion_clause
from .errors import ConflictError, DomainError, NotFoundError


MAX_EXPORT_ROWS = 10_000


def _csv_safe(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "-", "@")) or value.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + value
    return value


def build_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    stream = StringIO(newline="")
    stream.write("\ufeff")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe(value) for value in row])
    return stream.getvalue()


class DownloadRecordService:
    @staticmethod
    def append(
        session: Session,
        *,
        company_id: str,
        task_id: str,
        asset_id: str,
        requested_by_user_id: str,
        expires_seconds: int,
        request_id: str,
        record_id: str | None = None,
        expires_at: datetime | None = None,
        storage_binding: RelayArtifactStorageBinding | None = None,
        source_url_sha256: str | None = None,
        gateway_ticket: DownloadGatewayTicket | None = None,
        gateway_registration_request_id: str | None = None,
        gateway_transfer_reference: str | None = None,
    ) -> DownloadRecord:
        now = utcnow()
        if (storage_binding is None) != (source_url_sha256 is None):
            raise ConflictError("Download storage binding is incomplete")
        if gateway_ticket is not None and storage_binding is None:
            raise ConflictError("Gateway ticket requires a Relay storage binding")
        if gateway_ticket is not None and (
            gateway_registration_request_id is None
            or gateway_transfer_reference is None
            or gateway_ticket.transfer_reference != gateway_transfer_reference
            or gateway_ticket.issuance_request_id != request_id
        ):
            raise ConflictError("Download Gateway evidence is incomplete")
        if gateway_ticket is None and (
            gateway_registration_request_id is not None
            or gateway_transfer_reference is not None
        ):
            raise ConflictError("Download Gateway evidence is incomplete")
        effective_expires_at = expires_at or now + timedelta(
            seconds=expires_seconds
        )
        if gateway_ticket is not None:
            effective_expires_at = gateway_ticket.expires_at
        record = DownloadRecord(
            id=record_id or new_id(),
            company_id=company_id,
            task_id=task_id,
            asset_id=asset_id,
            requested_by_user_id=requested_by_user_id,
            expires_seconds=expires_seconds,
            expires_at=effective_expires_at,
            request_id=request_id,
            storage_binding_version=(1 if storage_binding is not None else None),
            storage_provider=(
                storage_binding.provider if storage_binding is not None else None
            ),
            storage_endpoint_host=(
                storage_binding.endpoint_host
                if storage_binding is not None
                else None
            ),
            storage_bucket=(
                storage_binding.bucket if storage_binding is not None else None
            ),
            storage_object_key=(
                storage_binding.object_key if storage_binding is not None else None
            ),
            source_url_sha256=source_url_sha256,
            relay_issued_at=(
                storage_binding.issued_at if storage_binding is not None else None
            ),
            relay_expires_at=(
                storage_binding.expires_at if storage_binding is not None else None
            ),
            gateway_registration_request_id=gateway_registration_request_id,
            gateway_ticket_id=(
                gateway_ticket.gateway_ticket_id
                if gateway_ticket is not None
                else None
            ),
            gateway_ticket_url_sha256=(
                hashlib.sha256(str(gateway_ticket.ticket_url).encode("utf-8")).hexdigest()
                if gateway_ticket is not None
                else None
            ),
            gateway_issued_at=(
                gateway_ticket.issued_at if gateway_ticket is not None else None
            ),
            gateway_expires_at=(
                gateway_ticket.expires_at if gateway_ticket is not None else None
            ),
            gateway_transfer_reference=gateway_transfer_reference,
            created_at=now,
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def append_registered_gateway_attempt(
        session: Session,
        *,
        attempt: DownloadGatewayRegistrationAttempt,
    ) -> DownloadRecord:
        if (
            attempt.status != DownloadGatewayRegistrationStatus.REGISTERED
            or attempt.storage_provider != "huawei_obs"
            or not attempt.gateway_ticket_id
            or not attempt.gateway_ticket_url_sha256
            or attempt.gateway_issued_at is None
            or attempt.gateway_expires_at is None
            or attempt.gateway_expires_seconds is None
            or attempt.gateway_expires_seconds <= 0
            or attempt.gateway_expires_at <= attempt.gateway_issued_at
        ):
            raise ConflictError(
                "Download Gateway registration attempt is incomplete"
            )
        record = DownloadRecord(
            id=attempt.download_record_id,
            company_id=attempt.company_id,
            task_id=attempt.task_id,
            asset_id=attempt.asset_id,
            requested_by_user_id=attempt.requested_by_user_id,
            expires_seconds=attempt.gateway_expires_seconds,
            expires_at=attempt.gateway_expires_at,
            request_id=attempt.platform_request_id,
            storage_binding_version=1,
            storage_provider=attempt.storage_provider,
            storage_endpoint_host=attempt.storage_endpoint_host,
            storage_bucket=attempt.storage_bucket,
            storage_object_key=attempt.storage_object_key,
            storage_version_id=None,
            source_url_sha256=attempt.source_url_sha256,
            relay_issued_at=attempt.relay_issued_at,
            relay_expires_at=attempt.relay_expires_at,
            gateway_registration_request_id=(
                attempt.registration_request_id
            ),
            gateway_ticket_id=attempt.gateway_ticket_id,
            gateway_ticket_url_sha256=(
                attempt.gateway_ticket_url_sha256
            ),
            gateway_issued_at=attempt.gateway_issued_at,
            gateway_expires_at=attempt.gateway_expires_at,
            gateway_transfer_reference=attempt.transfer_reference,
            created_at=utcnow(),
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def page(
        session: Session,
        *,
        company_id: str,
        page: int,
        page_size: int,
        task_id: str | None = None,
        asset_id: str | None = None,
        requested_by_user_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        statement = (
            select(
                DownloadRecord.id,
                DownloadRecord.task_id,
                DownloadRecord.asset_id,
                DownloadRecord.requested_by_user_id,
                User.display_name.label("requested_by_display_name"),
                DownloadRecord.expires_seconds,
                DownloadRecord.expires_at,
                DownloadRecord.request_id,
                DownloadRecord.created_at,
                DownloadCompletion.completed_at,
                DownloadCompletion.bytes_sent,
                DownloadCompletion.source.label("completion_source"),
            )
            .join(User, User.id == DownloadRecord.requested_by_user_id)
            .outerjoin(
                DownloadCompletion,
                and_(
                    DownloadCompletion.download_record_id == DownloadRecord.id,
                    verified_download_completion_clause(),
                ),
            )
            .where(DownloadRecord.company_id == company_id)
        )
        if task_id:
            statement = statement.where(DownloadRecord.task_id == task_id)
        if asset_id:
            statement = statement.where(DownloadRecord.asset_id == asset_id)
        if requested_by_user_id:
            statement = statement.where(
                DownloadRecord.requested_by_user_id == requested_by_user_id
            )
        if start_time:
            statement = statement.where(DownloadRecord.created_at >= start_time)
        if end_time:
            statement = statement.where(DownloadRecord.created_at < end_time)

        total = int(
            session.scalar(
                select(func.count()).select_from(statement.subquery())
            )
            or 0
        )
        rows = session.execute(
            statement.order_by(
                DownloadRecord.created_at.desc(), DownloadRecord.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        items = []
        for row in rows:
            item = dict(row)
            item["downloaded"] = item["completed_at"] is not None
            item["status"] = (
                "completed" if item["downloaded"] else "issued"
            )
            items.append(item)
        return total, items


class DownloadCompletionService:
    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _validate_replay(
        cls,
        completion: DownloadCompletion,
        *,
        download_record_id: str,
        external_event_id: str,
        source: DownloadCompletionSource,
        bytes_sent: int,
        completed_at: datetime,
        artifact_sha256: str,
        expected_size_bytes: int,
        http_status: int,
        transfer_scope: str,
        source_evidence: dict[str, str],
        signed_event_id: str,
        signed_payload_sha256: str,
    ) -> DownloadCompletion:
        if (
            completion.download_record_id != download_record_id
            or completion.external_event_id != external_event_id
            or completion.source != source
            or completion.bytes_sent != bytes_sent
            or cls._utc(completion.completed_at) != cls._utc(completed_at)
            or completion.verification_version != 1
            or completion.artifact_sha256 != artifact_sha256
            or completion.expected_size_bytes != expected_size_bytes
            or completion.http_status != http_status
            or completion.transfer_scope != transfer_scope
            or completion.source_evidence != source_evidence
            or completion.signed_event_id != signed_event_id
            or completion.signed_event_timestamp is None
            or completion.signed_payload_sha256 != signed_payload_sha256
            or completion.verified_at is None
        ):
            raise ConflictError(
                "Download-completion idempotency evidence conflicts with an existing event"
            )
        return completion

    @staticmethod
    def _validate_source_evidence(
        source: DownloadCompletionSource,
        source_evidence: dict[str, str],
    ) -> dict[str, str]:
        if source == DownloadCompletionSource.EDGE_GATEWAY:
            if set(source_evidence) != {
                "gateway_request_id",
                "gateway_transfer_reference",
            }:
                raise ConflictError(
                    "Edge-gateway download evidence is incomplete"
                )
            if any(
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or any(character.isspace() for character in value)
                for value in source_evidence.values()
            ):
                raise ConflictError("Gateway transfer references are invalid")
            return dict(source_evidence)
        if source == DownloadCompletionSource.OBS_ACCESS_LOG:
            allowed_keys = {
                "obs_bucket",
                "obs_object_key",
                "obs_version_id",
                "obs_request_id",
            }
            if (
                not {"obs_bucket", "obs_object_key"} <= set(source_evidence)
                or not set(source_evidence) <= allowed_keys
                or not (
                    source_evidence.get("obs_version_id")
                    or source_evidence.get("obs_request_id")
                )
                or any(
                    not isinstance(value, str) or not value
                    for value in source_evidence.values()
                )
                or not re.fullmatch(
                    r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
                    source_evidence["obs_bucket"],
                )
            ):
                raise ConflictError("OBS access-log evidence is invalid")
            return dict(source_evidence)
        raise ConflictError(
            "Platform-proxy download completion is not an accepted external proof"
        )

    @staticmethod
    def _bind_source_evidence_to_issuance(
        record: DownloadRecord,
        *,
        source: DownloadCompletionSource,
        source_evidence: dict[str, str],
    ) -> None:
        if (
            record.storage_binding_version != 1
            or record.storage_provider != "huawei_obs"
            or not record.storage_endpoint_host
            or not record.storage_bucket
            or not record.storage_object_key
            or not record.source_url_sha256
            or record.relay_issued_at is None
            or record.relay_expires_at is None
        ):
            raise ConflictError(
                "Download completion has no immutable storage issuance binding"
            )
        if source == DownloadCompletionSource.OBS_ACCESS_LOG:
            if (
                source_evidence.get("obs_bucket") != record.storage_bucket
                or source_evidence.get("obs_object_key")
                != record.storage_object_key
                or (
                    record.storage_version_id is not None
                    and source_evidence.get("obs_version_id")
                    != record.storage_version_id
                )
            ):
                raise ConflictError(
                    "OBS completion does not match the issued storage object"
                )
            return
        if source == DownloadCompletionSource.EDGE_GATEWAY:
            if (
                not record.gateway_registration_request_id
                or not record.gateway_ticket_id
                or not record.gateway_ticket_url_sha256
                or record.gateway_issued_at is None
                or record.gateway_expires_at is None
                or not record.gateway_transfer_reference
                or source_evidence.get("gateway_request_id")
                != record.request_id
                or source_evidence.get("gateway_transfer_reference")
                != record.gateway_transfer_reference
            ):
                raise ConflictError(
                    "Edge completion does not match the issued Gateway ticket"
                )
            return
        raise ConflictError("Download-completion source is not trusted")

    @classmethod
    def confirm(
        cls,
        session: Session,
        *,
        download_record_id: str,
        company_id: str,
        task_id: str,
        asset_id: str,
        external_event_id: str,
        source: DownloadCompletionSource,
        bytes_sent: int,
        completed_at: datetime,
        artifact_sha256: str,
        expected_size_bytes: int,
        http_status: int,
        transfer_scope: str,
        source_evidence: dict[str, str],
        signed_event_id: str,
        signed_event_timestamp: datetime,
        signed_payload_sha256: str,
    ) -> tuple[DownloadCompletion, bool]:
        if source not in {
            DownloadCompletionSource.EDGE_GATEWAY,
            DownloadCompletionSource.OBS_ACCESS_LOG,
        }:
            raise ConflictError("Download-completion source is not trusted")
        if (
            bytes_sent != expected_size_bytes
            or http_status != 200
            or transfer_scope != "full_body"
        ):
            raise ConflictError(
                "Download-completion evidence does not prove a complete transfer"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
            raise ConflictError("Artifact digest is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", signed_payload_sha256):
            raise ConflictError("Signed payload digest is invalid")
        try:
            if str(UUID(signed_event_id)) != signed_event_id:
                raise ValueError
        except (TypeError, ValueError):
            raise ConflictError("Signed event id is invalid") from None
        if (
            signed_event_timestamp.tzinfo is None
            or signed_event_timestamp.utcoffset() is None
        ):
            raise ConflictError(
                "Signed event timestamp must include a UTC offset"
            )
        source_evidence = cls._validate_source_evidence(
            source,
            source_evidence,
        )
        record = session.scalar(
            select(DownloadRecord)
            .where(DownloadRecord.id == download_record_id)
            .with_for_update()
        )
        if record is None:
            raise NotFoundError("Download issuance record does not exist")
        if (
            record.company_id != company_id
            or record.task_id != task_id
            or record.asset_id != asset_id
        ):
            raise ConflictError(
                "Download-completion event does not match the issued company, task, and artifact"
            )
        cls._bind_source_evidence_to_issuance(
            record,
            source=source,
            source_evidence=source_evidence,
        )
        # Re-read after the issuance-row lock. Concurrent confirmations for the
        # same issuance must replay the winning immutable event, not return 409.
        existing_signed_event = session.scalar(
            select(DownloadCompletion).where(
                DownloadCompletion.signed_event_id == signed_event_id
            )
        )
        if existing_signed_event is not None:
            return (
                cls._validate_replay(
                    existing_signed_event,
                    download_record_id=download_record_id,
                    external_event_id=external_event_id,
                    source=source,
                    bytes_sent=bytes_sent,
                    completed_at=completed_at,
                    artifact_sha256=artifact_sha256,
                    expected_size_bytes=expected_size_bytes,
                    http_status=http_status,
                    transfer_scope=transfer_scope,
                    source_evidence=source_evidence,
                    signed_event_id=signed_event_id,
                    signed_payload_sha256=signed_payload_sha256,
                ),
                False,
            )
        existing_event = session.scalar(
            select(DownloadCompletion).where(
                DownloadCompletion.external_event_id == external_event_id
            )
        )
        if existing_event is not None:
            return (
                cls._validate_replay(
                    existing_event,
                    download_record_id=download_record_id,
                    external_event_id=external_event_id,
                    source=source,
                    bytes_sent=bytes_sent,
                    completed_at=completed_at,
                    artifact_sha256=artifact_sha256,
                    expected_size_bytes=expected_size_bytes,
                    http_status=http_status,
                    transfer_scope=transfer_scope,
                    source_evidence=source_evidence,
                    signed_event_id=signed_event_id,
                    signed_payload_sha256=signed_payload_sha256,
                ),
                False,
            )
        if cls._utc(completed_at) < cls._utc(record.created_at):
            raise ConflictError("Download completion cannot precede URL issuance")
        if cls._utc(completed_at) > cls._utc(utcnow()) + timedelta(minutes=5):
            raise ConflictError("Download completion is too far in the future")

        artifact = session.scalar(
            select(TaskArtifact).where(
                TaskArtifact.company_id == record.company_id,
                TaskArtifact.task_id == record.task_id,
                TaskArtifact.asset_id == record.asset_id,
            )
        )
        if artifact is None:
            raise ConflictError(
                "Download record has no verifiable immutable artifact snapshot"
            )
        if (
            bytes_sent != artifact.size_bytes
            or expected_size_bytes != artifact.size_bytes
        ):
            raise ConflictError(
                "Download-completion byte count does not match the immutable artifact"
            )
        if artifact_sha256 != artifact.sha256:
            raise ConflictError(
                "Download-completion digest does not match the immutable artifact"
            )

        existing_completion = session.scalar(
            select(DownloadCompletion).where(
                DownloadCompletion.download_record_id == record.id
            )
        )
        if existing_completion is not None:
            return (
                cls._validate_replay(
                    existing_completion,
                    download_record_id=download_record_id,
                    external_event_id=external_event_id,
                    source=source,
                    bytes_sent=bytes_sent,
                    completed_at=completed_at,
                    artifact_sha256=artifact_sha256,
                    expected_size_bytes=expected_size_bytes,
                    http_status=http_status,
                    transfer_scope=transfer_scope,
                    source_evidence=source_evidence,
                    signed_event_id=signed_event_id,
                    signed_payload_sha256=signed_payload_sha256,
                ),
                False,
            )

        now = utcnow()
        completion = DownloadCompletion(
            download_record_id=record.id,
            external_event_id=external_event_id,
            source=source,
            bytes_sent=bytes_sent,
            completed_at=completed_at,
            verification_version=1,
            artifact_sha256=artifact_sha256,
            expected_size_bytes=expected_size_bytes,
            http_status=http_status,
            transfer_scope=transfer_scope,
            source_evidence=source_evidence,
            signed_event_id=signed_event_id,
            signed_event_timestamp=signed_event_timestamp,
            signed_payload_sha256=signed_payload_sha256,
            verified_at=now,
            created_at=now,
        )
        try:
            with session.begin_nested():
                session.add(completion)
                session.flush()
        except IntegrityError:
            # The database uniqueness constraints are the final arbiter for
            # races across workers or across different issuance-row locks.
            winner = session.scalar(
                select(DownloadCompletion).where(
                    or_(
                        DownloadCompletion.signed_event_id == signed_event_id,
                        DownloadCompletion.external_event_id == external_event_id,
                        DownloadCompletion.download_record_id == download_record_id,
                    )
                )
            )
            if winner is not None:
                return (
                    cls._validate_replay(
                        winner,
                        download_record_id=download_record_id,
                        external_event_id=external_event_id,
                        source=source,
                        bytes_sent=bytes_sent,
                        completed_at=completed_at,
                        artifact_sha256=artifact_sha256,
                        expected_size_bytes=expected_size_bytes,
                        http_status=http_status,
                        transfer_scope=transfer_scope,
                        source_evidence=source_evidence,
                        signed_event_id=signed_event_id,
                        signed_payload_sha256=signed_payload_sha256,
                    ),
                    False,
                )
            raise ConflictError(
                "Download issuance was already completed by another trusted event"
            ) from None
        return completion, True


class ReportService:
    @staticmethod
    def _task_statement(
        *,
        company_id: str,
        employee_user_id: str | None,
        model_id: str | None,
        status: TaskStatus | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ):
        statement = (
            select(
                GenerationTask.id.label("task_id"),
                GenerationTask.user_id.label("employee_user_id"),
                User.display_name.label("employee_display_name"),
                User.email.label("employee_email"),
                GenerationTask.model_id,
                ModelDefinition.display_name.label("model_display_name"),
                GenerationTask.status,
                GenerationTask.quote_cents,
                GenerationTask.actual_cost_cents,
                GenerationTask.request_payload,
                GenerationTask.created_at,
                GenerationTask.updated_at,
            )
            .join(User, User.id == GenerationTask.user_id)
            .join(ModelDefinition, ModelDefinition.id == GenerationTask.model_id)
            .where(GenerationTask.company_id == company_id)
        )
        if employee_user_id:
            statement = statement.where(
                GenerationTask.user_id == employee_user_id
            )
        if model_id:
            statement = statement.where(GenerationTask.model_id == model_id)
        if status:
            statement = statement.where(GenerationTask.status == status)
        if start_time:
            statement = statement.where(GenerationTask.created_at >= start_time)
        if end_time:
            statement = statement.where(GenerationTask.created_at < end_time)
        return statement

    @classmethod
    def task_page(
        cls,
        session: Session,
        *,
        company_id: str,
        page: int,
        page_size: int,
        employee_user_id: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        statement = cls._task_statement(
            company_id=company_id,
            employee_user_id=employee_user_id,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        filtered = statement.subquery()
        total = int(
            session.scalar(select(func.count()).select_from(filtered)) or 0
        )
        total_actual_cost = int(
            session.scalar(
                select(func.coalesce(func.sum(filtered.c.actual_cost_cents), 0))
            )
            or 0
        )
        rows = session.execute(
            statement.order_by(
                GenerationTask.created_at.desc(), GenerationTask.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        return total, total_actual_cost, [dict(row) for row in rows]

    @staticmethod
    def _consumption_statement(
        *,
        company_id: str | None = None,
        employee_user_id: str | None = None,
        employee_query: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        statement = (
            select(
                LedgerEntry.id.label("ledger_entry_id"),
                GenerationTask.company_id.label("company_id"),
                Company.name.label("company_name"),
                GenerationTask.id.label("task_id"),
                GenerationTask.user_id.label("employee_user_id"),
                User.display_name.label("employee_display_name"),
                User.email.label("employee_email"),
                GenerationTask.model_id,
                ModelDefinition.display_name.label("model_display_name"),
                GenerationTask.status.label("task_status"),
                GenerationTask.pricing_snapshot,
                LedgerEntry.amount_cents,
                LedgerEntry.created_at.label("consumed_at"),
            )
            .join(GenerationTask, GenerationTask.id == LedgerEntry.task_id)
            .join(Company, Company.id == GenerationTask.company_id)
            .join(User, User.id == GenerationTask.user_id)
            .join(ModelDefinition, ModelDefinition.id == GenerationTask.model_id)
            .where(LedgerEntry.kind == LedgerKind.SETTLE)
        )
        if company_id:
            statement = statement.where(LedgerEntry.company_id == company_id)
        if employee_user_id:
            statement = statement.where(
                GenerationTask.user_id == employee_user_id
            )
        if employee_query:
            normalized_query = employee_query.strip().lower()
            if normalized_query:
                statement = statement.where(
                    or_(
                        func.lower(User.display_name).contains(
                            normalized_query, autoescape=True
                        ),
                        func.lower(User.email).contains(
                            normalized_query, autoescape=True
                        ),
                    )
                )
        if model_id:
            statement = statement.where(GenerationTask.model_id == model_id)
        if status:
            statement = statement.where(GenerationTask.status == status)
        if start_time:
            statement = statement.where(LedgerEntry.created_at >= start_time)
        if end_time:
            statement = statement.where(LedgerEntry.created_at < end_time)
        return statement

    @staticmethod
    def _consumption_row(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        pricing_snapshot = result.pop("pricing_snapshot", None) or {}
        pricing_mode = pricing_snapshot.get("mode")
        result["pricing_mode"] = (
            pricing_mode
            if pricing_mode in {"per_second", "per_item"}
            else None
        )
        unit_price = pricing_snapshot.get("unit_price_cents")
        quantity = pricing_snapshot.get("quantity")
        result["unit_price_cents"] = (
            unit_price if isinstance(unit_price, int) else None
        )
        result["quantity"] = quantity if isinstance(quantity, int) else None
        return result

    @classmethod
    def consumption_page(
        cls,
        session: Session,
        *,
        company_id: str | None,
        page: int,
        page_size: int,
        employee_user_id: str | None = None,
        employee_query: str | None = None,
        model_id: str | None = None,
        status: TaskStatus | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        statement = cls._consumption_statement(
            company_id=company_id,
            employee_user_id=employee_user_id,
            employee_query=employee_query,
            model_id=model_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
        )
        filtered = statement.subquery("filtered_consumption")
        summary = (
            select(
                func.count().label("report_total"),
                func.coalesce(func.sum(filtered.c.amount_cents), 0).label(
                    "report_total_amount_cents"
                ),
            )
            .select_from(filtered)
            .cte("consumption_summary")
        )
        page_rows = (
            select(filtered)
            .order_by(
                filtered.c.consumed_at.desc(),
                filtered.c.ledger_entry_id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .cte("consumption_page")
        )
        result_rows = list(
            session.execute(
                select(summary, page_rows).select_from(
                    summary.outerjoin(page_rows, true())
                )
            ).mappings()
        )
        if not result_rows:
            return 0, 0, []
        total = int(result_rows[0]["report_total"] or 0)
        total_amount = int(
            result_rows[0]["report_total_amount_cents"] or 0
        )
        items = [
            cls._consumption_row(
                {
                    key: row[key]
                    for key in page_rows.c.keys()
                }
            )
            for row in result_rows
            if row["ledger_entry_id"] is not None
        ]
        return total, total_amount, items

    @classmethod
    def task_export(cls, session: Session, **filters: Any) -> str:
        statement = cls._task_statement(**filters).order_by(
            GenerationTask.created_at.desc(), GenerationTask.id.desc()
        )
        rows = list(
            session.execute(statement.limit(MAX_EXPORT_ROWS + 1)).mappings()
        )
        if len(rows) > MAX_EXPORT_ROWS:
            raise DomainError(
                "筛选结果超过 10000 行，请缩小导出时间范围",
                "export_too_large",
                413,
            )
        return build_csv(
            (
                "任务ID",
                "员工ID",
                "员工姓名",
                "员工邮箱",
                "模型ID",
                "模型名称",
                "状态",
                "报价（分）",
                "实际消费（分）",
                "请求参数",
                "创建时间",
                "更新时间",
            ),
            (
                (
                    row["task_id"],
                    row["employee_user_id"],
                    row["employee_display_name"],
                    row["employee_email"],
                    row["model_id"],
                    row["model_display_name"],
                    row["status"].value,
                    row["quote_cents"],
                    row["actual_cost_cents"],
                    json.dumps(
                        row["request_payload"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    row["created_at"],
                    row["updated_at"],
                )
                for row in rows
            ),
        )

    @classmethod
    def consumption_export(cls, session: Session, **filters: Any) -> str:
        statement = cls._consumption_statement(**filters).order_by(
            LedgerEntry.created_at.desc(), LedgerEntry.id.desc()
        )
        rows = [
            cls._consumption_row(dict(row))
            for row in session.execute(
                statement.limit(MAX_EXPORT_ROWS + 1)
            ).mappings()
        ]
        if len(rows) > MAX_EXPORT_ROWS:
            raise DomainError(
                "筛选结果超过 10000 行，请缩小导出时间范围",
                "export_too_large",
                413,
            )
        return build_csv(
            (
                "流水ID",
                "公司ID",
                "公司名称",
                "任务ID",
                "员工ID",
                "员工姓名",
                "员工邮箱",
                "模型ID",
                "模型名称",
                "任务状态",
                "计费方式",
                "单价（分）",
                "计费数量",
                "消费金额（分）",
                "消费时间",
            ),
            (
                (
                    row["ledger_entry_id"],
                    row["company_id"],
                    row["company_name"],
                    row["task_id"],
                    row["employee_user_id"],
                    row["employee_display_name"],
                    row["employee_email"],
                    row["model_id"],
                    row["model_display_name"],
                    row["task_status"].value,
                    row["pricing_mode"],
                    row["unit_price_cents"],
                    row["quantity"],
                    row["amount_cents"],
                    row["consumed_at"],
                )
                for row in rows
            ),
        )
