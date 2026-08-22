"""Persist the production authentication and account lifecycle boundary.

Revision ID: 0037_production_auth_lifecycle
Revises: 0036_platform_db_roles

The schema makes external identities, browser sessions, one-time OIDC state,
security events, and company invitations durable.  The protected PostgreSQL
path also replaces the v1 runtime ACL with the exact v2 policy after all DDL
has completed.
"""

from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa

from platform_api import database_privileges_v2 as policy_v2
from platform_api.database_privileges_behavior_v2 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v2,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0037_production_auth_lifecycle"
down_revision: str | None = "0036_platform_db_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


user_status = sa.Enum(
    "ACTIVE",
    "PENDING",
    "SUSPENDED",
    "DEACTIVATED",
    name="userstatus",
    native_enum=False,
)
company_invitation_status = sa.Enum(
    "PENDING",
    "ACCEPTED",
    "REVOKED",
    "EXPIRED",
    name="companyinvitationstatus",
    native_enum=False,
)
audit_outcome = sa.Enum(
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    name="auditoutcome",
    native_enum=False,
)
ledger_kind = sa.Enum(
    "RECHARGE",
    "RESERVE",
    "SETTLE",
    "RELEASE",
    name="ledgerkind",
    native_enum=False,
)


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def _sha256_check_constraints(
    column_name: str,
    *,
    constraint_name: str,
    nullable: bool = False,
) -> tuple[sa.CheckConstraint, sa.CheckConstraint]:
    sqlite_expression = (
        f"length({column_name}) = 64 "
        f"AND lower({column_name}) = {column_name} "
        f"AND {column_name} NOT GLOB '*[^0-9a-f]*'"
    )
    postgres_expression = f"{column_name} ~ '^[0-9a-f]{{64}}$'"
    if nullable:
        sqlite_expression = f"{column_name} IS NULL OR ({sqlite_expression})"
        postgres_expression = f"{column_name} IS NULL OR ({postgres_expression})"
    return (
        sa.CheckConstraint(
            sqlite_expression,
            name=constraint_name,
        ).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(
            postgres_expression,
            name=constraint_name,
        ).ddl_if(dialect="postgresql"),
    )


def _validate_user_email_casefold_inventory() -> None:
    duplicate = op.get_bind().scalar(
        sa.text(
            "SELECT 1 FROM users GROUP BY lower(email) "
            "HAVING count(*) > 1 LIMIT 1"
        )
    )
    if duplicate is not None:
        raise RuntimeError("user email casefold inventory is not unique")


def _create_sqlite_personal_ledger_guards() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_personal_ledger_entries_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_personal_ledger_entries_no_delete")
    op.execute(
        """
        CREATE TRIGGER trg_personal_ledger_entries_no_update
        BEFORE UPDATE ON personal_ledger_entries
        BEGIN
            SELECT RAISE(ABORT, 'personal ledger entries are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_personal_ledger_entries_no_delete
        BEFORE DELETE ON personal_ledger_entries
        BEGIN
            SELECT RAISE(ABORT, 'personal ledger entries are immutable');
        END
        """
    )


def _create_sqlite_channel_cost_personal_workspace_guard() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_personal_workspace_fk")
    op.execute(
        "CREATE TRIGGER trg_channel_cost_personal_workspace_fk "
        "BEFORE INSERT ON channel_cost_entries "
        "WHEN NEW.personal_workspace_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM personal_workspaces "
        "WHERE id = NEW.personal_workspace_id) BEGIN "
        "SELECT RAISE(ABORT, 'invalid personal workspace'); END"
    )


