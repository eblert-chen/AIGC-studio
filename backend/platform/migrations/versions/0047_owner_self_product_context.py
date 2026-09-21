"""Link a Platform Owner self workspace and rotate product-context sessions.

Revision ID: 0047_owner_self_product_context
Revises: 0046_company_points_billing_v2
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0047_owner_self_product_context"
down_revision: str | None = "0046_company_points_billing_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONTEXTS = "'personal', 'company', 'platform'"


def _drop_sqlite_workspace_references() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_channel_cost_personal_workspace_fk")


def _create_sqlite_workspace_references() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_channel_cost_personal_workspace_fk "
            "BEFORE INSERT ON channel_cost_entries "
            "WHEN NEW.personal_workspace_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM personal_workspaces WHERE id = NEW.personal_workspace_id) "
            "BEGIN SELECT RAISE(ABORT, 'invalid personal workspace'); END"
        )


def _drop_session_context_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_auth_session_product_context_insert ON auth_sessions")
        op.execute("DROP TRIGGER IF EXISTS trg_auth_session_product_context_update ON auth_sessions")
        op.execute("DROP FUNCTION IF EXISTS enforce_auth_session_product_context()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_auth_session_product_context_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_auth_session_product_context_update")


def _create_session_context_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION enforce_auth_session_product_context()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE resolved_account_type text;
            BEGIN
                SELECT account_type INTO resolved_account_type
                FROM users WHERE id = NEW.user_id;
                IF NOT (
                    (resolved_account_type = 'PERSONAL' AND NEW.active_product_context = 'personal')
                    OR (resolved_account_type = 'COMPANY' AND NEW.active_product_context = 'company')
                    OR (resolved_account_type = 'PLATFORM_ADMIN' AND NEW.active_product_context = 'platform')
                    OR (resolved_account_type = 'PLATFORM_ADMIN' AND NEW.active_product_context = 'personal'
                        AND EXISTS (SELECT 1 FROM personal_workspaces workspace
                            WHERE workspace.user_id = NEW.user_id AND workspace.active
                            AND workspace.owner_self_identity_id = NEW.external_identity_id))
                ) THEN
                    RAISE EXCEPTION 'auth session product context does not match principal'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE'
                   AND NEW.active_product_context IS DISTINCT FROM OLD.active_product_context THEN
                    RAISE EXCEPTION 'auth session product context is immutable'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER trg_auth_session_product_context_insert BEFORE INSERT ON auth_sessions "
            "FOR EACH ROW EXECUTE FUNCTION enforce_auth_session_product_context()"
        )
        op.execute(
            "CREATE TRIGGER trg_auth_session_product_context_update "
            "BEFORE UPDATE OF active_product_context, user_id, external_identity_id ON auth_sessions "
            "FOR EACH ROW EXECUTE FUNCTION enforce_auth_session_product_context()"
        )
        op.execute(
            "REVOKE ALL PRIVILEGES ON FUNCTION enforce_auth_session_product_context() FROM PUBLIC"
        )
    elif dialect == "sqlite":
        valid = (
            "(COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') = 'PERSONAL' "
            "AND NEW.active_product_context = 'personal') OR "
            "(COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') = 'COMPANY' "
            "AND NEW.active_product_context = 'company') OR "
            "(COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') = 'PLATFORM_ADMIN' "
            "AND NEW.active_product_context = 'platform') OR "
            "(COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') = 'PLATFORM_ADMIN' "
            "AND NEW.active_product_context = 'personal' AND EXISTS (SELECT 1 FROM personal_workspaces workspace "
            "WHERE workspace.user_id = NEW.user_id AND workspace.active = 1 "
            "AND workspace.owner_self_identity_id = NEW.external_identity_id))"
        )
        op.execute(
            "CREATE TRIGGER trg_auth_session_product_context_insert BEFORE INSERT ON auth_sessions "
            "WHEN NOT (" + valid + ") BEGIN SELECT RAISE(ABORT, "
            "'auth session product context does not match principal'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_auth_session_product_context_update "
            "BEFORE UPDATE OF active_product_context, user_id, external_identity_id ON auth_sessions "
            "WHEN NEW.active_product_context <> OLD.active_product_context OR NOT (" + valid + ") "
            "BEGIN SELECT RAISE(ABORT, 'auth session product context is immutable'); END"
        )


def _drop_partition_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_personal_workspace_account_type_update ON personal_workspaces")
        op.execute("DROP TRIGGER IF EXISTS trg_personal_workspace_account_type_insert ON personal_workspaces")
        op.execute("DROP FUNCTION IF EXISTS enforce_personal_workspace_account_type()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_personal_workspace_account_type_update")
        op.execute("DROP TRIGGER IF EXISTS trg_personal_workspace_account_type_insert")


def _create_owner_self_partition_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION enforce_personal_workspace_account_type()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                resolved_account_type text;
                linked_identity_user_id text;
            BEGIN
                SELECT account_type INTO resolved_account_type
                FROM users WHERE id = NEW.user_id;
                IF NEW.owner_self_identity_id IS NOT NULL THEN
                    SELECT user_id INTO linked_identity_user_id
                    FROM external_identities WHERE id = NEW.owner_self_identity_id;
                END IF;
                IF resolved_account_type = 'PERSONAL' THEN
                    IF NEW.owner_self_identity_id IS NOT NULL THEN
                        RAISE EXCEPTION 'standard personal workspace cannot carry owner-self identity'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF resolved_account_type = 'PLATFORM_ADMIN' THEN
                    IF NEW.owner_self_identity_id IS NULL
                       OR linked_identity_user_id IS DISTINCT FROM NEW.user_id THEN
                        RAISE EXCEPTION 'platform owner-self workspace requires its linked identity'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'personal workspace requires a personal product principal'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE'
                   AND OLD.owner_self_identity_id IS NOT NULL
                   AND NEW.owner_self_identity_id IS DISTINCT FROM OLD.owner_self_identity_id THEN
                    RAISE EXCEPTION 'owner-self identity link is immutable'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER trg_personal_workspace_account_type_insert "
            "BEFORE INSERT ON personal_workspaces FOR EACH ROW "
            "EXECUTE FUNCTION enforce_personal_workspace_account_type()"
        )
        op.execute(
            "CREATE TRIGGER trg_personal_workspace_account_type_update "
            "BEFORE UPDATE OF user_id, active, owner_self_identity_id ON personal_workspaces "
            "FOR EACH ROW EXECUTE FUNCTION enforce_personal_workspace_account_type()"
        )
        op.execute(
            "REVOKE ALL PRIVILEGES ON FUNCTION enforce_personal_workspace_account_type() FROM PUBLIC"
        )
    elif dialect == "sqlite":
        invalid_expression = (
            "(COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') = 'PERSONAL' "
            "AND NEW.owner_self_identity_id IS NOT NULL) OR "
            "(COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') = 'PLATFORM_ADMIN' "
            "AND (NEW.owner_self_identity_id IS NULL OR COALESCE((SELECT user_id FROM external_identities "
            "WHERE id = NEW.owner_self_identity_id), '') <> NEW.user_id)) OR "
            "COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') "
            "NOT IN ('PERSONAL', 'PLATFORM_ADMIN')"
        )
        op.execute(
            "CREATE TRIGGER trg_personal_workspace_account_type_insert "
            "BEFORE INSERT ON personal_workspaces WHEN " + invalid_expression +
            " BEGIN SELECT RAISE(ABORT, 'personal workspace product principal mismatch'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_personal_workspace_account_type_update "
            "BEFORE UPDATE OF user_id, active, owner_self_identity_id ON personal_workspaces WHEN (" +
            invalid_expression +
            ") OR (OLD.owner_self_identity_id IS NOT NULL AND "
            "NEW.owner_self_identity_id IS NOT OLD.owner_self_identity_id) "
            "BEGIN SELECT RAISE(ABORT, 'personal workspace product principal mismatch'); END"
        )
    else:  # pragma: no cover
        raise RuntimeError(f"unsupported Platform database dialect: {dialect}")


def _create_legacy_partition_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION enforce_personal_workspace_account_type()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE resolved_account_type text;
            BEGIN
                SELECT account_type INTO resolved_account_type
                FROM users WHERE id = NEW.user_id;
                IF resolved_account_type IS DISTINCT FROM 'PERSONAL' THEN
                    RAISE EXCEPTION 'personal workspace requires a PERSONAL account'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER trg_personal_workspace_account_type_insert "
            "BEFORE INSERT ON personal_workspaces FOR EACH ROW "
            "EXECUTE FUNCTION enforce_personal_workspace_account_type()"
        )
        op.execute(
            "CREATE TRIGGER trg_personal_workspace_account_type_update "
            "BEFORE UPDATE OF user_id, active ON personal_workspaces FOR EACH ROW "
            "EXECUTE FUNCTION enforce_personal_workspace_account_type()"
        )
        op.execute(
            "REVOKE ALL PRIVILEGES ON FUNCTION enforce_personal_workspace_account_type() FROM PUBLIC"
        )
    elif dialect == "sqlite":
        for suffix, operation in (
            ("insert", "INSERT"),
            ("update", "UPDATE OF user_id, active"),
        ):
            op.execute(
                f"CREATE TRIGGER trg_personal_workspace_account_type_{suffix} "
                f"BEFORE {operation} ON personal_workspaces "
                "WHEN COALESCE((SELECT account_type FROM users WHERE id = NEW.user_id), '') "
                "<> 'PERSONAL' BEGIN SELECT RAISE(ABORT, "
                "'personal workspace requires a PERSONAL account'); END"
            )


def upgrade() -> None:
    _drop_partition_guards()
    _drop_sqlite_workspace_references()
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(
            sa.Column(
                "active_product_context",
                sa.String(length=16),
                nullable=True,
                server_default=sa.text("'personal'"),
            )
        )
    op.execute(
        "UPDATE auth_sessions SET active_product_context = CASE "
        "WHEN EXISTS (SELECT 1 FROM users WHERE users.id = auth_sessions.user_id "
        "AND users.account_type = 'PLATFORM_ADMIN') THEN 'platform' "
        "WHEN EXISTS (SELECT 1 FROM users WHERE users.id = auth_sessions.user_id "
        "AND users.account_type = 'COMPANY') THEN 'company' ELSE 'personal' END"
    )
    with op.batch_alter_table("auth_sessions") as batch:
        batch.alter_column(
            "active_product_context",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default=sa.text("'personal'"),
        )
        batch.create_check_constraint(
            "ck_auth_session_product_context",
            f"active_product_context IN ({_CONTEXTS})",
        )

    with op.batch_alter_table("personal_workspaces") as batch:
        batch.add_column(sa.Column("owner_self_identity_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_personal_workspace_owner_self_identity",
            "external_identities",
            ["owner_self_identity_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_personal_workspace_owner_self_identity",
            ["owner_self_identity_id"],
        )
        batch.create_index(
            "ix_personal_workspaces_owner_self_identity_id",
            ["owner_self_identity_id"],
            unique=False,
        )

    op.create_table(
        "auth_product_context_switches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_session_id", sa.String(length=36), nullable=False),
        sa.Column("target_session_id", sa.String(length=36), nullable=False),
        sa.Column("source_context", sa.String(length=16), nullable=False),
        sa.Column("target_context", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND lower(request_fingerprint) = request_fingerprint "
            "AND request_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_auth_product_context_switch_fingerprint_sha256",
        ).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_auth_product_context_switch_fingerprint_sha256",
        ).ddl_if(dialect="postgresql"),
        sa.CheckConstraint(
            f"source_context IN ({_CONTEXTS})",
            name="ck_auth_product_context_switch_source",
        ),
        sa.CheckConstraint(
            f"target_context IN ({_CONTEXTS})",
            name="ck_auth_product_context_switch_target",
        ),
        sa.CheckConstraint(
            "source_context <> target_context",
            name="ck_auth_product_context_switch_distinct",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_session_id"], ["auth_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_session_id"], ["auth_sessions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_auth_product_context_switch_user_key"),
        sa.UniqueConstraint("source_session_id", name="uq_auth_product_context_switch_source_session"),
        sa.UniqueConstraint("target_session_id", name="uq_auth_product_context_switch_target_session"),
    )
    op.create_index(
        "ix_auth_product_context_switch_user_created",
        "auth_product_context_switches",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_auth_product_context_switches_user_id",
        "auth_product_context_switches",
        ["user_id"],
    )
    _create_owner_self_partition_guards()
    _create_sqlite_workspace_references()
    _create_session_context_guards()


def downgrade() -> None:
    _drop_session_context_guards()
    _drop_partition_guards()
    _drop_sqlite_workspace_references()
    # A rollback closes the linked surface but preserves every wallet, task and
    # artifact row. Security events remain as durable evidence of prior use.
    op.execute(
        "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP), "
        "revoked_reason = COALESCE(revoked_reason, 'product_context_rollback') "
        "WHERE active_product_context = 'personal' AND EXISTS ("
        "SELECT 1 FROM users WHERE users.id = auth_sessions.user_id "
        "AND users.account_type = 'PLATFORM_ADMIN')"
    )
    op.execute(
        "UPDATE personal_workspaces SET active = false, updated_at = CURRENT_TIMESTAMP "
        "WHERE owner_self_identity_id IS NOT NULL"
    )
    op.drop_index("ix_auth_product_context_switches_user_id", table_name="auth_product_context_switches")
    op.drop_index("ix_auth_product_context_switch_user_created", table_name="auth_product_context_switches")
    op.drop_table("auth_product_context_switches")
    with op.batch_alter_table("personal_workspaces") as batch:
        batch.drop_index("ix_personal_workspaces_owner_self_identity_id")
        batch.drop_constraint("uq_personal_workspace_owner_self_identity", type_="unique")
        batch.drop_constraint("fk_personal_workspace_owner_self_identity", type_="foreignkey")
        batch.drop_column("owner_self_identity_id")
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_constraint("ck_auth_session_product_context", type_="check")
        batch.drop_column("active_product_context")
    _create_legacy_partition_guards()
    _create_sqlite_workspace_references()
