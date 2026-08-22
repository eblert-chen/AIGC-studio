from __future__ import annotations

from datetime import datetime

from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..models import (
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    TaskStatus,
    WalletAccount,
)
from .errors import ConflictError, InsufficientBalanceError, NotFoundError


MAX_MONEY_CENTS = 9_000_000_000_000_000


class WalletService:
    @staticmethod
    def _locked_account(session: Session, company_id: str) -> WalletAccount:
        account = session.scalar(
            select(WalletAccount)
            .where(WalletAccount.company_id == company_id)
            .with_for_update()
        )
        if account is None:
            raise NotFoundError("公司钱包不存在")
        return account

    @staticmethod
    def _existing(
        session: Session,
        *,
        company_id: str,
        idempotency_key: str,
        expected_kind: LedgerKind,
        expected_amount: int | None,
        task_id: str | None,
        expected_note: str | None = None,
    ) -> LedgerEntry | None:
        entry = session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.company_id == company_id,
                LedgerEntry.idempotency_key == idempotency_key,
            )
        )
        if entry and (
            entry.kind != expected_kind
            or (
                expected_amount is not None
                and entry.amount_cents != expected_amount
            )
            or entry.task_id != task_id
            or (expected_note is not None and entry.note != expected_note)
        ):
            raise ConflictError("幂等键已被另一笔不同的账务操作使用")
        return entry

    @classmethod
    def recharge(
        cls,
        session: Session,
        *,
        company_id: str,
        amount_cents: int,
        idempotency_key: str,
        note: str = "",
    ) -> tuple[WalletAccount, LedgerEntry, bool]:
        if amount_cents <= 0 or amount_cents > MAX_MONEY_CENTS:
            raise ConflictError("充值金额必须大于 0 分")
        account = cls._locked_account(session, company_id)
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RECHARGE,
            expected_amount=amount_cents,
            task_id=None,
            expected_note=note,
        )
        if existing:
            return account, existing, False
        if account.available_cents > MAX_MONEY_CENTS - amount_cents:
            raise ConflictError("充值后公司余额超出系统上限")
        account.available_cents += amount_cents
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.RECHARGE,
            amount_cents=amount_cents,
            available_delta_cents=amount_cents,
            reserved_delta_cents=0,
            idempotency_key=idempotency_key,
            note=note,
        )
        session.add(entry)
        session.flush()
        return account, entry, True

    @staticmethod
    def recharge_page(
        session: Session,
        *,
        company_id: str,
        page: int,
        page_size: int,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        statement = select(LedgerEntry).where(
            LedgerEntry.company_id == company_id,
            LedgerEntry.kind == LedgerKind.RECHARGE,
        )
        if start_time is not None:
            statement = statement.where(LedgerEntry.created_at >= start_time)
        if end_time is not None:
            statement = statement.where(LedgerEntry.created_at < end_time)
        filtered = statement.subquery("filtered_recharges")
        summary = (
            select(
                func.count().label("recharge_total"),
                func.coalesce(func.sum(filtered.c.amount_cents), 0).label(
                    "recharge_total_amount_cents"
                ),
            )
            .select_from(filtered)
            .cte("recharge_summary")
        )
        page_rows = (
            select(filtered)
            .order_by(
                filtered.c.created_at.desc(),
                filtered.c.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .cte("recharge_page")
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
        total = int(result_rows[0]["recharge_total"] or 0)
        total_amount_cents = int(
            result_rows[0]["recharge_total_amount_cents"] or 0
        )
        items = [
            {key: row[key] for key in page_rows.c.keys()}
            for row in result_rows
            if row["id"] is not None
        ]
        return total, total_amount_cents, items

    @classmethod
    def reserve(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> tuple[WalletAccount, LedgerEntry]:
        if amount_cents <= 0 or amount_cents > MAX_MONEY_CENTS:
            raise ConflictError("预占金额必须大于 0 分")
        account = cls._locked_account(session, company_id)
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RESERVE,
            expected_amount=amount_cents,
            task_id=task_id,
        )
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("当前公司下不存在该任务")
        if existing:
            return account, existing
        if task.status != TaskStatus.DRAFT or task.reserved_cents != 0:
            raise ConflictError("只有未预占的草稿任务可以预占额度")
        if task.quote_cents != amount_cents:
            raise ConflictError("预占金额必须等于任务报价")
        if account.available_cents < amount_cents:
            raise InsufficientBalanceError()

        account.available_cents -= amount_cents
        account.reserved_cents += amount_cents
        task.reserved_cents = amount_cents
        task.status = TaskStatus.QUEUED
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.RESERVE,
            amount_cents=amount_cents,
            available_delta_cents=-amount_cents,
            reserved_delta_cents=amount_cents,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        return account, entry

    @classmethod
    def settle_success(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        actual_cost_cents: int,
        idempotency_key: str,
    ) -> tuple[WalletAccount, LedgerEntry]:
        if actual_cost_cents < 0 or actual_cost_cents > MAX_MONEY_CENTS:
            raise ConflictError("实际成本不能小于 0 分")
        account = cls._locked_account(session, company_id)
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.SETTLE,
            expected_amount=actual_cost_cents,
            task_id=task_id,
        )
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("当前公司下不存在该任务")
        if existing:
            return account, existing
        if task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("任务当前状态不能成功结算")
        reserved = task.reserved_cents
        if reserved <= 0 or actual_cost_cents > reserved:
            raise ConflictError("实际成本必须在已预占额度范围内")

        refund = reserved - actual_cost_cents
        account.available_cents += refund
        account.reserved_cents -= reserved
        task.reserved_cents = 0
        task.actual_cost_cents = actual_cost_cents
        task.status = TaskStatus.SUCCEEDED
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.SETTLE,
            amount_cents=actual_cost_cents,
            available_delta_cents=refund,
            reserved_delta_cents=-reserved,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        return account, entry

    @classmethod
    def release_failure(
        cls,
        session: Session,
        *,
        company_id: str,
        task_id: str,
        idempotency_key: str,
        failure_reason: str,
        terminal_status: TaskStatus = TaskStatus.FAILED,
    ) -> tuple[WalletAccount, LedgerEntry]:
        if terminal_status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ConflictError("额度释放只支持失败或取消终态")
        account = cls._locked_account(session, company_id)
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.company_id == company_id,
            )
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("当前公司下不存在该任务")
        release_amount = task.reserved_cents
        existing = cls._existing(
            session,
            company_id=company_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RELEASE,
            expected_amount=None,
            task_id=task_id,
        )
        if existing:
            return account, existing
        if task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("任务当前状态不能释放额度")
        if release_amount <= 0:
            raise ConflictError("任务没有可释放的预占额度")

        account.available_cents += release_amount
        account.reserved_cents -= release_amount
        task.reserved_cents = 0
        task.status = terminal_status
        task.failure_reason = failure_reason
        entry = LedgerEntry(
            company_id=company_id,
            kind=LedgerKind.RELEASE,
            amount_cents=release_amount,
            available_delta_cents=release_amount,
            reserved_delta_cents=-release_amount,
            idempotency_key=idempotency_key,
            task_id=task_id,
            note=failure_reason[:240],
        )
        session.add(entry)
        session.flush()
        return account, entry
