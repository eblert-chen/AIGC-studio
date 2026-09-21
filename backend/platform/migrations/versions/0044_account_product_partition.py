"""Persist and enforce mutually exclusive product account boundaries.

Revision ID: 0044_account_product_partition
Revises: 0043_admin_task_content

The historical personal-workspace migration provisioned a workspace for every
then-existing user, including company members and platform administrators.  This
revision classifies the user itself as the authoritative product boundary,
keeps every historical row, and disables only surfaces that no longer belong to
that boundary.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v8 as policy_v8
from platform_api import database_privileges_v9 as policy_v9
from platform_api.database_privileges_behavior_v8 import (
    collect_platform_database_evidence as collect_v8_database_evidence,
    validate_platform_database_acl_evidence as validate_v8_database_acl_evidence,
)
from platform_api.database_privileges_behavior_v9 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v9,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0044_account_product_partition"
down_revision: str | None = "0043_admin_task_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACCOUNT_TYPES = "'PERSONAL', 'COMPANY', 'PLATFORM_ADMIN'"


def _account_type_type() -> sa.Enum:
    # Match the ORM's non-native enum exactly so ``alembic check`` remains a
    # useful schema-drift gate on both SQLite and PostgreSQL.
    return sa.Enum(
        "PERSONAL",
        "COMPANY",
        "PLATFORM_ADMIN",
        name="useraccounttype",
        native_enum=False,
    )


def _create_postgres_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_personal_workspace_account_type()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            resolved_account_type text;
            must_validate boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                must_validate := true;
            ELSE
                must_validate := NEW.active OR NEW.user_id IS DISTINCT FROM OLD.user_id;
            END IF;
            IF must_validate THEN
                SELECT account_type INTO resolved_account_type
                FROM users WHERE id = NEW.user_id;
                IF resolved_account_type IS DISTINCT FROM 'PERSONAL' THEN
                    RAISE EXCEPTION 'personal workspace requires a PERSONAL account'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_personal_workspace_account_type_insert
        BEFORE INSERT ON personal_workspaces
        FOR EACH ROW EXECUTE FUNCTION enforce_personal_workspace_account_type()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_personal_workspace_account_type_update
        BEFORE UPDATE OF user_id, active ON personal_workspaces
        FOR EACH ROW EXECUTE FUNCTION enforce_personal_workspace_account_type()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_company_membership_account_type()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            resolved_account_type text;
            must_validate boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                must_validate := true;
            ELSE
                must_validate := (
                    NEW.status = 'ACTIVE'
                    OR NEW.user_id IS DISTINCT FROM OLD.user_id
                );
            END IF;
            IF must_validate THEN
                SELECT account_type INTO resolved_account_type
                FROM users WHERE id = NEW.user_id;
                IF resolved_account_type IS DISTINCT FROM 'COMPANY' THEN
                    RAISE EXCEPTION 'company membership requires a COMPANY account'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_company_membership_account_type_insert
        BEFORE INSERT ON company_memberships
        FOR EACH ROW EXECUTE FUNCTION enforce_company_membership_account_type()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_company_membership_account_type_update
        BEFORE UPDATE OF user_id, status ON company_memberships
        FOR EACH ROW EXECUTE FUNCTION enforce_company_membership_account_type()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_user_account_type_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.account_type IS DISTINCT FROM OLD.account_type THEN
                RAISE EXCEPTION 'user account_type is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_users_account_type_immutable
        BEFORE UPDATE OF account_type ON users
        FOR EACH ROW EXECUTE FUNCTION reject_user_account_type_mutation()
        """
    )
    # Trigger functions need no direct runtime EXECUTE grants.  Harden them
    # explicitly as well as relying on the protected database's frozen default
    # privileges so local/rehearsal PostgreSQL cannot accidentally expose a
    # wider routine surface.
    for routine_name in (
        "enforce_personal_workspace_account_type",
        "enforce_company_membership_account_type",
        "reject_user_account_type_mutation",
    ):
        op.execute(
            f"REVOKE ALL PRIVILEGES ON FUNCTION {routine_name}() FROM PUBLIC"
        )


def _drop_postgres_guards() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_users_account_type_immutable ON users"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS reject_user_account_type_mutation()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_membership_account_type_update "
        "ON company_memberships"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_membership_account_type_insert "
        "ON company_memberships"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS enforce_company_membership_account_type()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_personal_workspace_account_type_update "
        "ON personal_workspaces"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_personal_workspace_account_type_insert "
        "ON personal_workspaces"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS enforce_personal_workspace_account_type()"
    )


def _create_sqlite_guards() -> None:
    statements = (
        "CREATE TRIGGER trg_personal_workspace_account_type_insert "
        "BEFORE INSERT ON personal_workspaces "
        "WHEN COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') "
        "<> 'PERSONAL' BEGIN SELECT RAISE(ABORT, "
        "'personal workspace requires a PERSONAL account'); END",
        "CREATE TRIGGER trg_personal_workspace_account_type_update "
        "BEFORE UPDATE OF user_id, active ON personal_workspaces "
        "WHEN (NEW.active = 1 OR NEW.user_id <> OLD.user_id) AND "
        "COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') "
        "<> 'PERSONAL' BEGIN SELECT RAISE(ABORT, "
        "'personal workspace requires a PERSONAL account'); END",
        "CREATE TRIGGER trg_company_membership_account_type_insert "
        "BEFORE INSERT ON company_memberships "
        "WHEN COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') "
        "<> 'COMPANY' BEGIN SELECT RAISE(ABORT, "
        "'company membership requires a COMPANY account'); END",
        "CREATE TRIGGER trg_company_membership_account_type_update "
        "BEFORE UPDATE OF user_id, status ON company_memberships "
        "WHEN (NEW.status = 'ACTIVE' OR NEW.user_id <> OLD.user_id) AND "
        "COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') "
        "<> 'COMPANY' BEGIN SELECT RAISE(ABORT, "
        "'company membership requires a COMPANY account'); END",
        "CREATE TRIGGER trg_users_account_type_immutable "
        "BEFORE UPDATE OF account_type ON users "
        "WHEN NEW.account_type <> OLD.account_type BEGIN SELECT RAISE(ABORT, "
        "'user account_type is immutable'); END",
    )
    for statement in statements:
        op.execute(statement)


