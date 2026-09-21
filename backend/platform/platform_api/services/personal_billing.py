from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    GenerationTask,
    LedgerKind,
    PersonalLedgerEntry,
    PersonalPointLot,
    PersonalTaskPointLotAllocation,
    PersonalWalletAccount,
    PointLotSettlementValueAllocation,
    PointLotSourceKind,
    TaskStatus,
    utcnow,
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
        # A lock must replace stale pre-lock ORM state; flush first because
        # production deliberately uses autoflush=False.
        session.flush()
        account = session.scalar(
            select(PersonalWalletAccount)
            .where(PersonalWalletAccount.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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

    @staticmethod
    def _value_slice(
        lot: PersonalPointLot,
        *,
        settled_before: int,
        settled_points: int,
    ) -> tuple[int, int, int]:
        """Allocate exact lot value without inventing point-derived revenue."""

        end = settled_before + settled_points
        cash = (
            lot.cash_basis_cents * end // lot.original_points
            - lot.cash_basis_cents * settled_before // lot.original_points
        )
        receivable = (
            lot.receivable_basis_cents * end // lot.original_points
            - lot.receivable_basis_cents * settled_before // lot.original_points
        )
        subsidy = settled_points * 10 - cash - receivable
        if subsidy < 0:
            raise ConflictError("个人积分批次价值分摊无效")
        return cash, receivable, subsidy

    @classmethod
    def _ensure_lot_projection(
        cls,
        session: Session,
        *,
        account: PersonalWalletAccount,
    ) -> None:
        """Classify pre-v2 personal balances as non-refundable legacy points.

        Alembic performs the same conversion for deployed databases.  This
        guarded path keeps create_all-based tests and old development databases
        safe without ever assigning an unverifiable cash basis.
        """

        existing_lot = session.scalar(
            select(PersonalPointLot.id)
            .where(PersonalPointLot.workspace_id == account.workspace_id)
            .limit(1)
        )
        if existing_lot is not None:
            return
        settled_points = int(
            session.scalar(
                select(func.coalesce(func.sum(PersonalLedgerEntry.amount_points), 0)).where(
                    PersonalLedgerEntry.workspace_id == account.workspace_id,
                    PersonalLedgerEntry.kind == LedgerKind.SETTLE,
                )
            )
            or 0
        )
        original_points = account.available_points + account.reserved_points + settled_points
        if original_points == 0:
            return
        recharge_points = int(
            session.scalar(
                select(func.coalesce(func.sum(PersonalLedgerEntry.amount_points), 0)).where(
                    PersonalLedgerEntry.workspace_id == account.workspace_id,
                    PersonalLedgerEntry.kind == LedgerKind.RECHARGE,
                )
            )
            or 0
        )
        if recharge_points not in {0, original_points}:
            raise ConflictError("个人钱包与历史积分账本无法形成批次投影")
        lot = PersonalPointLot(
            workspace_id=account.workspace_id,
            source_kind=PointLotSourceKind.LEGACY,
            original_points=original_points,
            available_points=account.available_points,
            reserved_points=account.reserved_points,
            reversal_reserved_points=0,
            settled_points=settled_points,
            reversed_points=0,
            cash_basis_cents=0,
            receivable_basis_cents=0,
            subsidy_cents=original_points * 10,
            refundable=False,
            idempotency_key="personal-legacy-projection-v1",
        )
        session.add(lot)
        session.flush()
        active_tasks = list(
            session.scalars(
                select(GenerationTask)
                .where(
                    GenerationTask.personal_workspace_id == account.workspace_id,
                    GenerationTask.reserved_points > 0,
                    GenerationTask.status.in_({TaskStatus.QUEUED, TaskStatus.PROCESSING}),
                )
                .order_by(GenerationTask.created_at, GenerationTask.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        if sum(task.reserved_points for task in active_tasks) != account.reserved_points:
            raise ConflictError("个人钱包预占与活动任务无法形成批次投影")
        for task in active_tasks:
            session.add(
                PersonalTaskPointLotAllocation(
                    workspace_id=account.workspace_id,
                    task_id=task.id,
                    lot_id=lot.id,
                    allocated_points=task.reserved_points,
                    reserved_points=task.reserved_points,
                    settled_points=0,
                    released_points=0,
                )
            )
        session.flush()

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
        cls._ensure_lot_projection(session, account=account)
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
            lot = session.scalar(
                select(PersonalPointLot).where(
                    PersonalPointLot.workspace_id == workspace_id,
                    PersonalPointLot.idempotency_key == idempotency_key,
                )
            )
            if (
                lot is None
                or lot.source_kind != PointLotSourceKind.PROMOTIONAL
                or lot.original_points != amount_points
                or lot.cash_basis_cents != 0
                or lot.subsidy_cents != amount_points * 10
            ):
                raise ConflictError("个人积分入账幂等键对应的批次意图不一致")
            return account, existing, False
        if account.available_points > MAX_POINTS - amount_points:
            raise ConflictError("入账后个人积分超出系统上限")
        account.available_points += amount_points
        session.add(
            PersonalPointLot(
                workspace_id=workspace_id,
                source_kind=PointLotSourceKind.PROMOTIONAL,
                original_points=amount_points,
                available_points=amount_points,
                reserved_points=0,
                reversal_reserved_points=0,
                settled_points=0,
                reversed_points=0,
                cash_basis_cents=0,
                receivable_basis_cents=0,
                subsidy_cents=amount_points * 10,
                refundable=False,
                idempotency_key=idempotency_key,
            )
        )
        entry = PersonalLedgerEntry(
            workspace_id=workspace_id,
            kind=LedgerKind.RECHARGE,
            amount_points=amount_points,
            available_delta_points=amount_points,
            reserved_delta_points=0,
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
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
        cls._ensure_lot_projection(session, account=account)
        if account.debt_points > 0:
            raise ConflictError("个人钱包存在拒付债务，暂不能创建新任务")
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
            .execution_options(populate_existing=True)
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

        now = utcnow()
        lots = list(
            session.scalars(
                select(PersonalPointLot)
                .where(
                    PersonalPointLot.workspace_id == workspace_id,
                    PersonalPointLot.available_points > 0,
                    or_(
                        PersonalPointLot.expires_at.is_(None),
                        PersonalPointLot.expires_at > now,
                    ),
                )
                .order_by(
                    PersonalPointLot.expires_at.is_(None),
                    PersonalPointLot.expires_at,
                    PersonalPointLot.created_at,
                    PersonalPointLot.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        remaining = amount_points
        for lot in lots:
            allocated = min(lot.available_points, remaining)
            if allocated <= 0:
                continue
            lot.available_points -= allocated
            lot.reserved_points += allocated
            session.add(
                PersonalTaskPointLotAllocation(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    lot_id=lot.id,
                    allocated_points=allocated,
                    reserved_points=allocated,
                    settled_points=0,
                    released_points=0,
                )
            )
            remaining -= allocated
            if remaining == 0:
                break
        if remaining:
            raise ConflictError("个人积分钱包与积分批次余额不一致")

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
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
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
        allow_terminal: bool = False,
    ) -> tuple[PersonalWalletAccount, PersonalLedgerEntry]:
        """Settle a reserved task.

        `allow_terminal` exists only for reservation recovery. A task can reach
        a terminal status without its reservation ever being settled (status
        write and ledger write are two separate steps). Every ordinary path
        refuses to touch an already-terminal task, so without this switch such
        a reservation would stay held forever and never reach revenue.
        """
        if actual_cost_points < 0 or actual_cost_points > MAX_POINTS:
            raise ConflictError("实际积分成本不能小于 0")
        account = cls._locked_account(session, workspace_id)
        cls._ensure_lot_projection(session, account=account)
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
            .execution_options(populate_existing=True)
        )
        if task is None:
            raise NotFoundError("个人空间下不存在该任务")
        if existing is not None:
            return account, existing
        if allow_terminal:
            if task.status != TaskStatus.SUCCEEDED:
                raise ConflictError("补偿结算只支持已经成功的任务")
        elif task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("个人任务当前状态不能成功结算")
        reserved = task.reserved_points
        if reserved <= 0 or actual_cost_points > reserved:
            raise ConflictError("实际积分成本必须在已预占积分范围内")

        allocation_rows = session.execute(
            select(PersonalTaskPointLotAllocation, PersonalPointLot)
            .join(PersonalPointLot, PersonalPointLot.id == PersonalTaskPointLotAllocation.lot_id)
            .where(
                PersonalTaskPointLotAllocation.workspace_id == workspace_id,
                PersonalTaskPointLotAllocation.task_id == task_id,
                PersonalTaskPointLotAllocation.reserved_points > 0,
            )
            .order_by(
                PersonalTaskPointLotAllocation.created_at,
                PersonalTaskPointLotAllocation.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if sum(allocation.reserved_points for allocation, _ in allocation_rows) != reserved:
            raise ConflictError("个人任务预占与积分批次分配不一致")
        remaining_to_settle = actual_cost_points
        value_rows: list[tuple[PersonalTaskPointLotAllocation, int, int, int, int]] = []
        for allocation, lot in allocation_rows:
            settled = min(allocation.reserved_points, remaining_to_settle)
            released = allocation.reserved_points - settled
            settled_before = lot.settled_points
            allocation.reserved_points = 0
            allocation.settled_points += settled
            allocation.released_points += released
            lot.reserved_points -= settled + released
            lot.settled_points += settled
            lot.available_points += released
            remaining_to_settle -= settled
            if settled:
                cash, receivable, subsidy = cls._value_slice(
                    lot,
                    settled_before=settled_before,
                    settled_points=settled,
                )
                value_rows.append((allocation, settled, cash, receivable, subsidy))
        if remaining_to_settle:
            raise ConflictError("个人任务积分批次不足以完成结算")

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
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        session.add(entry)
        session.flush()
        for allocation, settled, cash, receivable, subsidy in value_rows:
            session.add(
                PointLotSettlementValueAllocation(
                    company_id=None,
                    personal_workspace_id=workspace_id,
                    task_id=task_id,
                    company_task_allocation_id=None,
                    personal_task_allocation_id=allocation.id,
                    company_settle_ledger_id=None,
                    personal_settle_ledger_id=entry.id,
                    settled_points=settled,
                    cash_basis_cents=cash,
                    receivable_basis_cents=receivable,
                    subsidy_cents=subsidy,
                )
            )
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
        allow_terminal: bool = False,
    ) -> tuple[PersonalWalletAccount, PersonalLedgerEntry]:
        if terminal_status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ConflictError("积分释放只支持失败或取消终态")
        account = cls._locked_account(session, workspace_id)
        cls._ensure_lot_projection(session, account=account)
        task = session.scalar(
            select(GenerationTask)
            .where(
                GenerationTask.id == task_id,
                GenerationTask.personal_workspace_id == workspace_id,
                GenerationTask.company_id.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
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
        if allow_terminal:
            if task.status != terminal_status:
                raise ConflictError("补偿释放的任务终态与预期不一致")
        elif task.status not in {TaskStatus.QUEUED, TaskStatus.PROCESSING}:
            raise ConflictError("个人任务当前状态不能释放积分")
        if release_amount <= 0:
            raise ConflictError("个人任务没有可释放的预占积分")

        allocation_rows = session.execute(
            select(PersonalTaskPointLotAllocation, PersonalPointLot)
            .join(PersonalPointLot, PersonalPointLot.id == PersonalTaskPointLotAllocation.lot_id)
            .where(
                PersonalTaskPointLotAllocation.workspace_id == workspace_id,
                PersonalTaskPointLotAllocation.task_id == task_id,
                PersonalTaskPointLotAllocation.reserved_points > 0,
            )
            .order_by(
                PersonalTaskPointLotAllocation.created_at,
                PersonalTaskPointLotAllocation.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if sum(allocation.reserved_points for allocation, _ in allocation_rows) != release_amount:
            raise ConflictError("个人任务预占与积分批次分配不一致")
        for allocation, lot in allocation_rows:
            released = allocation.reserved_points
            allocation.reserved_points = 0
            allocation.released_points += released
            lot.reserved_points -= released
            lot.available_points += released

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
            reversal_reserved_delta_points=0,
            debt_delta_points=0,
            idempotency_key=idempotency_key,
            task_id=task_id,
            note=failure_reason[:240],
        )
        session.add(entry)
        session.flush()
        return account, entry
