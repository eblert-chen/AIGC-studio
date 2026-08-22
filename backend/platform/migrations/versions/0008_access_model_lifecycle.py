"""Backfill role templates and explicit model publication history.

Revision ID: 0008_access_model_lifecycle
Revises: 0007_task_timeout_compensation
Create Date: 2026-08-01
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "0008_access_model_lifecycle"
down_revision: str | None = "0007_task_timeout_compensation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TEAM_LEAD_PERMISSIONS = (
    "users.read",
    "models.read",
    "resources.read",
    "tasks.read",
    "tasks.create",
    "tasks.manage",
)

OPERATOR_PERMISSIONS = (
    "models.read",
    "resources.read",
    "tasks.read",
    "tasks.create",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _insert_template(
    connection,
    *,
    company_id: str,
    name: str,
    description: str,
    system_key: str,
    permission_codes: tuple[str, ...],
) -> None:
    existing = connection.execute(
        sa.text(
            "SELECT id FROM roles "
            "WHERE company_id = :company_id AND "
            "(system_key = :system_key OR name = :name)"
        ),
        {"company_id": company_id, "system_key": system_key, "name": name},
    ).scalar()
    if existing:
        connection.execute(
            sa.text(
                "UPDATE roles SET is_system = :is_system, system_key = :system_key "
                "WHERE id = :role_id"
            ),
            {"is_system": True, "system_key": system_key, "role_id": existing},
        )
        role_id = existing
    else:
        role_id = str(uuid.uuid4())
        now = _utcnow()
        connection.execute(
            sa.text(
                "INSERT INTO roles "
                "(id, company_id, name, description, is_system, system_key, "
                "created_at, updated_at) VALUES "
                "(:id, :company_id, :name, :description, :is_system, "
                ":system_key, :created_at, :updated_at)"
            ),
            {
                "id": role_id,
                "company_id": company_id,
                "name": name,
                "description": description,
                "is_system": True,
                "system_key": system_key,
                "created_at": now,
                "updated_at": now,
            },
        )

    existing_permissions = set(
        connection.execute(
            sa.text(
                "SELECT permission_code FROM role_permissions "
                "WHERE role_id = :role_id"
            ),
            {"role_id": role_id},
        ).scalars()
    )
    valid_permissions = set(
        connection.execute(
            sa.text(
                "SELECT code FROM permissions WHERE code IN "
                "('users.read', 'models.read', 'resources.read', "
                "'tasks.read', 'tasks.create', 'tasks.manage')"
            )
        ).scalars()
    )
    for permission_code in permission_codes:
        if (
            permission_code in valid_permissions
            and permission_code not in existing_permissions
        ):
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code) "
                    "VALUES (:role_id, :permission_code)"
                ),
                {"role_id": role_id, "permission_code": permission_code},
            )


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch_op:
        batch_op.add_column(sa.Column("system_key", sa.String(length=40), nullable=True))
    with op.batch_alter_table("model_definitions") as batch_op:
        batch_op.add_column(
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE model_definitions SET published_at = created_at "
            "WHERE published_at IS NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE roles SET system_key = :system_key "
            "WHERE is_system = :is_system AND name = :name"
        ),
        {"system_key": "owner", "is_system": True, "name": "老板"},
    )

    company_ids = connection.execute(sa.text("SELECT id FROM companies")).scalars()
    for company_id in company_ids:
        _insert_template(
            connection,
            company_id=company_id,
            name="组长",
            description="公司内置组长权限模板，老板可按成员继续微调",
            system_key="team_lead",
            permission_codes=TEAM_LEAD_PERMISSIONS,
        )
        _insert_template(
            connection,
            company_id=company_id,
            name="运营",
            description="公司内置运营权限模板，老板可按成员继续微调",
            system_key="operator",
            permission_codes=OPERATOR_PERMISSIONS,
        )

    with op.batch_alter_table("roles") as batch_op:
        batch_op.create_unique_constraint(
            "uq_role_company_system_key", ["company_id", "system_key"]
        )


def downgrade() -> None:
    connection = op.get_bind()
    template_role_ids = list(
        connection.execute(
            sa.text(
                "SELECT id FROM roles WHERE system_key IN "
                "('team_lead', 'operator')"
            )
        ).scalars()
    )
    for role_id in template_role_ids:
        connection.execute(
            sa.text("DELETE FROM membership_roles WHERE role_id = :role_id"),
            {"role_id": role_id},
        )
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :role_id"),
            {"role_id": role_id},
        )
        connection.execute(
            sa.text("DELETE FROM roles WHERE id = :role_id"),
            {"role_id": role_id},
        )

    with op.batch_alter_table("roles") as batch_op:
        batch_op.drop_constraint("uq_role_company_system_key", type_="unique")
        batch_op.drop_column("system_key")
    with op.batch_alter_table("model_definitions") as batch_op:
        batch_op.drop_column("published_at")
