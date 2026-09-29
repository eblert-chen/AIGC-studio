"""Point-lot expiry settlement for company and personal wallets.

Mature credit-system rule (aligned with 可灵 / Runway practice): only
*unreserved available* points in a due lot expire. A reservation is a
commitment to an in-flight task and is never cancelled by expiry; when that
task later releases its reservation, the points return to the (already
expired) lot's available balance and the next sweep removes them.

Every expiry writes an append-only ledger row (kind=EXPIRY) so the reduction
is auditable and the wallet projection stays reconcileable. Sweeps are
idempotent: a lot whose available_points is already zero is skipped, and the
per-lot ledger idempotency key (point-expiry:<lot id>) is unique.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CompanyPointLedgerEntry,
    CompanyPointLot,
    CompanyPointWalletAccount,
    LedgerKind,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalWalletAccount,
    PointLedgerKind,
    utcnow,
)
from .errors import ConflictError


@dataclass(frozen=True, slots=True)
class ExpiredLot:
    lot_id: str
    expired_points: int


class CompanyPointExpiryService:
    """Sweep due company lots (expires_at <= now, available_points > 0).

    Lock order is wallet -> lot, matching the enterprise reserve order
    (company -> wallet -> lots) so expiry never deadlocks against a
    concurrent reservation.
    """

    @classmethod
    def run_once(
        cls,
        session: Session,
        *,
        company_id: str | None = None,
        limit: int = 200,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or utcnow()
        lot_ids = list(
            session.scalars(
                select(CompanyPointLot.id)
                .where(
                    CompanyPointLot.expires_at.is_not(None),
                    CompanyPointLot.expires_at <= now,
                    CompanyPointLot.available_points > 0,
                    CompanyPointLot.company_id == company_id
                    if company_id is not None
                    else True,
                )
                .order_by(CompanyPointLot.expires_at, CompanyPointLot.id)
                .limit(limit)
            ).all()
        )
        expired: list[ExpiredLot] = []
        failed: list[dict[str, str]] = []
        for lot_id in lot_ids:
            try:
                with session.begin_nested():
                    outcome = cls._expire_one(session, lot_id=lot_id, now=now)
            except (ConflictError, ValueError) as error:
                failed.append({"lot_id": lot_id, "reason": str(error)})
                continue
            if outcome is not None:
                expired.append(outcome)
        return {
            "processed": len(lot_ids) > 0,
            "scanned": len(lot_ids),
            "expired_lots": [
                {"lot_id": item.lot_id, "expired_points": item.expired_points}
                for item in expired
            ],
            "failed": failed,
        }

    @classmethod
    def _expire_one(
        cls,
        session: Session,
        *,
        lot_id: str,
        now: datetime,
    ) -> ExpiredLot | None:
        company_id = session.scalar(
            select(CompanyPointLot.company_id).where(CompanyPointLot.id == lot_id)
        )
        if company_id is None:
            raise ConflictError("积分批次不存在")
        wallet = session.scalar(
            select(CompanyPointWalletAccount)
            .where(CompanyPointWalletAccount.company_id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if wallet is None:
            raise ConflictError("企业积分钱包不存在")
        lot = session.scalar(
            select(CompanyPointLot)
            .where(CompanyPointLot.id == lot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lot is None:
            raise ConflictError("积分批次不存在")
        expires_at = lot.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is None or expires_at > now or lot.available_points <= 0:
            return None
        expired_points = lot.available_points
        lot.available_points = 0
        lot.expired_points += expired_points
        wallet.available_points -= expired_points
        if wallet.available_points < 0:
            raise ConflictError("企业积分钱包余额不足以结算到期积分")
        session.add(
            CompanyPointLedgerEntry(
                company_id=lot.company_id,
                kind=PointLedgerKind.EXPIRY,
                amount_points=expired_points,
                available_delta_points=-expired_points,
                reserved_delta_points=0,
                reversal_reserved_delta_points=0,
                debt_delta_points=0,
                idempotency_key=f"point-expiry:{lot.id}",
                task_id=None,
                note=f"积分到期结算 {expires_at.isoformat()}",
            )
        )
        session.flush()
        return ExpiredLot(lot_id=lot.id, expired_points=expired_points)


class PersonalPointExpiryService:
    """Sweep due personal lots with the same semantics as the company sweep."""

    @classmethod
    def run_once(
        cls,
        session: Session,
        *,
        workspace_id: str | None = None,
        limit: int = 200,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or utcnow()
        lot_ids = list(
            session.scalars(
                select(PersonalPointLot.id)
                .where(
                    PersonalPointLot.expires_at.is_not(None),
                    PersonalPointLot.expires_at <= now,
                    PersonalPointLot.available_points > 0,
                    PersonalPointLot.workspace_id == workspace_id
                    if workspace_id is not None
                    else True,
                )
                .order_by(PersonalPointLot.expires_at, PersonalPointLot.id)
                .limit(limit)
            ).all()
        )
        expired: list[ExpiredLot] = []
        failed: list[dict[str, str]] = []
        for lot_id in lot_ids:
            try:
                with session.begin_nested():
                    outcome = cls._expire_one(session, lot_id=lot_id, now=now)
            except (ConflictError, ValueError) as error:
                failed.append({"lot_id": lot_id, "reason": str(error)})
                continue
            if outcome is not None:
                expired.append(outcome)
        return {
            "processed": len(lot_ids) > 0,
            "scanned": len(lot_ids),
            "expired_lots": [
                {"lot_id": item.lot_id, "expired_points": item.expired_points}
                for item in expired
            ],
            "failed": failed,
        }

    @classmethod
    def _expire_one(
        cls,
        session: Session,
        *,
        lot_id: str,
        now: datetime,
    ) -> ExpiredLot | None:
        workspace_id = session.scalar(
            select(PersonalPointLot.workspace_id).where(PersonalPointLot.id == lot_id)
        )
        if workspace_id is None:
            raise ConflictError("积分批次不存在")
        account = session.scalar(
            select(PersonalWalletAccount)
            .where(PersonalWalletAccount.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise ConflictError("个人积分钱包不存在")
        lot = session.scalar(
            select(PersonalPointLot)
            .where(PersonalPointLot.id == lot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lot is None:
            raise ConflictError("积分批次不存在")
        expires_at = lot.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is None or expires_at > now or lot.available_points <= 0:
            return None
        expired_points = lot.available_points
        lot.available_points = 0
        lot.expired_points += expired_points
        account.available_points -= expired_points
        if account.available_points < 0:
            raise ConflictError("个人积分钱包余额不足以结算到期积分")
        session.add(
            PersonalLedgerEntry(
                workspace_id=lot.workspace_id,
                kind=LedgerKind.EXPIRY,
                amount_points=expired_points,
                available_delta_points=-expired_points,
                reserved_delta_points=0,
                reversal_reserved_delta_points=0,
                debt_delta_points=0,
                idempotency_key=f"point-expiry:{lot.id}",
                task_id=None,
                note=f"积分到期结算 {expires_at.isoformat()}",
            )
        )
        session.flush()
        return ExpiredLot(lot_id=lot.id, expired_points=expired_points)


class PointExpiryService:
    """Facade run by the billing worker: sweeps company and personal lots."""

    @classmethod
    def run_once(
        cls,
        session: Session,
        *,
        limit: int = 200,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        company = CompanyPointExpiryService.run_once(session, limit=limit, now=now)
        personal = PersonalPointExpiryService.run_once(session, limit=limit, now=now)
        return {
            "processed": company["processed"] or personal["processed"],
            "scanned": company["scanned"] + personal["scanned"],
            "expired_lots": company["expired_lots"] + personal["expired_lots"],
            "failed": company["failed"] + personal["failed"],
        }
