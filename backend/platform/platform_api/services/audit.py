from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuditLog, AuditOutcome, utcnow


_LAST_AUDIT_CREATED_AT_KEY = "platform.audit.last_created_at"


def _next_audit_created_at(session: Session) -> datetime:
    """Return a clock time that preserves append order inside one Session.

    Audit identifiers are random UUIDs, so they cannot be used as an insertion
    sequence when two entries receive the same database timestamp.  A workflow
    often appends several causally ordered audit entries in one transaction;
    keep those timestamps strictly monotonic even when the host clock has not
    advanced (or moves backwards).  Independent concurrent transactions remain
    intentionally unordered because assigning a global order would require a
    database sequence and a schema migration.
    """

    observed = utcnow()
    previous = session.info.get(_LAST_AUDIT_CREATED_AT_KEY)
    if previous is not None and observed <= previous:
        observed = previous + timedelta(microseconds=1)
    session.info[_LAST_AUDIT_CREATED_AT_KEY] = observed
    return observed


class AuditService:
    @staticmethod
    def append(
        session: Session,
        *,
        actor_user_id: str | None = None,
        actor_kind: str = "user",
        actor_key: str | None = None,
        action: str,
        target_type: str,
        target_id: str,
        before_summary: dict,
        after_summary: dict,
        request_id: str,
        outcome: AuditOutcome = AuditOutcome.SUCCEEDED,
    ) -> AuditLog:
        """Append one immutable, caller-observed execution outcome.

        Successful mutation paths keep the backwards-compatible default.
        Callers which have durable evidence of a rejected/failed or ambiguous
        side effect must pass ``FAILED`` or ``UNKNOWN`` respectively; the API
        then returns that stored value instead of inventing a result while
        adapting the response.
        """
        if actor_kind == "user":
            if actor_user_id is None or actor_key is not None:
                raise ValueError("User audit actors require only actor_user_id")
        elif actor_kind == "system":
            if actor_user_id is not None or actor_key is None:
                raise ValueError("System audit actors require only actor_key")
            if (
                actor_key != actor_key.strip()
                or not actor_key
                or len(actor_key) > 120
            ):
                raise ValueError("System audit actor_key is invalid")
        else:
            raise ValueError("Audit actor_kind is invalid")
        entry = AuditLog(
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            actor_key=actor_key,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_summary=before_summary,
            after_summary=after_summary,
            outcome=outcome,
            request_id=request_id,
            created_at=_next_audit_created_at(session),
        )
        session.add(entry)
        session.flush()
        return entry

    @staticmethod
    def page(
        session: Session, *, page: int, page_size: int
    ) -> tuple[int, list[AuditLog]]:
        total = session.scalar(select(func.count(AuditLog.id))) or 0
        items = list(
            session.scalars(
                select(AuditLog)
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return total, items
