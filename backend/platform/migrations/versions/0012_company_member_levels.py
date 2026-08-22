"""Normalize every company member to one primary organization level.

Revision ID: 0012_company_member_levels
Revises: 0011_relay_submit_reconcile
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_company_member_levels"
down_revision: str | None = "0011_relay_submit_reconcile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    company_ids = list(
        connection.execute(sa.text("SELECT id FROM companies")).scalars()
    )
    for company_id in company_ids:
        role_rows = connection.execute(
            sa.text(
                "SELECT id, system_key FROM roles "
                "WHERE company_id = :company_id AND "
                "system_key IN ('owner', 'team_lead', 'operator')"
            ),
            {"company_id": company_id},
        ).all()
        role_ids = {system_key: role_id for role_id, system_key in role_rows}
        owner_role_id = role_ids.get("owner")
        team_lead_role_id = role_ids.get("team_lead")
        operator_role_id = role_ids.get("operator")
        if not owner_role_id or not team_lead_role_id or not operator_role_id:
            raise RuntimeError(
                f"company {company_id} is missing a required organization role"
            )

        owner_membership_ids = set(
            connection.execute(
                sa.text(
                    "SELECT membership_id FROM membership_roles "
                    "WHERE role_id = :owner_role_id"
                ),
                {"owner_role_id": owner_role_id},
            ).scalars()
        )
        membership_ids = list(
            connection.execute(
                sa.text(
                    "SELECT id FROM company_memberships "
                    "WHERE company_id = :company_id"
                ),
                {"company_id": company_id},
            ).scalars()
        )

        for membership_id in membership_ids:
            if membership_id in owner_membership_ids:
                connection.execute(
                    sa.text(
                        "DELETE FROM membership_roles "
                        "WHERE membership_id = :membership_id AND "
                        "role_id IN (:team_lead_role_id, :operator_role_id)"
                    ),
                    {
                        "membership_id": membership_id,
                        "team_lead_role_id": team_lead_role_id,
                        "operator_role_id": operator_role_id,
                    },
                )
                continue

            assigned_primary_ids = set(
                connection.execute(
                    sa.text(
                        "SELECT role_id FROM membership_roles "
                        "WHERE membership_id = :membership_id AND "
                        "role_id IN (:team_lead_role_id, :operator_role_id)"
                    ),
                    {
                        "membership_id": membership_id,
                        "team_lead_role_id": team_lead_role_id,
                        "operator_role_id": operator_role_id,
                    },
                ).scalars()
            )
            if team_lead_role_id in assigned_primary_ids:
                connection.execute(
                    sa.text(
                        "DELETE FROM membership_roles "
                        "WHERE membership_id = :membership_id "
                        "AND role_id = :operator_role_id"
                    ),
                    {
                        "membership_id": membership_id,
                        "operator_role_id": operator_role_id,
                    },
                )
            elif operator_role_id not in assigned_primary_ids:
                connection.execute(
                    sa.text(
                        "INSERT INTO membership_roles (membership_id, role_id) "
                        "VALUES (:membership_id, :operator_role_id)"
                    ),
                    {
                        "membership_id": membership_id,
                        "operator_role_id": operator_role_id,
                    },
                )


def downgrade() -> None:
    # This migration normalizes ambiguous legacy data. The old zero/multiple
    # primary-role state cannot be reconstructed safely.
    pass
