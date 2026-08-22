from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuditLog, AuditOutcome


class AuditService:
    @staticmethod
    def append(
        session: Session,
        *,
        actor_user_id: str,
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
        entry = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_summary=before_summary,
            after_summary=after_summary,
            outcome=outcome,
            request_id=request_id,
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
