"""Seal structured Director Shot Packages and bind them to generation tasks.

Revision ID: 0053_director_shot_packages
Revises: 0052_personal_input_assets
"""
from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v18 as policy_v18
from platform_api.database_privileges_behavior_v18 import (
    protected_platform_runtime_requested_v18,
)


revision: str = "0053_director_shot_packages"
down_revision: str | None = "0052_personal_input_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_TABLES = ("director_shot_packages", "task_director_shot_packages")


def _quote(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _sha256_constraints(column: str, name: str):
    sqlite = (
        f"length({column}) = 64 AND lower({column}) = {column} "
        f"AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    postgres = f"{column} ~ '^[0-9a-f]{{64}}$'"
    return (
        sa.CheckConstraint(sqlite, name=name).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(postgres, name=name).ddl_if(dialect="postgresql"),
    )


def _apply_acl() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    runtime_roles = tuple(
        role
        for process, role in policy_v18.DATABASE_ROLE_BY_PROCESS.items()
        if process != "migration"
    )
    for table_name in _TABLES:
        for role in runtime_roles:
            op.execute(
                sa.text(
                    f"REVOKE ALL PRIVILEGES ON TABLE public.{_quote(table_name)} "
                    f"FROM {_quote(role)}"
                )
            )
    for process, privileges_by_table in policy_v18.PRIVILEGES_BY_PROCESS.items():
        role = _quote(policy_v18.DATABASE_ROLE_BY_PROCESS[process])
        for table_name in _TABLES:
            privileges = privileges_by_table.get(table_name)
            if privileges:
                op.execute(
                    sa.text(
                        f"GRANT {', '.join(sorted(privileges))} ON TABLE public."
                        f"{_quote(table_name)} TO {role}"
                    )
                )


def _create_immutable_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    "CREATE TRIGGER trg_director_shot_package_no_"
                    f"{action.lower()} BEFORE {action} ON director_shot_packages "
                    "BEGIN SELECT RAISE(ABORT, "
                    "'director shot packages are immutable'); END"
                )
            )
        for action in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    "CREATE TRIGGER trg_task_director_shot_package_no_"
                    f"{action.lower()} BEFORE {action} ON "
                    "task_director_shot_packages BEGIN SELECT "
                    "RAISE(ABORT, 'task director shot package bindings are "
                    "immutable'); END"
                )
            )
    elif dialect == "postgresql":
        op.execute(
            sa.text(
                "CREATE FUNCTION reject_director_shot_package_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'director shot packages are immutable'; END $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_director_shot_package_immutable BEFORE UPDATE "
                "OR DELETE ON director_shot_packages FOR EACH ROW EXECUTE FUNCTION "
                "reject_director_shot_package_mutation()"
            )
        )
        op.execute(
            sa.text(
                "CREATE FUNCTION reject_task_director_shot_package_update() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'task director shot package bindings are immutable'; END $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_task_director_shot_package_immutable BEFORE UPDATE "
                "OR DELETE ON task_director_shot_packages FOR EACH ROW EXECUTE FUNCTION "
                "reject_task_director_shot_package_update()"
            )
        )


def _drop_immutable_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for trigger in (
            "trg_director_shot_package_no_update",
            "trg_director_shot_package_no_delete",
            "trg_task_director_shot_package_no_update",
            "trg_task_director_shot_package_no_delete",
        ):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
    elif dialect == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_director_shot_package_immutable "
                "ON director_shot_packages"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS reject_director_shot_package_mutation"
            )
        )
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_task_director_shot_package_immutable "
                "ON task_director_shot_packages"
            )
        )
        op.execute(
            sa.text(
                "DROP FUNCTION IF EXISTS reject_task_director_shot_package_update"
            )
        )


def upgrade() -> None:
    op.create_table(
        "director_shot_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), nullable=True),
        sa.Column("personal_workspace_id", sa.String(36), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("composition_asset_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("scene_revision_sha256", sa.String(64), nullable=False),
        sa.Column("sealed_revision_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["personal_workspace_id"], ["personal_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["composition_asset_id"], ["input_assets.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "company_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_director_shot_package_company_idempotency",
        ),
        sa.UniqueConstraint(
            "personal_workspace_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_director_shot_package_personal_idempotency",
        ),
        sa.CheckConstraint(
            "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
            "(company_id IS NULL AND personal_workspace_id IS NOT NULL)",
            name="ck_director_shot_package_scope",
        ),
        sa.CheckConstraint(
            "length(id) = 36 AND substr(id, 1, 4) = 'dsp_'",
            name="ck_director_shot_package_id",
        ),
        sa.CheckConstraint(
            "schema_version = 1", name="ck_director_shot_package_schema_version"
        ),
        *_sha256_constraints(
            "manifest_sha256", "ck_director_shot_package_manifest_sha"
        ),
        *_sha256_constraints(
            "scene_revision_sha256", "ck_director_shot_package_scene_revision_sha"
        ),
        *_sha256_constraints(
            "sealed_revision_sha256", "ck_director_shot_package_sealed_revision_sha"
        ),
        *_sha256_constraints(
            "request_fingerprint", "ck_director_shot_package_request_fingerprint_sha"
        ),
    )
    for index_name, columns in (
        ("ix_director_shot_packages_company_id", ["company_id"]),
        ("ix_director_shot_packages_personal_workspace_id", ["personal_workspace_id"]),
        ("ix_director_shot_packages_created_by_user_id", ["created_by_user_id"]),
        ("ix_director_shot_packages_composition_asset_id", ["composition_asset_id"]),
        ("ix_director_shot_package_company_created", ["company_id", "created_at"]),
        (
            "ix_director_shot_package_personal_created",
            ["personal_workspace_id", "created_at"],
        ),
    ):
        op.create_index(index_name, "director_shot_packages", columns)

    op.create_table(
        "task_director_shot_packages",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("package_id", sa.String(36), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["generation_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["package_id"], ["director_shot_packages.id"], ondelete="RESTRICT"
        ),
        *_sha256_constraints(
            "manifest_sha256", "ck_task_director_shot_package_manifest_sha"
        ),
    )
    op.create_index(
        "ix_task_director_shot_package_package",
        "task_director_shot_packages",
        ["package_id"],
    )
    _create_immutable_guards()
    if (
        op.get_bind().dialect.name == "postgresql"
        and protected_platform_runtime_requested_v18()
    ):
        _apply_acl()


def downgrade() -> None:
    bind = op.get_bind()
    package_count = int(
        bind.execute(sa.text("SELECT count(*) FROM director_shot_packages")).scalar_one()
    )
    if package_count:
        raise RuntimeError("0053 downgrade blocked by sealed director shot packages")
    _drop_immutable_guards()
    op.drop_index(
        "ix_task_director_shot_package_package",
        table_name="task_director_shot_packages",
    )
    op.drop_table("task_director_shot_packages")
    for index_name in (
        "ix_director_shot_package_personal_created",
        "ix_director_shot_package_company_created",
        "ix_director_shot_packages_composition_asset_id",
        "ix_director_shot_packages_created_by_user_id",
        "ix_director_shot_packages_personal_workspace_id",
        "ix_director_shot_packages_company_id",
    ):
        op.drop_index(index_name, table_name="director_shot_packages")
    op.drop_table("director_shot_packages")
