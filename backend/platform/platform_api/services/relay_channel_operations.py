from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import RelayChannelOperationJournal, utcnow
from .audit import AuditService


RelayChannelOperationKind = Literal["test", "status"]


class RelayChannelOperationConflict(Exception):
    """The tenant-global operation id is already bound to other evidence."""


def _canonical_tenant_id(tenant_id: str) -> str:
    canonical = str(UUID(tenant_id))
    if canonical != tenant_id:
        raise ValueError("Relay tenant_id must use canonical UUID form")
    return canonical


def _intent_payload(
    *,
    tenant_id: str,
    operation_id: str,
    channel_id: int,
    kind: RelayChannelOperationKind,
    actor_user_id: str,
    reason: str,
    expected_revision: str | None,
    target_status: str | None,
) -> tuple[dict[str, Any], str]:
    tenant_id = _canonical_tenant_id(tenant_id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", operation_id):
        raise ValueError("operation_id is invalid")
    if channel_id <= 0:
        raise ValueError("channel_id is invalid")
    if actor_user_id != actor_user_id.strip() or not (1 <= len(actor_user_id) <= 128):
        raise ValueError("actor_user_id is invalid")
    if reason != reason.strip() or not (3 <= len(reason) <= 240):
        raise ValueError("reason is invalid")
    if kind == "test":
        if expected_revision is not None or target_status is not None:
            raise ValueError("channel test intent has status fields")
    elif kind == "status":
        if not expected_revision or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", expected_revision
        ):
            raise ValueError("expected_revision is invalid")
        if target_status not in {"enabled", "manually_disabled"}:
            raise ValueError("target_status is invalid")
    else:
        raise ValueError("channel operation kind is invalid")
    payload = {
        "tenant_id": tenant_id,
        "operation_id": operation_id,
        "channel_id": channel_id,
        "kind": kind,
        "actor_user_id": actor_user_id,
        "reason": reason,
        "expected_revision": expected_revision,
        "target_status": target_status,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return payload, hashlib.sha256(encoded).hexdigest()


def _matches(
    row: RelayChannelOperationJournal,
    *,
    tenant_id: str,
    operation_id: str,
    channel_id: int,
    kind: RelayChannelOperationKind,
    digest: str,
) -> bool:
    return (
        row.tenant_id == tenant_id
        and row.operation_id == operation_id
        and row.channel_id == channel_id
        and row.kind == kind
        and row.intent_sha256 == digest
    )


class RelayChannelOperationJournalService:
    @staticmethod
    def find(
        session: Session, *, tenant_id: str, operation_id: str
    ) -> RelayChannelOperationJournal | None:
        canonical_tenant_id = _canonical_tenant_id(tenant_id)
        return session.scalar(
            select(RelayChannelOperationJournal).where(
                RelayChannelOperationJournal.tenant_id == canonical_tenant_id,
                RelayChannelOperationJournal.operation_id == operation_id,
            )
        )

    @staticmethod
    def claim(
        session: Session,
        *,
        tenant_id: str,
        operation_id: str,
        channel_id: int,
        kind: RelayChannelOperationKind,
        actor_user_id: str,
        reason: str,
        expected_revision: str | None,
        target_status: str | None,
        before_summary: dict[str, Any],
        approval_proof: dict[str, Any],
        request_id: str,
    ) -> tuple[RelayChannelOperationJournal, bool]:
        payload, digest = _intent_payload(
            tenant_id=tenant_id,
            operation_id=operation_id,
            channel_id=channel_id,
            kind=kind,
            actor_user_id=actor_user_id,
            reason=reason,
            expected_revision=expected_revision,
            target_status=target_status,
        )
        canonical_tenant_id = payload["tenant_id"]
        action = (
            "relay.channel.test.approve"
            if kind == "test"
            else "relay.channel.status_change.approve"
        )
        candidate: RelayChannelOperationJournal | None = None
        try:
            # The audit and journal insert share the savepoint.  A uniqueness
            # race rolls both back, so AuditLog never becomes the mutex and no
            # losing approval row is left behind.
            with session.begin_nested():
                approval = AuditService.append(
                    session,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type="relay_channel",
                    target_id=str(channel_id),
                    before_summary=before_summary,
                    after_summary=approval_proof,
                    request_id=request_id,
                )
                candidate = RelayChannelOperationJournal(
                    tenant_id=canonical_tenant_id,
                    operation_id=operation_id,
                    channel_id=channel_id,
                    kind=kind,
                    actor_user_id=actor_user_id,
                    reason=reason,
                    expected_revision=expected_revision,
                    target_status=target_status,
                    intent_sha256=digest,
                    intent_payload=payload,
                    before_summary=before_summary,
                    state="approved",
                    approval_audit_id=approval.id,
                    approval_request_id=request_id,
                )
                session.add(candidate)
                session.flush()
            return candidate, True
        except IntegrityError:
            existing = session.scalar(
                select(RelayChannelOperationJournal).where(
                    RelayChannelOperationJournal.tenant_id == canonical_tenant_id,
                    RelayChannelOperationJournal.operation_id == operation_id,
                )
            )
            if existing is None:
                raise
            if not _matches(
                existing,
                tenant_id=canonical_tenant_id,
                operation_id=operation_id,
                channel_id=channel_id,
                kind=kind,
                digest=digest,
            ):
                raise RelayChannelOperationConflict(
                    "operation_id is already bound to different Relay channel evidence"
                )
            return existing, False

    @staticmethod
    def assert_matches(
        row: RelayChannelOperationJournal,
        *,
        tenant_id: str,
        operation_id: str,
        channel_id: int,
        kind: RelayChannelOperationKind,
        actor_user_id: str,
        reason: str,
        expected_revision: str | None,
        target_status: str | None,
    ) -> None:
        payload, digest = _intent_payload(
            tenant_id=tenant_id,
            operation_id=operation_id,
            channel_id=channel_id,
            kind=kind,
            actor_user_id=actor_user_id,
            reason=reason,
            expected_revision=expected_revision,
            target_status=target_status,
        )
        if not _matches(
            row,
            tenant_id=payload["tenant_id"],
            operation_id=operation_id,
            channel_id=channel_id,
            kind=kind,
            digest=digest,
        ):
            raise RelayChannelOperationConflict(
                "operation_id is already bound to different Relay channel evidence"
            )

    @staticmethod
    def record_receipt(
        session: Session,
        *,
        journal_id: str,
        receipt: dict[str, Any],
        relay_intent_sha256: str,
        result_summary: dict[str, Any],
        request_id: str,
    ) -> RelayChannelOperationJournal:
        if not re.fullmatch(r"[0-9a-f]{64}", relay_intent_sha256):
            raise ValueError("Relay intent digest is invalid")
        row = session.scalar(
            select(RelayChannelOperationJournal)
            .where(RelayChannelOperationJournal.id == journal_id)
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("Relay channel operation journal disappeared")
        if row.relay_intent_sha256 not in {None, relay_intent_sha256}:
            raise RelayChannelOperationConflict(
                "Relay receipt intent conflicts with the Platform journal"
            )
        if row.state == "completed":
            return row

        row.relay_intent_sha256 = relay_intent_sha256
        row.relay_receipt = receipt
        row.result_request_id = request_id
        if receipt.get("state") == "pending":
            session.flush()
            return row

        action = (
            "relay.channel.test"
            if row.kind == "test"
            else "relay.channel.status_change"
        )
        result_audit = AuditService.append(
            session,
            actor_user_id=row.actor_user_id,
            action=action,
            target_type="relay_channel",
            target_id=str(row.channel_id),
            before_summary=row.before_summary,
            after_summary={
                **row.intent_payload,
                "approved": True,
                **result_summary,
                "approval_audit_id": row.approval_audit_id,
            },
            request_id=request_id,
        )
        now = utcnow()
        row.state = "completed"
        row.result_audit_id = result_audit.id
        row.completed_at = now
        row.updated_at = now
        session.flush()
        return row

