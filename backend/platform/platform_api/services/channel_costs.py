from __future__ import annotations

from datetime import datetime, timezone
import hmac
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import (
    ChannelCostEntry,
    ChannelCostSource,
    ChannelType,
    Company,
    GenerationTask,
    PersonalWorkspace,
    TaskStatus,
    new_id,
    utcnow,
)
from .errors import ConflictError, NotFoundError


MAX_MONEY_CENTS = 9_000_000_000_000_000


class ChannelCostService:
    @staticmethod
    def _required_text(value: str, *, field_name: str, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ConflictError(f"{field_name} must not be blank")
        if len(normalized) > max_length:
            raise ConflictError(f"{field_name} is too long")
        return normalized

    @staticmethod
    def _utc_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ConflictError("occurred_at must include a UTC offset")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _stored_utc_timestamp(value: datetime) -> datetime:
        # SQLite discards timezone metadata. All writes are normalized to UTC,
        # so a naive value read from SQLite is UTC rather than local time.
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _validate_replay(
        cls,
        entry: ChannelCostEntry,
        *,
        amount_cents: int,
        channel_key: str,
        channel_type: ChannelType,
        occurred_at: datetime,
        external_reference: str,
        company_id: str | None,
        personal_workspace_id: str | None,
        task_id: str | None,
        relay_job_id: str | None,
        note: str,
        evidence_source: str | None,
        evidence_reference: str | None,
        source_document_sha256: str | None,
        relay_event_id: str | None,
        relay_payload_sha256: str | None,
    ) -> ChannelCostEntry:
        # A signed Relay delivery must never acknowledge an operator-created
        # row. Report that trust-boundary collision before comparing the newer
        # provider-evidence fields so the reason remains deterministic.
        if relay_event_id is not None and entry.relay_event_id is None:
            raise ConflictError(
                "signed Relay event collides with an unsigned channel cost entry"
            )
        if (
            entry.amount_cents != amount_cents
            or entry.channel_key != channel_key
            or entry.channel_type != channel_type
            or cls._stored_utc_timestamp(entry.occurred_at) != occurred_at
            or entry.external_reference != external_reference
            or entry.company_id != company_id
            or entry.personal_workspace_id != personal_workspace_id
            or entry.task_id != task_id
            or entry.relay_job_id != relay_job_id
            or entry.note != note
            or entry.evidence_source != evidence_source
            or entry.evidence_reference != evidence_reference
            or entry.source_document_sha256 != source_document_sha256
        ):
            raise ConflictError(
                "idempotency_key is already used by a different channel cost entry"
            )
        if relay_event_id is not None:
            if (
                entry.relay_event_id != relay_event_id
                or entry.relay_payload_sha256 is None
                or relay_payload_sha256 is None
                or not hmac.compare_digest(
                    entry.relay_payload_sha256, relay_payload_sha256
                )
            ):
                raise ConflictError(
                    "idempotency_key is already used by a different Relay event"
                )
        return entry

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        amount_cents: int,
        idempotency_key: str,
        channel_key: str,
        channel_type: ChannelType,
        occurred_at: datetime,
        external_reference: str,
        company_id: str | None = None,
        personal_workspace_id: str | None = None,
        task_id: str | None = None,
        relay_job_id: str | None = None,
        note: str = "",
        evidence_source: str | None = None,
        evidence_reference: str | None = None,
        source_document_sha256: str | None = None,
        relay_event_id: str | None = None,
        relay_event_timestamp: datetime | None = None,
        relay_payload_sha256: str | None = None,
        source: ChannelCostSource,
        recorded_by_user_id: str | None,
    ) -> tuple[ChannelCostEntry, bool]:
        if (
            isinstance(amount_cents, bool)
            or amount_cents < -MAX_MONEY_CENTS
            or amount_cents > MAX_MONEY_CENTS
        ):
            raise ConflictError("amount_cents is outside the supported range")
        normalized_idempotency_key = cls._required_text(
            idempotency_key, field_name="idempotency_key", max_length=160
        )
        normalized_channel_key = cls._required_text(
            channel_key, field_name="channel_key", max_length=120
        )
        normalized_external_reference = cls._required_text(
            external_reference,
            field_name="external_reference",
            max_length=240,
        )
        normalized_note = note.strip()
        if len(normalized_note) > 240:
            raise ConflictError("note is too long")
        normalized_occurred_at = cls._utc_timestamp(occurred_at)

        evidence_fields = (
            evidence_source,
            evidence_reference,
            source_document_sha256,
        )
        if source != ChannelCostSource.RELAY:
            if any(value is not None for value in evidence_fields):
                raise ConflictError(
                    "Only Relay may attach provider cost evidence"
                )
        else:
            allowed_evidence_sources = {
                "provider_reported",
                "provider_invoice",
                "contract_rate",
                "operator_adjustment",
            }
            if evidence_source not in allowed_evidence_sources:
                raise ConflictError(
                    "Relay channel costs require explicit provider-side evidence"
                )
            if evidence_source in {"provider_invoice", "contract_rate"}:
                if (
                    not evidence_reference
                    or evidence_reference.strip() != evidence_reference
                    or len(evidence_reference) > 240
                    or source_document_sha256 is None
                    or len(source_document_sha256) != 64
                    or source_document_sha256.lower()
                    != source_document_sha256
                    or any(
                        character not in "0123456789abcdef"
                        for character in source_document_sha256
                    )
                ):
                    raise ConflictError(
                        "Relay invoice and contract costs require document evidence"
                    )
            elif evidence_reference is not None or source_document_sha256 is not None:
                raise ConflictError(
                    "Document evidence is only accepted for invoice and contract costs"
                )

        evidence_values = (
            relay_event_id,
            relay_event_timestamp,
            relay_payload_sha256,
        )
        if any(value is not None for value in evidence_values) and not all(
            value is not None for value in evidence_values
        ):
            raise ConflictError("Relay channel-cost event evidence is incomplete")
        if relay_event_id is not None and source != ChannelCostSource.RELAY:
            raise ConflictError("Only Relay may attach channel-cost event evidence")
        normalized_relay_event_timestamp = (
            cls._utc_timestamp(relay_event_timestamp)
            if relay_event_timestamp is not None
            else None
        )
        if relay_payload_sha256 is not None and (
            len(relay_payload_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in relay_payload_sha256
            )
        ):
            raise ConflictError("Relay channel-cost payload digest is invalid")

        if task_id is not None:
            submitted_company_id = company_id
            submitted_personal_workspace_id = personal_workspace_id
            submitted_relay_job_id = relay_job_id
            task = session.scalar(
                select(GenerationTask)
                .where(GenerationTask.id == task_id)
                .with_for_update()
            )
            if task is None:
                raise NotFoundError("generation task does not exist")
            if relay_event_id is not None:
                # A provider charge is incurred when the upstream provider
                # succeeds, before artifact transfer and the customer task's
                # terminal callback necessarily finish. Signed Relay costs do
                # not mutate the wallet, so accept them as soon as their
                # task/company/relay-job linkage can be proven below. Requiring
                # a terminal task here would turn slow OBS transfers into 409
                # retries and eventually dead-letter a legitimate cost.
                if (submitted_company_id is None) == (
                    submitted_personal_workspace_id is None
                ):
                    raise ConflictError(
                        "signed Relay task costs require exactly one billing scope"
                    )
                if submitted_relay_job_id is None:
                    raise ConflictError(
                        "signed Relay task costs require an explicit relay_job_id"
                    )
            elif task.status != TaskStatus.SUCCEEDED:
                raise ConflictError(
                    "channel costs can only be linked to a succeeded task"
                )
            if company_id is not None and company_id != task.company_id:
                raise ConflictError("company_id does not match the task")
            if (
                personal_workspace_id is not None
                and personal_workspace_id != task.personal_workspace_id
            ):
                raise ConflictError("personal_workspace_id does not match the task")
            if relay_job_id is not None and relay_job_id != task.relay_job_id:
                raise ConflictError("relay_job_id does not match the task")
            company_id = task.company_id
            personal_workspace_id = task.personal_workspace_id
            relay_job_id = task.relay_job_id
        else:
            if company_id is not None and personal_workspace_id is not None:
                raise ConflictError("channel cost cannot belong to two billing scopes")
            if company_id is not None and session.get(Company, company_id) is None:
                raise NotFoundError("company does not exist")
            if (
                personal_workspace_id is not None
                and session.get(PersonalWorkspace, personal_workspace_id) is None
            ):
                raise NotFoundError("personal workspace does not exist")

        values: dict[str, Any] = {
            "id": new_id(),
            "amount_cents": amount_cents,
            "idempotency_key": normalized_idempotency_key,
            "channel_key": normalized_channel_key,
            "channel_type": channel_type,
            "occurred_at": normalized_occurred_at,
            "external_reference": normalized_external_reference,
            "company_id": company_id,
            "personal_workspace_id": personal_workspace_id,
            "task_id": task_id,
            "relay_job_id": relay_job_id,
            "relay_event_id": relay_event_id,
            "relay_event_timestamp": normalized_relay_event_timestamp,
            "relay_payload_sha256": relay_payload_sha256,
            "note": normalized_note,
            "evidence_source": evidence_source,
            "evidence_reference": evidence_reference,
            "source_document_sha256": source_document_sha256,
            "source": source,
            "recorded_by_user_id": recorded_by_user_id,
            "created_at": utcnow(),
        }
        existing = session.scalar(
            select(ChannelCostEntry).where(
                ChannelCostEntry.idempotency_key == normalized_idempotency_key
            )
        )
        if existing is not None:
            return (
                cls._validate_replay(existing, **{k: values[k] for k in (
                    "amount_cents",
                    "channel_key",
                    "channel_type",
                    "occurred_at",
                    "external_reference",
                    "company_id",
                    "personal_workspace_id",
                    "task_id",
                    "relay_job_id",
                    "note",
                    "evidence_source",
                    "evidence_reference",
                    "source_document_sha256",
                    "relay_event_id",
                    "relay_payload_sha256",
                )}),
                False,
            )

        if relay_event_id is not None:
            existing_event = session.scalar(
                select(ChannelCostEntry).where(
                    ChannelCostEntry.relay_event_id == relay_event_id
                )
            )
            if existing_event is not None:
                if existing_event.idempotency_key != normalized_idempotency_key:
                    raise ConflictError(
                        "relay_event_id is already used by a different channel cost entry"
                    )
                return (
                    cls._validate_replay(
                        existing_event,
                        **{k: values[k] for k in (
                            "amount_cents",
                            "channel_key",
                            "channel_type",
                            "occurred_at",
                            "external_reference",
                            "company_id",
                            "personal_workspace_id",
                            "task_id",
                            "relay_job_id",
                            "note",
                            "evidence_source",
                            "evidence_reference",
                            "source_document_sha256",
                            "relay_event_id",
                            "relay_payload_sha256",
                        )},
                    ),
                    False,
                )

        dialect_name = session.get_bind().dialect.name
        if dialect_name == "sqlite":
            insert_statement = sqlite_insert(ChannelCostEntry)
        elif dialect_name == "postgresql":
            insert_statement = postgresql_insert(ChannelCostEntry)
        else:
            entry = ChannelCostEntry(**values)
            session.add(entry)
            session.flush()
            return entry, True

        inserted_id = session.scalar(
            insert_statement.values(**values)
            .on_conflict_do_nothing()
            .returning(ChannelCostEntry.id)
        )
        entry = session.scalar(
            select(ChannelCostEntry).where(
                ChannelCostEntry.idempotency_key == normalized_idempotency_key
            )
        )
        if entry is None:
            if relay_event_id is not None:
                event_entry = session.scalar(
                    select(ChannelCostEntry).where(
                        ChannelCostEntry.relay_event_id == relay_event_id
                    )
                )
                if event_entry is not None:
                    raise ConflictError(
                        "relay_event_id is already used by a different channel cost entry"
                    )
            raise ConflictError("channel cost entry could not be persisted")
        if inserted_id is None:
            cls._validate_replay(
                entry,
                **{k: values[k] for k in (
                    "amount_cents",
                    "channel_key",
                    "channel_type",
                    "occurred_at",
                    "external_reference",
                    "company_id",
                    "personal_workspace_id",
                    "task_id",
                    "relay_job_id",
                    "note",
                    "evidence_source",
                    "evidence_reference",
                    "source_document_sha256",
                    "relay_event_id",
                    "relay_payload_sha256",
                )},
            )
        return entry, inserted_id is not None

    @staticmethod
    def page(
        session: Session,
        *,
        page: int,
        page_size: int,
        company_id: str | None = None,
        personal_workspace_id: str | None = None,
        task_id: str | None = None,
        channel_key: str | None = None,
        channel_type: ChannelType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, int, list[ChannelCostEntry]]:
        statement = select(ChannelCostEntry)
        if company_id is not None:
            if session.get(Company, company_id) is None:
                raise NotFoundError("company does not exist")
            statement = statement.where(ChannelCostEntry.company_id == company_id)
        if personal_workspace_id is not None:
            if session.get(PersonalWorkspace, personal_workspace_id) is None:
                raise NotFoundError("personal workspace does not exist")
            statement = statement.where(
                ChannelCostEntry.personal_workspace_id == personal_workspace_id
            )
        if task_id is not None:
            statement = statement.where(ChannelCostEntry.task_id == task_id)
        if channel_key is not None:
            statement = statement.where(
                ChannelCostEntry.channel_key == channel_key.strip()
            )
        if channel_type is not None:
            statement = statement.where(
                ChannelCostEntry.channel_type == channel_type
            )
        if start_time is not None:
            statement = statement.where(ChannelCostEntry.occurred_at >= start_time)
        if end_time is not None:
            statement = statement.where(ChannelCostEntry.occurred_at < end_time)

        filtered = statement.subquery("filtered_channel_costs")
        total, total_amount_cents = session.execute(
            select(
                func.count(filtered.c.id),
                func.coalesce(func.sum(filtered.c.amount_cents), 0),
            )
        ).one()
        items = list(
            session.scalars(
                statement.order_by(
                    ChannelCostEntry.occurred_at.desc(),
                    ChannelCostEntry.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return int(total or 0), int(total_amount_cents or 0), items