def _reconcile_existing_metadata() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name

    with op.batch_alter_table("personal_ledger_entries") as batch:
        batch.alter_column(
            "kind",
            existing_type=sa.String(length=20),
            type_=ledger_kind,
            existing_nullable=False,
        )
    if dialect == "sqlite":
        _create_sqlite_personal_ledger_guards()

    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_personal_workspace_fk")
    with op.batch_alter_table("personal_workspaces") as batch:
        batch.drop_constraint("uq_personal_workspace_user", type_="unique")
    if dialect == "sqlite":
        _create_sqlite_channel_cost_personal_workspace_guard()

    for index_name, table_name in (
        ("ix_channel_cost_entries_personal_workspace_id", "channel_cost_entries"),
        ("ix_generation_tasks_personal_workspace_id", "generation_tasks"),
        ("ix_task_artifacts_personal_workspace_id", "task_artifacts"),
        ("ix_task_timeout_events_personal_workspace_id", "task_timeout_events"),
    ):
        op.create_index(index_name, table_name, ["personal_workspace_id"])


def _restore_0036_metadata_shape() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    for index_name, table_name in (
        ("ix_task_timeout_events_personal_workspace_id", "task_timeout_events"),
        ("ix_task_artifacts_personal_workspace_id", "task_artifacts"),
        ("ix_generation_tasks_personal_workspace_id", "generation_tasks"),
        ("ix_channel_cost_entries_personal_workspace_id", "channel_cost_entries"),
    ):
        op.drop_index(index_name, table_name=table_name)

    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_personal_workspace_fk")
    with op.batch_alter_table("personal_workspaces") as batch:
        batch.create_unique_constraint(
            "uq_personal_workspace_user",
            ["user_id"],
        )
    if dialect == "sqlite":
        _create_sqlite_channel_cost_personal_workspace_guard()

    with op.batch_alter_table("personal_ledger_entries") as batch:
        batch.alter_column(
            "kind",
            existing_type=ledger_kind,
            type_=sa.String(length=20),
            existing_nullable=False,
        )
    if dialect == "sqlite":
        _create_sqlite_personal_ledger_guards()