def _drop_sqlite_guards() -> None:
    for name in (
        "trg_users_account_type_immutable",
        "trg_company_membership_account_type_update",
        "trg_company_membership_account_type_insert",
        "trg_personal_workspace_account_type_update",
        "trg_personal_workspace_account_type_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")


def _create_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _create_postgres_guards()
    elif dialect == "sqlite":
        _create_sqlite_guards()
    else:  # pragma: no cover - production uses PostgreSQL and tests use SQLite.
        raise RuntimeError(f"unsupported Platform database dialect: {dialect}")


def _drop_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        _drop_postgres_guards()
    elif dialect == "sqlite":
        _drop_sqlite_guards()


def _restore_sqlite_casefold_email_index() -> None:
    # SQLite batch-table recreation cannot reflect expression indexes and
    # drops this historical uniqueness guard.  Recreate it after either batch
    # so both the new head and a later downgrade retain case-insensitive email
    # uniqueness. PostgreSQL ALTER TABLE preserves its native index.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_casefold "
            "ON users (lower(email))"
        )


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v9()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v9)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v9,
        )

    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "account_type",
                _account_type_type(),
                nullable=True,
                server_default=sa.text("'PERSONAL'"),
            )
        )

    # Precedence is deliberate and stable: the security boundary outranks any
    # historical product association, then a company association or pending
    # invitation outranks the workspace that revision 0034 created for everyone.
    op.execute(
        "UPDATE users SET account_type = 'PLATFORM_ADMIN' "
        "WHERE is_platform_admin = true"
    )
    op.execute(
        "UPDATE users SET account_type = 'COMPANY' "
        "WHERE is_platform_admin = false AND EXISTS ("
        "SELECT 1 FROM company_memberships membership "
        "WHERE membership.user_id = users.id)"
    )
    op.execute(
        "UPDATE users SET account_type = 'COMPANY' "
        "WHERE is_platform_admin = false "
        "AND status = 'PENDING' "
        "AND NOT EXISTS (SELECT 1 FROM company_memberships membership "
        "WHERE membership.user_id = users.id) "
        "AND EXISTS (SELECT 1 FROM company_invitations invitation "
        "WHERE lower(invitation.email) = lower(users.email) "
        "AND invitation.status = 'PENDING')"
    )
    op.execute(
        "UPDATE users SET account_type = 'PERSONAL' "
        "WHERE account_type IS NULL"
    )

    # Preserve wallets, immutable ledger rows, tasks, artifacts, membership and
    # role evidence. Only their now-invalid entry surfaces are disabled.
    op.execute(
        "UPDATE personal_workspaces SET active = false, "
        "updated_at = CURRENT_TIMESTAMP WHERE user_id IN ("
        "SELECT id FROM users WHERE account_type IN "
        "('COMPANY', 'PLATFORM_ADMIN'))"
    )
    op.execute(
        "UPDATE company_memberships SET status = 'DISABLED', "
        "updated_at = CURRENT_TIMESTAMP WHERE user_id IN ("
        "SELECT id FROM users WHERE account_type = 'PLATFORM_ADMIN')"
    )

    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "account_type",
            existing_type=_account_type_type(),
            nullable=False,
            server_default=sa.text("'PERSONAL'"),
        )
        batch.create_check_constraint(
            "ck_users_account_type",
            f"account_type IN ({_ACCOUNT_TYPES})",
        )
        batch.create_check_constraint(
            "ck_users_account_type_admin_consistency",
            "(account_type = 'PLATFORM_ADMIN') OR "
            "(account_type IN ('PERSONAL', 'COMPANY') "
            "AND is_platform_admin = false)",
        )

    _restore_sqlite_casefold_email_index()
    _create_guards()

    if protected_postgres:
        evidence = collect_platform_database_evidence(connection, policy=policy_v9)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v9,
        )


def downgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v9()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v9)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v9,
        )

    # Guards must be removed before the compatibility surfaces can be restored.
    _drop_guards()

    # Do not re-enable any personal workspace here.  Revision 0044 cannot
    # distinguish a row it disabled from one that was intentionally inactive
    # before the upgrade; widening access without durable provenance would be
    # unsafe.  Historical wallet, ledger, task, and artifact rows remain
    # preserved for a deliberate recovery or account-migration workflow.

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint(
            "ck_users_account_type_admin_consistency", type_="check"
        )
        batch.drop_constraint("ck_users_account_type", type_="check")
        batch.drop_column("account_type")

    _restore_sqlite_casefold_email_index()

    if protected_postgres:
        evidence = collect_v8_database_evidence(connection, policy=policy_v8)
        validate_v8_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v8,
        )
