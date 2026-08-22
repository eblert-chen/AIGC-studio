from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    GenerationTask,
    LedgerKind,
    PersonalLedgerEntry,
    PersonalWalletAccount,
    TaskStatus,
)
from .errors import ConflictError, DomainError, NotFoundError


MAX_POINTS = 9_000_000_000_000_000


class InsufficientPersonalPointsError(DomainError):
    def __init__(self) -> None:
        super().__init__("个人可用积分不足", "insufficient_personal_points", 409)


class PersonalWalletService:
    """Reserve and settle individual points without touching company money."""

    @staticmethod
    def _locked_account(
        session: Session, workspace_id: str
    ) -> PersonalWalletAccount:
        account = session.scalar(
            select(PersonalWalletAccount)
            .where(PersonalWalletAccount.workspace_id == workspace_id)
            .with_for_update()
        )
        if account is None:
            raise NotFoundError("个人积分账户不存在")
        return account

    @staticmethod
    def _existing(
        session: Session,
        *,
        workspace_id: str,
        idempotency_key: str,
        expected_kind: LedgerKind,
        expected_amount: int | None,
        task_id: str | None,
        expected_note: str | None = None,
    ) -> PersonalLedgerEntry | None:
        entry = session.scalar(
            select(PersonalLedgerEntry).where(
                PersonalLedgerEntry.workspace_id == workspace_id,
                PersonalLedgerEntry.idempotency_key == idempotency_key,
            )
        )
        if entry and (
            entry.kind != expected_kind
            or (expected_amount is not None and entry.amount_points != expected_amount)
            or entry.task_id != task_id
            or (expected_note is not None and entry.note != expected_note)
        ):
            raise ConflictError("幂等键已被另一笔不同的个人积分操作使用")
        return entry

    @classmethod
    def credit(
        cls,
        session: Session,
        *,
        workspace_id: str,
        amount_points: int,
        idempotency_key: str,
        note: str,
    ) -> tuple[PersonalWalletAccount, PersonalLedgerEntry, bool]:
        """Provision purchased/promotional points from a trusted server flow.

        The public personal API intentionally exposes no fake payment endpoint.
        Payment or audited operations integrations call the internal route that
        wraps this idempotent ledger operation.
        """
        if amount_points <= 0 or amount_points > MAX_POINTS:
            raise ConflictError("入账积分必须大于 0")
        if len(note) > 240:
            raise ConflictError("积分入账备注过长")
        account = cls._locked_account(session, workspace_id)
        existing = cls._existing(
            session,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RECHARGE,
            expected_amount=amount_points,
            task_id=None,
            expected_note=note,
        )
        if existing is not None:
            return account, existing, False
        if account.available_points > MAX_POINTS - amount_points:
            raise ConflictError("入账后个人积分超出系统上限")
        account.available_points += amount_points
        entry = PersonalLedgerEntry(
            workspace_id=workspace_id,
            kind=LedgerKind.RECHARGE,
            amount_points=amount_points,
            available_delta_points=amount_points,
            reserved_delta_points=0,
            idempotency_key=idempotency_key,
            task_id=None,
            note=note,
        )
        session.add(entry)
        session.flush()
        return account, entry, True

    @classmethod
    def reserve(
        cls,
        session: Session,
        *,
        workspace_id: str,
        task_id: str,
        amount_points: int,
        idempotency_key: str,
    ) -> tuple[PersonalWalletAccount, PersonalLedgerEntry]:
        if amount_points <= 0 or amount_points > MAX_POINTS:
            raise ConflictError("预占积分必须大于 0")
        account = cls._locked_account(session, workspace_id)
        existing = cls._existing(
            session,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RESERVE,
            expected_amount=amount_points,
            task_id=task_id,
        )
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.company_id.is_(None),
            )
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("个人空间下不存在该任务")
        if existing is not None:
            return account, existing
        if task.status != TaskStatus.DRAFT or task.reserved_points != 0:
            raise ConflictError("只有未预占的个人草稿任务可以预占积分")
        if task.quote_points != amount_points:
            raise ConflictError("预占积分必须等于任务报价")
        if account.available_points < amount_points:
            raise InsufficientPersonalPointsError()

        account.available_points -= amount_points
        account.reserved_points += amount_points
        task.reserved_points = amount_points
        task.status = TaskStatus.QUEUED
        entry = PersonalLedgerEntry(
            workspace_id=workspace_id,
            kind=LedgerKind.RESERVE,
            amount_points=amount_points,
            available_delta_points=-amount_points,
            reserved_delta_points=amount_points,
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
        workspace_id: str,
        task_id: str,
        actual_cost_points: int,
        idempotency_key: str,
    ) -> tuple[PersonalWalletAccount, PersonalLedgerEntry]:
        if actual_cost_points < 0 or actual_cost_points > MAX_POINTS:
            raise ConflictError("实际积分成本不能小于 0")
        account = cls._locked_account(session, workspace_id)
        existing = cls._existing(
            session,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.SETTLE,
            expected_amount=actual_cost_points,
            task_id=task_id,
        )
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.company_id.is_(None),
            )
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("个人空间下不存在该任务")
        if existing is not None:
            return account, existing
        if task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("个人任务当前状态不能成功结算")
        reserved = task.reserved_points
        if reserved <= 0 or actual_cost_points > reserved:
            raise ConflictError("实际积分成本必须在已预占积分范围内")

        refund = reserved - actual_cost_points
        account.available_points += refund
        account.reserved_points -= reserved
        task.reserved_points = 0
        task.actual_cost_points = actual_cost_points
        task.status = TaskStatus.SUCCEEDED
        entry = PersonalLedgerEntry(
            workspace_id=workspace_id,
            kind=LedgerKind.SETTLE,
            amount_points=actual_cost_points,
            available_delta_points=refund,
            reserved_delta_points=-reserved,
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
        workspace_id: str,
        task_id: str,
        idempotency_key: str,
        failure_reason: str,
        terminal_status: TaskStatus = TaskStatus.FAILED,
    ) -> tuple[PersonalWalletAccount, PersonalLedgerEntry]:
        if terminal_status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ConflictError("积分释放只支持失败或取消终态")
        account = cls._locked_account(session, workspace_id)
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.company_id.is_(None),
            )
            .with_for_update()
        )
        if task is None:
            raise NotFoundError("个人空间下不存在该任务")
        release_amount = task.reserved_points
        existing = cls._existing(
            session,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            expected_kind=LedgerKind.RELEASE,
            expected_amount=None,
            task_id=task_id,
        )
        if existing is not None:
            return account, existing
        if task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("个人任务当前状态不能释放积分")
        if release_amount <= 0:
            raise ConflictError("个人任务没有可释放的预占积分")

        account.available_points += release_amount
        account.reserved_points -= release_amount
        task.reserved_points = 0
        task.status = terminal_status
        task.failure_reason = failure_reason
        entry = PersonalLedgerEntry(
            workspace_id=workspace_id,
            kind=LedgerKind.RELEASE,
            amount_points=release_amount,
            available_delta_points=release_amount,
            reserved_delta_points=-release_amount,
            idempotency_key=idempotency_key,
            task_id=task_id,
            note=failure_reason[:240],
        )
        session.add(entry)
        session.flush()
        return account, entry
