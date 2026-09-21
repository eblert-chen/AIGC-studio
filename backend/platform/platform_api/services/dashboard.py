from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    BillingUnit,
    ChannelCostEntry,
    Company,
    CompanyPointLedgerEntry,
    CompanyPointWalletAccount,
    CompanyStatus,
    GenerationTask,
    LedgerEntry,
    LedgerKind,
    PointLedgerKind,
    TaskStatus,
    WalletAccount,
)
from .errors import ConflictError


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
        point_ledger_totals: dict[tuple[str, PointLedgerKind], int] = {}
        task_totals: dict[tuple[str, TaskStatus], int] = {}
        wallets: dict[str, tuple[int, int]] = {}
        point_wallets: dict[str, tuple[int, int]] = {}
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
            for company_id, kind, amount in session.execute(
                select(
                    CompanyPointLedgerEntry.company_id,
                    CompanyPointLedgerEntry.kind,
                    func.coalesce(
                        func.sum(CompanyPointLedgerEntry.amount_points), 0
                    ),
                )
                .where(CompanyPointLedgerEntry.company_id.in_(company_ids))
                .group_by(
                    CompanyPointLedgerEntry.company_id,
                    CompanyPointLedgerEntry.kind,
                )
            ):
                point_ledger_totals[(company_id, kind)] = int(amount)
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
            point_wallets = {
                company_id: (int(available_points), int(reserved_points))
                for company_id, available_points, reserved_points in session.execute(
                    select(
                        CompanyPointWalletAccount.company_id,
                        CompanyPointWalletAccount.available_points,
                        CompanyPointWalletAccount.reserved_points,
                    ).where(CompanyPointWalletAccount.company_id.in_(company_ids))
                ).all()
            }

        for company in companies:
            if company.billing_version == 2 and company.id not in point_wallets:
                raise ConflictError("企业积分计费版本与积分钱包投影不一致")
            if company.billing_version == 1 and company.id not in wallets:
                raise ConflictError("企业旧币种计费版本与钱包投影不一致")

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
        platform_point_recharge = session.scalar(
            select(
                func.coalesce(func.sum(CompanyPointLedgerEntry.amount_points), 0)
            ).where(
                CompanyPointLedgerEntry.kind.in_(
                    {PointLedgerKind.MIGRATION, PointLedgerKind.CREDIT}
                )
            )
        )
        platform_point_consumption = session.scalar(
            select(
                func.coalesce(func.sum(CompanyPointLedgerEntry.amount_points), 0)
            ).where(CompanyPointLedgerEntry.kind == PointLedgerKind.SETTLE)
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
        unattributed_point_settlement_count = session.scalar(
            select(func.count(GenerationTask.id)).where(
                GenerationTask.status == TaskStatus.SUCCEEDED,
                GenerationTask.company_id.is_not(None),
                GenerationTask.personal_workspace_id.is_(None),
                GenerationTask.billing_unit == BillingUnit.POINT,
                GenerationTask.billing_version == 2,
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
                    "billing_unit": (
                        BillingUnit.POINT
                        if company.billing_version == 2
                        else BillingUnit.CNY_CENT
                    ),
                    "billing_version": company.billing_version,
                    "recharge_cents": ledger_totals.get(
                        (company.id, LedgerKind.RECHARGE), 0
                    ),
                    "consumption_cents": ledger_totals.get(
                        (company.id, LedgerKind.SETTLE), 0
                    ),
                    "available_cents": wallets.get(company.id, (0, 0))[0],
                    "reserved_cents": wallets.get(company.id, (0, 0))[1],
                    "recharge_points": point_ledger_totals.get(
                        (company.id, PointLedgerKind.MIGRATION), 0
                    )
                    + point_ledger_totals.get(
                        (company.id, PointLedgerKind.CREDIT), 0
                    ),
                    "consumption_points": point_ledger_totals.get(
                        (company.id, PointLedgerKind.SETTLE), 0
                    ),
                    "available_points": point_wallets.get(company.id, (0, 0))[0],
                    "reserved_points": point_wallets.get(company.id, (0, 0))[1],
                    "task_count": task_count,
                    "succeeded_count": task_totals.get(
                        (company.id, TaskStatus.SUCCEEDED), 0
                    ),
                    "failed_count": task_totals.get(
                        (company.id, TaskStatus.FAILED), 0
                    ),
                }
            )
        billing_versions = {
            int(version)
            for version in session.scalars(
                select(Company.billing_version).distinct()
            ).all()
        }
        if billing_versions == {1}:
            platform_billing_unit: BillingUnit | str = BillingUnit.CNY_CENT
            platform_billing_version: int | None = 1
        elif billing_versions == {2}:
            platform_billing_unit = BillingUnit.POINT
            platform_billing_version = 2
        else:
            platform_billing_unit = "MIXED"
            platform_billing_version = None
        known_gross_profit = int(platform_income or 0) - int(channel_cost or 0)
        cost_complete = not unreconciled_succeeded_count
        revenue_complete = not unattributed_point_settlement_count
        finance_complete = cost_complete and revenue_complete
        return {
            "billing_unit": platform_billing_unit,
            "billing_version": platform_billing_version,
            "platform_income_cents": int(platform_income or 0),
            "platform_recharge_cents": int(platform_recharge or 0),
            "platform_recharge_points": int(platform_point_recharge or 0),
            "platform_consumption_points": int(platform_point_consumption or 0),
            "channel_cost_cents": int(channel_cost or 0),
            "known_gross_profit_cents": known_gross_profit,
            "gross_profit_cents": (
                known_gross_profit if finance_complete else None
            ),
            "channel_costs": channel_costs,
            "unreconciled_succeeded_count": int(
                unreconciled_succeeded_count
            ),
            "channel_cost_status": (
                "complete" if cost_complete else "incomplete"
            ),
            "unattributed_point_settlement_count": int(
                unattributed_point_settlement_count
            ),
            "revenue_reconciliation_status": (
                "complete" if revenue_complete else "incomplete"
            ),
            "finance_status": (
                "complete" if finance_complete else "incomplete"
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
