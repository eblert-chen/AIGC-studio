"""Add revocable anonymous read-only share links.

Revision ID: 0062_share_links
Revises: 0061_released_exec_block

A share link is a bearer credential: anyone holding the URL can read the
resource until it expires or is revoked. That makes two properties
non-negotiable at the storage layer, not merely in application code:

1. The raw token is never persisted. Only a peppered digest is stored, so a
   database leak cannot be replayed as a working link. The digest column is
   shape-checked like every other digest in this schema.

2. The link's identity is frozen. A link must never be re-pointed at another
   resource or silently escalated from preview to a broader permission, so
   token, scope, resource and permission are append-only. Only lifecycle
   fields (expiry, revocation, hit counters) may change. Revocation is a
   state change, never an edit.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0062_share_links"
down_revision: str | None = "0061_released_exec_block"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_POSTGRES_DIGEST = "token_digest ~ '^[0-9a-f]{64}$'"
_SQLITE_DIGEST = (
    "length(token_digest) = 64 AND lower(token_digest) = token_digest "
    "AND token_digest NOT GLOB '*[^0-9a-f]*'"
)

# Mirrors the model's non-native enum columns exactly; the migration-vs-metadata
# guard compares both the member list and the generated type.
_SHARE_RESOURCE_TYPE = sa.Enum("TASK", name="shareresourcetype", native_enum=False)
_SHARE_PERMISSION = sa.Enum("READ", name="sharepermission", native_enum=False)

_SCOPE = (
    "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
    "(company_id IS NULL AND personal_workspace_id IS NOT NULL)"
)

# Only lifecycle columns remain writable. Everything that decides *what* the
# bearer can see is frozen at insert.
_FROZEN = (
    "token_digest",
    "company_id",
    "personal_workspace_id",
    "resource_type",
    "resource_id",
    "permission",
    "created_by_user_id",
)

_POSTGRES_GUARD = (
    "CREATE OR REPLACE FUNCTION guard_share_link_identity() "
    "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
    + " ".join(
        f"IF NEW.{column} IS DISTINCT FROM OLD.{column} THEN "
        f"RAISE EXCEPTION 'share link {column} is immutable'; END IF; "
        for column in _FROZEN
    )
    + "RETURN NEW; END $$"
)

_SQLITE_WHEN = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _FROZEN)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("personal_workspace_id", sa.String(length=36), nullable=True),
        sa.Column("resource_type", _SHARE_RESOURCE_TYPE, nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("permission", _SHARE_PERMISSION, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _POSTGRES_DIGEST if dialect == "postgresql" else _SQLITE_DIGEST,
            name="ck_share_link_token_digest_sha256",
        ),
        sa.CheckConstraint(_SCOPE, name="ck_share_link_scope"),
        sa.CheckConstraint(
            "access_count >= 0", name="ck_share_link_access_count_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personal_workspace_id"],
            ["personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("token_digest"),
        sa.UniqueConstraint(
            "company_id", "idempotency_key", name="uq_share_link_company_idempotency"
        ),
        sa.UniqueConstraint(
            "personal_workspace_id",
            "idempotency_key",
            name="uq_share_link_personal_idempotency",
        ),
    )
    # token_digest uniqueness is the UniqueConstraint above; the model's
    # `index=True` columns are the remaining ix_share_links_* indexes.
    op.create_index("ix_share_links_company_id", "share_links", ["company_id"], unique=False)
    op.create_index(
        "ix_share_links_personal_workspace_id",
        "share_links",
        ["personal_workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_share_links_created_by_user_id",
        "share_links",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_share_link_company_created", "share_links", ["company_id", "created_at"]
    )
    op.create_index(
        "ix_share_link_personal_created",
        "share_links",
        ["personal_workspace_id", "created_at"],
    )
    op.create_index(
        "ix_share_link_resource", "share_links", ["resource_type", "resource_id"]
    )
    if dialect == "postgresql":
        op.execute(sa.text(_POSTGRES_GUARD))
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_share_link_identity BEFORE UPDATE ON "
                "share_links FOR EACH ROW EXECUTE FUNCTION "
                "guard_share_link_identity()"
            )
        )
    elif dialect == "sqlite":
        op.execute(
            sa.text(
                "CREATE TRIGGER trg_share_link_identity BEFORE UPDATE ON "
                f"share_links WHEN {_SQLITE_WHEN} "
                "BEGIN SELECT RAISE(ABORT, 'share link identity is immutable'); END"
            )
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text("DROP TRIGGER trg_share_link_identity ON share_links"))
        op.execute(sa.text("DROP FUNCTION guard_share_link_identity()"))
    elif dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER trg_share_link_identity"))
    op.drop_index("ix_share_link_resource", table_name="share_links")
    op.drop_index("ix_share_link_personal_created", table_name="share_links")
    op.drop_index("ix_share_link_company_created", table_name="share_links")
    op.drop_index("ix_share_links_created_by_user_id", table_name="share_links")
    op.drop_index("ix_share_links_personal_workspace_id", table_name="share_links")
    op.drop_index("ix_share_links_company_id", table_name="share_links")
    op.drop_table("share_links")
