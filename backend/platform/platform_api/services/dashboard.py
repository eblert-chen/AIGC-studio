from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ChannelCostEntry,
    Company,
    CompanyStatus,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    TaskStatus,
    WalletAccount,
)


class DashboardService:
    @staticmethod
    def build(
        session: Session, *, page: int, page_size: int
    ) -> dict:
        total_companies = session.scalar(select(func.count(Company.id))) or 0
        active_company_count = session.scalar(
            select(func.count(Company.id)).where(
                Company.status == CompanyStatus.ACTIVE
            )
        ) or 0
        companies = list(
            session.scalars(
                select(Company)
                .order_by(Company.created_at.desc(), Company.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        company_ids = [company.id for company in companies]
        ledger_totals: dict[tuple[str, LedgerKind], int] = {}
        task_totals: dict[tuple[str, TaskStatus], int] = {}
        wallets: dict[str, tuple[int, int]] = {}
        if company_ids:
            for company_id, kind, amount in session.execute(
                select(
                    LedgerEntry.company_id,
                    LedgerEntry.kind,
                    func.coalesce(func.sum(LedgerEntry.amount_cents), 0),
                )
                .where(LedgerEntry.company_id.in_(company_ids))
                .group_by(LedgerEntry.company_id, LedgerEntry.kind)
            ):
                ledger_totals[(company_id, kind)] = int(amount)
            for company_id, status, count in session.execute(
                select(
                    GenerationTask.company_id,
                    GenerationTask.status,
                    func.count(GenerationTask.id),
                )
                .where(GenerationTask.company_id.in_(company_ids))
                .group_by(GenerationTask.company_id, GenerationTask.status)
            ):
                task_totals[(company_id, status)] = int(count)
            wallets = {
                company_id: (int(available_cents), int(reserved_cents))
                for company_id, available_cents, reserved_cents in session.execute(
                    select(
                        WalletAccount.company_id,
                        WalletAccount.available_cents,
                        WalletAccount.reserved_cents,
                    ).where(WalletAccount.company_id.in_(company_ids))
                ).all()
            }

        platform_income = session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
                LedgerEntry.kind == LedgerKind.SETTLE
            )
        )
        platform_recharge = session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
                LedgerEntry.kind == LedgerKind.RECHARGE
            )
        )
        channel_cost = session.scalar(
            select(func.coalesce(func.sum(ChannelCostEntry.amount_cents), 0)).where(
                ChannelCostEntry.company_id.is_not(None),
                ChannelCostEntry.personal_workspace_id.is_(None),
            )
        )
        channel_costs = [
            {
                "channel_key": channel_key,
                "channel_type": channel_type,
                "amount_cents": int(amount_cents),
            }
            for channel_key, channel_type, amount_cents in session.execute(
                select(
                    ChannelCostEntry.channel_key,
                    ChannelCostEntry.channel_type,
                    func.coalesce(func.sum(ChannelCostEntry.amount_cents), 0),
                )
                .where(
                    ChannelCostEntry.company_id.is_not(None),
                    ChannelCostEntry.personal_workspace_id.is_(None),
                )
                .group_by(
                    ChannelCostEntry.channel_key,
                    ChannelCostEntry.channel_type,
                )
                .order_by(
                    ChannelCostEntry.channel_type,
                    ChannelCostEntry.channel_key,
                )
            )
        ]
        unreconciled_succeeded_count = session.scalar(
            select(func.count(GenerationTask.id)).where(
                GenerationTask.status == TaskStatus.SUCCEEDED,
                GenerationTask.company_id.is_not(None),
                GenerationTask.personal_workspace_id.is_(None),
                ~select(ChannelCostEntry.id)
                .where(ChannelCostEntry.task_id == GenerationTask.id)
                .exists(),
            )
        ) or 0
        global_task_counts = dict(
            session.execute(
                select(GenerationTask.status, func.count(GenerationTask.id))
                .where(
                    GenerationTask.company_id.is_not(None),
                    GenerationTask.personal_workspace_id.is_(None),
                )
                .group_by(GenerationTask.status)
            ).all()
        )
        total_task_count = sum(int(count) for count in global_task_counts.values())
        rows = []
        for company in companies:
            task_count = sum(
                count
                for (company_id, _), count in task_totals.items()
                if company_id == company.id
            )
            rows.append(
                {
                    "company_id": company.id,
                    "company_name": company.name,
                    "company_status": company.status,
                    "recharge_cents": ledger_totals.get(
                        (company.id, LedgerKind.RECHARGE), 0
                    ),
                    "consumption_cents": ledger_totals.get(
                        (company.id, LedgerKind.SETTLE), 0
                    ),
                    "available_cents": wallets.get(company.id, (0, 0))[0],
                    "reserved_cents": wallets.get(company.id, (0, 0))[1],
                    "task_count": task_count,
                    "succeeded_count": task_totals.get(
                        (company.id, TaskStatus.SUCCEEDED), 0
                    ),
                    "failed_count": task_totals.get(
                        (company.id, TaskStatus.FAILED), 0
                    ),
                }
            )
        known_gross_profit = int(platform_income or 0) - int(channel_cost or 0)
        cost_complete = not unreconciled_succeeded_count
        return {
            "platform_income_cents": int(platform_income or 0),
            "platform_recharge_cents": int(platform_recharge or 0),
            "channel_cost_cents": int(channel_cost or 0),
            "known_gross_profit_cents": known_gross_profit,
            "gross_profit_cents": (
                known_gross_profit if cost_complete else None
            ),
            "channel_costs": channel_costs,
            "unreconciled_succeeded_count": int(
                unreconciled_succeeded_count
            ),
            "channel_cost_status": (
                "complete" if cost_complete else "incomplete"
            ),
            "active_company_count": int(active_company_count),
            "total_task_count": total_task_count,
            "succeeded_task_count": int(
                global_task_counts.get(TaskStatus.SUCCEEDED, 0)
            ),
            "failed_task_count": int(
                global_task_counts.get(TaskStatus.FAILED, 0)
            ),
            "page": page,
            "page_size": page_size,
            "total_companies": total_companies,
            "companies": rows,
        }
