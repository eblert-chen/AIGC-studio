"""Read immutable point consumption value; never infer revenue from face value."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    CompanyPointLedgerEntry, CompanyPointLot, GenerationTask, LedgerKind,
    PersonalLedgerEntry, PersonalPointLot,
    PersonalTaskPointLotAllocation, PointLedgerKind,
    PointLotSettlementValueAllocation, TaskPointLotAllocation, TaskStatus,
)


@dataclass(frozen=True)
class PointIncome:
    settlement_count: int
    settled_points: int = 0
    cash_basis_cents: int = 0
    receivable_basis_cents: int = 0
    subsidy_cents: int = 0
    complete: bool = False
    settled_at: datetime | None = None

    @property
    def revenue_cents(self) -> int:
        return self.cash_basis_cents + self.receivable_basis_cents


def load_point_income(
    session: Session, tasks: Iterable[GenerationTask]
) -> dict[str, PointIncome]:
    """Require a complete task → settle ledger → lot allocation → value chain.

    Invalid/partial chains contribute no money, including apparently valid
    fragments of an incomplete settlement. Promotional value is explicit
    subsidy, not sales revenue. Cash basis is historic consumed prepaid value,
    not cash collected in the reporting period; receivable basis is separate.
    """
    tasks = list(tasks)
    ids = [task.id for task in tasks]
    if not ids:
        return {}
    company_ledgers = defaultdict(list)
    personal_ledgers = defaultdict(list)
    for model, grouped, kind in (
        (CompanyPointLedgerEntry, company_ledgers, PointLedgerKind.SETTLE),
        (PersonalLedgerEntry, personal_ledgers, LedgerKind.SETTLE),
    ):
        for ledger in session.scalars(select(model).where(
            model.task_id.in_(ids), model.kind == kind
        )):
            grouped[ledger.task_id].append(ledger)
    allocations = defaultdict(list)
    lots = {}
    for model in (TaskPointLotAllocation, PersonalTaskPointLotAllocation):
        for allocation in session.scalars(select(model).where(model.task_id.in_(ids))):
            allocations[allocation.task_id].append(allocation)
    for model, allocation_type in ((CompanyPointLot, TaskPointLotAllocation),
                                   (PersonalPointLot, PersonalTaskPointLotAllocation)):
        lot_ids = {a.lot_id for rows in allocations.values() for a in rows
                   if isinstance(a, allocation_type)}
        if lot_ids:
            for lot in session.scalars(select(model).where(model.id.in_(lot_ids))):
                lots[(allocation_type, lot.id)] = lot
    invalid_lots = set()
    # Validate each whole source lot, not merely the tasks in this report
    # window: a second task outside the period must not double-claim capacity.
    for allocation_type, lot_type, value_fk in (
        (TaskPointLotAllocation, CompanyPointLot, PointLotSettlementValueAllocation.company_task_allocation_id),
        (PersonalTaskPointLotAllocation, PersonalPointLot, PointLotSettlementValueAllocation.personal_task_allocation_id),
    ):
        lot_ids = [lot_id for (kind, lot_id) in lots if kind is allocation_type]
        if not lot_ids:
            continue
        all_allocations = list(session.scalars(select(allocation_type).where(
            allocation_type.lot_id.in_(lot_ids), allocation_type.settled_points > 0,
        )))
        all_values = defaultdict(list)
        if all_allocations:
            for value in session.scalars(select(PointLotSettlementValueAllocation).where(
                value_fk.in_([a.id for a in all_allocations])
            )):
                all_values[getattr(value, value_fk.key)].append(value)
        by_lot = defaultdict(list)
        for allocation in all_allocations:
            by_lot[allocation.lot_id].append(allocation)
        for lot_id in lot_ids:
            lot = lots[(allocation_type, lot_id)]
            group = by_lot[lot_id]
            chain = [value for allocation in group for value in all_values[allocation.id]]
            expected_cash = lot.cash_basis_cents * lot.settled_points // lot.original_points
            expected_receivable = lot.receivable_basis_cents * lot.settled_points // lot.original_points
            expected_subsidy = lot.settled_points * 10 - expected_cash - expected_receivable
            if (any(len(all_values[a.id]) != 1 for a in group)
                    or sum(a.settled_points for a in group) != lot.settled_points
                    or sum(v.settled_points for v in chain) != lot.settled_points
                    or sum(v.cash_basis_cents for v in chain) != expected_cash
                    or sum(v.receivable_basis_cents for v in chain) != expected_receivable
                    or sum(v.subsidy_cents for v in chain) != expected_subsidy):
                invalid_lots.add((allocation_type, lot_id))
    values = defaultdict(list)
    for value in session.scalars(select(PointLotSettlementValueAllocation).where(
        PointLotSettlementValueAllocation.task_id.in_(ids)
    )):
        values[value.task_id].append(value)
    result = {}
    for task in tasks:
        is_company = task.company_id is not None
        ledgers = (company_ledgers if is_company else personal_ledgers)[task.id]
        opposite = (personal_ledgers if is_company else company_ledgers)[task.id]
        invalid = PointIncome(settlement_count=len(ledgers), settled_at=ledgers[0].created_at if ledgers else None)
        result[task.id] = invalid
        if len(ledgers) != 1 or opposite or task.status != TaskStatus.SUCCEEDED:
            continue
        ledger = ledgers[0]
        amount = int(ledger.amount_points)
        result[task.id] = PointIncome(settlement_count=1, settled_points=amount, settled_at=ledger.created_at)
        scope = task.company_id if is_company else task.personal_workspace_id
        ledger_scope = ledger.company_id if is_company else ledger.workspace_id
        if (scope != ledger_scope or amount <= 0
                or task.actual_cost_points != amount or task.reserved_points != 0):
            continue
        settled = {a.id: a for a in allocations[task.id] if a.settled_points > 0}
        evidence = values[task.id]
        if not settled or len(evidence) != len(settled):
            continue
        valid = True
        seen = set()
        cash = receivable = subsidy = points = 0
        for value in evidence:
            allocation_id = (value.company_task_allocation_id if is_company
                             else value.personal_task_allocation_id)
            allocation = settled.get(allocation_id)
            value_scope = value.company_id if is_company else value.personal_workspace_id
            ledger_id = (value.company_settle_ledger_id if is_company
                         else value.personal_settle_ledger_id)
            expected_type = TaskPointLotAllocation if is_company else PersonalTaskPointLotAllocation
            if allocation is None or not isinstance(allocation, expected_type):
                valid = False
                break
            allocation_scope = allocation.company_id if is_company else allocation.workspace_id
            lot = lots.get((expected_type, allocation.lot_id))
            if lot is None or (expected_type, allocation.lot_id) in invalid_lots:
                valid = False
                break
            lot_scope = lot.company_id if is_company else lot.workspace_id
            if (allocation_id in seen or scope != value_scope or scope != allocation_scope
                    or scope != lot_scope or allocation.settled_points > lot.settled_points
                    or ledger_id != ledger.id or value.settled_points != allocation.settled_points
                    or allocation.reserved_points != 0
                    or min(value.cash_basis_cents, value.receivable_basis_cents, value.subsidy_cents) < 0
                    or value.cash_basis_cents + value.receivable_basis_cents + value.subsidy_cents
                    != value.settled_points * 10):
                valid = False
                break
            seen.add(allocation_id)
            points += int(value.settled_points)
            cash += int(value.cash_basis_cents)
            receivable += int(value.receivable_basis_cents)
            subsidy += int(value.subsidy_cents)
        if valid and points == amount and seen == set(settled):
            result[task.id] = PointIncome(1, points, cash, receivable, subsidy, True, ledger.created_at)
    return result


def point_income_tasks(session: Session, *, start: datetime, end: datetime,
                       company_only: bool = False) -> list[GenerationTask]:
    """Select by immutable posting time, retaining unposted successful gaps."""
    company_settle = select(CompanyPointLedgerEntry.id).where(
        CompanyPointLedgerEntry.task_id == GenerationTask.id,
        CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE,
    )
    personal_settle = select(PersonalLedgerEntry.id).where(
        PersonalLedgerEntry.task_id == GenerationTask.id,
        PersonalLedgerEntry.kind == LedgerKind.SETTLE,
    )
    statement = select(GenerationTask).where(or_(
        company_settle.where(CompanyPointLedgerEntry.created_at >= start,
                             CompanyPointLedgerEntry.created_at < end).exists(),
        personal_settle.where(PersonalLedgerEntry.created_at >= start,
                              PersonalLedgerEntry.created_at < end).exists(),
        (GenerationTask.status == TaskStatus.SUCCEEDED)
        & (GenerationTask.billing_version == 2)
        & ~company_settle.exists() & ~personal_settle.exists()
        & (GenerationTask.updated_at >= start) & (GenerationTask.updated_at < end),
    ))
    if company_only:
        statement = statement.where(GenerationTask.company_id.is_not(None),
                                    GenerationTask.personal_workspace_id.is_(None))
    return list(session.scalars(statement).all())


def point_income_report(session: Session, *, start: datetime, end: datetime) -> dict:
    """Both customer scopes, separately, using immutable settlement time.

    This is a consumption-value report, not PSP cash flow or accounting net
    profit. Refunds/debt, tax, fees and operating expenditure retain their own
    immutable ledgers and must not rewrite historical consumed value.
    """
    tasks = point_income_tasks(session, start=start, end=end)
    incomes = load_point_income(session, tasks)
    def empty():
        return {"task_count": 0, "settled_points": 0, "cash_basis_cents": 0,
                "receivable_basis_cents": 0, "subsidy_cents": 0,
                "settled_revenue_cents": 0, "unattributed_task_count": 0}
    scopes = {"company": empty(), "personal": empty()}
    totals = empty()
    for task in tasks:
        income = incomes[task.id]
        for row in (scopes["company" if task.company_id else "personal"], totals):
            row["task_count"] += 1
            row["settled_points"] += income.settled_points
            if not income.complete:
                row["unattributed_task_count"] += 1
                continue
            row["cash_basis_cents"] += income.cash_basis_cents
            row["receivable_basis_cents"] += income.receivable_basis_cents
            row["subsidy_cents"] += income.subsidy_cents
            row["settled_revenue_cents"] += income.revenue_cents
    for row in (*scopes.values(), totals):
        row["reconciliation_status"] = "incomplete" if row["unattributed_task_count"] else "complete"
    return {"time_basis": "immutable_settle_ledger_created_at",
            "basis": "consumed_prepaid_value_plus_receivable_excluding_subsidy",
            "is_cash_flow": False, "is_net_profit": False,
            "totals": totals, "scopes": scopes}
