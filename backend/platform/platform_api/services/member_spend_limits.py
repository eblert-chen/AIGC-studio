"""Per-member ceilings on company point reservations.

The company wallet is shared, so every member draws on the same balance. That
makes "who may spend how much" a policy the owner layers on top -- and a policy
is only real if it is enforced where the money actually moves.

Enforcement therefore lives at RESERVE, not at SETTLE. A reservation is the
moment the company's exposure is created; settlement happens minutes later and
may never happen at all. Checking at settle would let N concurrent tasks each
pass the check, reserve their full price, and overspend together before a
single one of them settles.

Usage is never stored. It is summed from the append-only point ledger joined
back to the task that caused it. The ledger cannot be updated or deleted, so a
member's consumed total cannot drift or be quietly rewritten: the number the
check sees is the number that was actually reserved.

Two deliberate limits on scope:

* Only POINT (billing v2) tasks are counted. v1 CNY_CENT tasks use a different
  unit and a different ledger; mixing them would produce a number that means
  nothing, so those tasks are skipped rather than converted.
* A member with no row is unlimited. The feature is inert until an owner opts
  a member in, which keeps existing companies unaffected on upgrade.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    CompanyMemberSpendLimit,
    CompanyPointLedgerEntry,
    GenerationTask,
    PointLedgerKind,
    SpendLimitPeriod,
    utcnow,
)
from .errors import ConflictError, MemberSpendLimitExceededError


class UpsertMemberSpendLimitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_kind: Literal["day", "week", "month", "lifetime"] = "month"
    limit_points: int | None = Field(default=None, ge=0)

    @property
    def resolved_period(self) -> SpendLimitPeriod:
        return SpendLimitPeriod(self.period_kind)


def period_start(period: SpendLimitPeriod, *, now: datetime) -> datetime | None:
    """Return the inclusive lower bound of one window, or None for all time."""

    if period is SpendLimitPeriod.LIFETIME:
        return None
    if period is SpendLimitPeriod.DAY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period is SpendLimitPeriod.WEEK:
        # Subtract weekdays rather than mutating `day`: the first days of a
        # month can belong to the previous month's last ISO week.
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_of_day - timedelta(days=start_of_day.weekday())
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class CompanyMemberSpendLimitService:
    @staticmethod
    def reserved_points(
        session: Session,
        *,
        company_id: str,
        user_id: str,
        period: SpendLimitPeriod,
        now: datetime | None = None,
    ) -> int:
        """Sum reservations this member caused inside the window.

        RESERVE entries measure peak exposure, which is exactly what a budget
        must bound: a released (failed) task still reserved its price for as
        long as it ran, and concurrent tasks hold their reservations at the
        same time.
        """

        moment = now or utcnow()
        statement = (
            select(func.coalesce(func.sum(CompanyPointLedgerEntry.amount_points), 0))
            .join(
                GenerationTask,
                GenerationTask.id == CompanyPointLedgerEntry.task_id,
            )
            .where(
                CompanyPointLedgerEntry.company_id == company_id,
                CompanyPointLedgerEntry.kind == PointLedgerKind.RESERVE,
                GenerationTask.user_id == user_id,
            )
        )
        start = period_start(period, now=moment)
        if start is not None:
            statement = statement.where(
                CompanyPointLedgerEntry.created_at >= start
            )
        return int(session.scalar(statement) or 0)

    @classmethod
    def enforce_reserve(
        cls,
        session: Session,
        *,
        company_id: str,
        user_id: str,
        amount_points: int,
        now: datetime | None = None,
    ) -> None:
        """Reject a reservation that would break this member's ceiling.

        Callers must already hold the company point wallet lock. The lock is
        what makes the read-then-decide sequence safe: without it two
        concurrent reservations could both observe the same remaining budget
        and both pass.
        """

        if amount_points <= 0:
            return
        limit = session.scalar(
            select(CompanyMemberSpendLimit)
            .where(
                CompanyMemberSpendLimit.company_id == company_id,
                CompanyMemberSpendLimit.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if limit is None or limit.limit_points is None:
            return
        moment = now or utcnow()
        consumed = cls.reserved_points(
            session,
            company_id=company_id,
            user_id=user_id,
            period=limit.period_kind,
            now=moment,
        )
        if consumed + amount_points > int(limit.limit_points):
            raise MemberSpendLimitExceededError(
                remaining=int(limit.limit_points) - consumed
            )

    @classmethod
    def upsert(
        cls,
        session: Session,
        *,
        company_id: str,
        user_id: str,
        actor_user_id: str,
        body: UpsertMemberSpendLimitRequest,
    ) -> tuple[CompanyMemberSpendLimit, bool]:
        if body.limit_points is not None and body.limit_points < 0:
            raise ConflictError("成员配额不能为负数")
        limit = session.scalar(
            select(CompanyMemberSpendLimit)
            .where(
                CompanyMemberSpendLimit.company_id == company_id,
                CompanyMemberSpendLimit.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        created = limit is None
        if limit is None:
            limit = CompanyMemberSpendLimit(
                company_id=company_id,
                user_id=user_id,
            )
            session.add(limit)
        limit.period_kind = body.resolved_period
        limit.limit_points = body.limit_points
        limit.updated_by_user_id = actor_user_id
        session.flush()
        return limit, created

    @staticmethod
    def get(
        session: Session,
        *,
        company_id: str,
        user_id: str,
    ) -> CompanyMemberSpendLimit | None:
        return session.scalar(
            select(CompanyMemberSpendLimit).where(
                CompanyMemberSpendLimit.company_id == company_id,
                CompanyMemberSpendLimit.user_id == user_id,
            )
        )

    @staticmethod
    def list(session: Session, *, company_id: str) -> list[CompanyMemberSpendLimit]:
        return list(
            session.scalars(
                select(CompanyMemberSpendLimit)
                .where(CompanyMemberSpendLimit.company_id == company_id)
                .order_by(CompanyMemberSpendLimit.user_id)
            )
        )

    @classmethod
    def usage(
        cls,
        session: Session,
        *,
        company_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        limit = cls.get(session, company_id=company_id, user_id=user_id)
        period = limit.period_kind if limit is not None else SpendLimitPeriod.MONTH
        consumed = cls.reserved_points(
            session,
            company_id=company_id,
            user_id=user_id,
            period=period,
            now=now,
        )
        ceiling = None if limit is None else limit.limit_points
        return {
            "user_id": user_id,
            "period_kind": period.value,
            "limit_points": ceiling,
            "reserved_points": consumed,
            "remaining_points": (
                None if ceiling is None else int(ceiling) - consumed
            ),
        }

    @staticmethod
    def payload(limit: CompanyMemberSpendLimit) -> dict[str, Any]:
        return {
            "user_id": limit.user_id,
            "period_kind": limit.period_kind.value,
            "limit_points": limit.limit_points,
            "updated_by_user_id": limit.updated_by_user_id,
            "updated_at": limit.updated_at.isoformat(),
        }