def _create_auth_history_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_account_security_event_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'account security events are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_account_security_events_immutable
            BEFORE UPDATE OR DELETE ON account_security_events
            FOR EACH ROW EXECUTE FUNCTION reject_account_security_event_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_account_security_events_no_truncate
            BEFORE TRUNCATE ON account_security_events
            FOR EACH STATEMENT EXECUTE FUNCTION reject_account_security_event_mutation()
            """
        )
        op.execute(
            """
            CREATE FUNCTION reject_company_invitation_delete()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'company invitations cannot be deleted';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_company_invitations_no_delete
            BEFORE DELETE ON company_invitations
            FOR EACH ROW EXECUTE FUNCTION reject_company_invitation_delete()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_company_invitations_no_truncate
            BEFORE TRUNCATE ON company_invitations
            FOR EACH STATEMENT EXECUTE FUNCTION reject_company_invitation_delete()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_account_security_events_no_update
            BEFORE UPDATE ON account_security_events
            BEGIN
                SELECT RAISE(ABORT, 'account security events are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_account_security_events_no_delete
            BEFORE DELETE ON account_security_events
            BEGIN
                SELECT RAISE(ABORT, 'account security events are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_company_invitations_no_delete
            BEFORE DELETE ON company_invitations
            BEGIN
                SELECT RAISE(ABORT, 'company invitations cannot be deleted');
            END
            """
        )


def _drop_auth_history_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_account_security_events_no_truncate "
            "ON account_security_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_account_security_events_immutable "
            "ON account_security_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_company_invitations_no_truncate "
            "ON company_invitations"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_company_invitations_no_delete "
            "ON company_invitations"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_account_security_event_mutation()")
        op.execute("DROP FUNCTION IF EXISTS reject_company_invitation_delete()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_account_security_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_account_security_events_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_company_invitations_no_delete")


def _apply_runtime_acl() -> None:
    connection = op.get_bind()
    database_name = connection.scalar(sa.text("SELECT current_database()"))
    if not isinstance(database_name, str) or not database_name:
        raise RuntimeError("Platform database identity is unavailable")
    quoted_database = connection.dialect.identifier_preparer.quote(database_name)
    runtime_process_roles = tuple(policy_v2.PRIVILEGES_BY_PROCESS)
    runtime_database_roles = tuple(
        policy_v2.DATABASE_ROLE_BY_PROCESS[process_role]
        for process_role in runtime_process_roles
    )

    _execute(
        f"REVOKE CONNECT, CREATE, TEMPORARY ON DATABASE {quoted_database} FROM PUBLIC"
    )
    _execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
    _execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    _execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    _execute("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")

    for database_role in runtime_database_roles:
        quoted_role = _quote_identifier(database_role)
        _execute(
            f"REVOKE ALL PRIVILEGES ON DATABASE {quoted_database} FROM {quoted_role}"
        )
        _execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {quoted_role}")
        _execute(
            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
            f"FROM {quoted_role}"
        )
        _execute(
            "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
            f"FROM {quoted_role}"
        )
        _execute(
            "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public "
            f"FROM {quoted_role}"
        )
        _execute(f"GRANT CONNECT ON DATABASE {quoted_database} TO {quoted_role}")
        _execute(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")

    quoted_migration_role = _quote_identifier(policy_v2.MIGRATION_DATABASE_ROLE)
    _execute(
        "ALTER DEFAULT PRIVILEGES "
        f"FOR ROLE {quoted_migration_role} IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
    )
    _execute(
        "ALTER DEFAULT PRIVILEGES "
        f"FOR ROLE {quoted_migration_role} IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC"
    )
    _execute(
        "ALTER DEFAULT PRIVILEGES "
        f"FOR ROLE {quoted_migration_role} "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )

    for process_role, privileges_by_table in policy_v2.PRIVILEGES_BY_PROCESS.items():
        quoted_role = _quote_identifier(
            policy_v2.DATABASE_ROLE_BY_PROCESS[process_role]
        )
        for table_name, privileges in sorted(privileges_by_table.items()):
            privilege_list = ", ".join(sorted(privileges))
            _execute(
                f"GRANT {privilege_list} ON TABLE public."
                f"{_quote_identifier(table_name)} TO {quoted_role}"
            )
        _execute(
            "GRANT SELECT ON TABLE public.alembic_version "
            f"TO {quoted_role}"
        )


def _create_auth_schema() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "status",
                user_status,
                nullable=False,
                server_default="ACTIVE",
            )
        )
        batch.add_column(
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "auth_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_users_auth_version",
            "auth_version >= 1",
        )
        batch.create_check_constraint(
            "ck_users_status_deactivated",
            "(status = 'DEACTIVATED' AND deactivated_at IS NOT NULL) OR "
            "(status <> 'DEACTIVATED' AND deactivated_at IS NULL)",
        )

    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "status",
            existing_type=user_status,
            server_default=None,
        )
        batch.alter_column(
            "auth_version",
            existing_type=sa.Integer(),
            server_default=None,
        )

    op.create_index(
        "uq_users_email_casefold",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("email_at_link", sa.String(length=320), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_external_identity_issuer_subject",
        ),
    )
    op.create_index(
        "ix_external_identities_user_id",
        "external_identities",
        ["user_id"],
    )
    op.create_index(
        "ix_external_identity_user",
        "external_identities",
        ["user_id", "created_at"],
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("external_identity_id", sa.String(length=36), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("amr", sa.JSON(), nullable=False),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=120), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        *_sha256_check_constraints(
            "token_digest",
            constraint_name="ck_auth_session_token_digest_sha256",
        ),
        *_sha256_check_constraints(
            "csrf_digest",
            constraint_name="ck_auth_session_csrf_digest_sha256",
        ),
        sa.CheckConstraint(
            "auth_version >= 1",
            name="ck_auth_session_auth_version",
        ),
        sa.ForeignKeyConstraint(
            ["external_identity_id"],
            ["external_identities.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index(
        "ix_auth_sessions_external_identity_id",
        "auth_sessions",
        ["external_identity_id"],
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_session_user_expiry",
        "auth_sessions",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_auth_session_active",
        "auth_sessions",
        ["revoked_at", "expires_at"],
    )

    op.create_table(
        "oidc_login_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=160), nullable=False),
        sa.Column("code_verifier", sa.String(length=160), nullable=False),
        sa.Column("return_to", sa.String(length=2048), nullable=False),
        sa.Column("prompt", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        *_sha256_check_constraints(
            "state_digest",
            constraint_name="ck_oidc_login_state_digest_sha256",
        ),
        *_sha256_check_constraints(
            "ip_hash",
            constraint_name="ck_oidc_login_ip_hash_sha256",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_digest"),
    )
    op.create_index(
        "ix_oidc_login_expiry",
        "oidc_login_transactions",
        ["expires_at", "consumed_at"],
    )

    op.create_table(
        "account_security_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("outcome", audit_outcome, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=True),
        sa.Column("subject_hash", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_sha256_check_constraints(
            "subject_hash",
            constraint_name="ck_account_security_event_subject_hash_sha256",
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_security_events_user_id",
        "account_security_events",
        ["user_id"],
    )
    op.create_index(
        "ix_account_security_event_user_created",
        "account_security_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_account_security_event_type_created",
        "account_security_events",
        ["event_type", "created_at"],
    )

    op.create_table(
        "company_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("primary_role", sa.String(length=40), nullable=False),
        sa.Column("status", company_invitation_status, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("accepted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_sha256_check_constraints(
            "token_digest",
            constraint_name="ck_company_invitation_token_digest_sha256",
        ),
        *_sha256_check_constraints(
            "request_fingerprint",
            constraint_name="ck_company_invitation_request_fingerprint_sha256",
        ),
        sa.CheckConstraint(
            "primary_role IN ('operator', 'team_lead')",
            name="ck_company_invitation_primary_role",
        ),
        sa.CheckConstraint(
            "(status = 'ACCEPTED' AND accepted_by_user_id IS NOT NULL "
            "AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status = 'REVOKED' AND accepted_by_user_id IS NULL "
            "AND accepted_at IS NULL AND revoked_at IS NOT NULL) OR "
            "(status IN ('PENDING', 'EXPIRED') AND accepted_by_user_id IS NULL "
            "AND accepted_at IS NULL AND revoked_at IS NULL)",
            name="ck_company_invitation_status_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_company_invitation_idempotency",
        ),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index(
        "ix_company_invitations_accepted_by_user_id",
        "company_invitations",
        ["accepted_by_user_id"],
    )
    op.create_index(
        "ix_company_invitations_company_id",
        "company_invitations",
        ["company_id"],
    )
    op.create_index(
        "ix_company_invitations_created_by_user_id",
        "company_invitations",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_company_invitation_company_status",
        "company_invitations",
        ["company_id", "status"],
    )
    op.create_index(
        "ix_company_invitation_email_status",
        "company_invitations",
        ["email", "status"],
    )
    _create_auth_history_guards()


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v2()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v2)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v2,
        )

    _validate_user_email_casefold_inventory()
    _reconcile_existing_metadata()
    _create_auth_schema()

    if protected_postgres:
        _apply_runtime_acl()
        evidence = collect_platform_database_evidence(connection, policy=policy_v2)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v2,
        )


def downgrade() -> None:
    _drop_auth_history_guards()
    op.drop_table("company_invitations")
    op.drop_table("account_security_events")
    op.drop_table("oidc_login_transactions")
    op.drop_table("auth_sessions")
    op.drop_table("external_identities")
    op.drop_index("uq_users_email_casefold", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_status_deactivated", type_="check")
        batch.drop_constraint("ck_users_auth_version", type_="check")
        batch.drop_column("deactivated_at")
        batch.drop_column("last_login_at")
        batch.drop_column("auth_version")
        batch.drop_column("email_verified_at")
        batch.drop_column("status")
    _restore_0036_metadata_shape()
