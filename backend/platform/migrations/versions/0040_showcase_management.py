"""add owner-only, versioned homepage showcase management

Revision ID: 0040_showcase_management
Revises: 0039_new_api_relay_defaults
Create Date: 2026-08-21
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from platform_api import database_privileges_v5 as policy_v5
from platform_api.database_privileges_behavior_v5 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v5,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0040_showcase_management"
down_revision: str | None = "0039_new_api_relay_defaults"
branch_labels: str | None = None
depends_on: str | None = None


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_SHOWCASE_TABLES = (
    "showcase_channels",
    "showcase_draft_items",
    "showcase_media",
    "showcase_publication_events",
    "showcase_release_items",
    "showcase_releases",
)


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("Platform database ACL identifier is invalid")
    return f'"{value}"'


def _sha256_constraints(column: str, prefix: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(
            f"length({column}) = 64 AND lower({column}) = {column} "
            f"AND {column} NOT GLOB '*[^0-9a-f]*'",
            name=prefix,
        ).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(
            f"{column} ~ '^[0-9a-f]{{64}}$'",
            name=prefix,
        ).ddl_if(dialect="postgresql"),
    )


def _create_tables() -> None:
    op.create_table(
        "showcase_media",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_task_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "media_type IN ('image', 'video')", name="ck_showcase_media_type"
        ),
        sa.CheckConstraint(
            "size_bytes > 0", name="ck_showcase_media_size_positive"
        ),
        *_sha256_constraints("sha256", "ck_showcase_media_sha256_hex"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_task_artifact_id"],
            ["task_artifacts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_showcase_media_object_key"),
        sa.UniqueConstraint("sha256", name="uq_showcase_media_sha256"),
        sa.UniqueConstraint(
            "created_by_user_id",
            "idempotency_key",
            name="uq_showcase_media_owner_idempotency",
        ),
        sa.UniqueConstraint(
            "source_task_artifact_id", name="uq_showcase_media_source_artifact"
        ),
    )
    op.create_index(
        "ix_showcase_media_created_by_user_id",
        "showcase_media",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_showcase_media_source_task_artifact_id",
        "showcase_media",
        ["source_task_artifact_id"],
    )

    op.create_table(
        "showcase_releases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("draft_version", sa.BigInteger(), nullable=False),
        sa.Column("publication_version", sa.BigInteger(), nullable=False),
        sa.Column("published_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("source_release_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("release_note", sa.String(length=500), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_showcase_release_version_positive"
        ),
        sa.CheckConstraint(
            "publication_version > 0",
            name="ck_showcase_release_publication_version_positive",
        ),
        sa.CheckConstraint(
            "draft_version >= 0",
            name="ck_showcase_release_draft_version_nonnegative",
        ),
        *_sha256_constraints(
            "manifest_sha256", "ck_showcase_release_manifest_sha256_hex"
        ),
        *_sha256_constraints(
            "request_fingerprint", "ck_showcase_release_request_sha256_hex"
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_release_id"], ["showcase_releases.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_showcase_release_version"),
        sa.UniqueConstraint(
            "publication_version",
            name="uq_showcase_release_publication_version",
        ),
        sa.UniqueConstraint(
            "published_by_user_id",
            "idempotency_key",
            name="uq_showcase_release_owner_idempotency",
        ),
    )
    op.create_index(
        "ix_showcase_releases_published_by_user_id",
        "showcase_releases",
        ["published_by_user_id"],
    )
    op.create_index(
        "ix_showcase_releases_source_release_id",
        "showcase_releases",
        ["source_release_id"],
    )
    op.create_index(
        "ix_showcase_releases_published_at",
        "showcase_releases",
        ["published_at"],
    )

    op.create_table(
        "showcase_channels",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column(
            "draft_version", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "publication_version",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("current_release_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "id = 'home'", name="ck_showcase_channel_singleton"
        ),
        sa.CheckConstraint(
            "draft_version >= 0", name="ck_showcase_channel_draft_nonnegative"
        ),
        sa.CheckConstraint(
            "publication_version >= 0",
            name="ck_showcase_channel_publication_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["current_release_id"], ["showcase_releases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_release_id"),
    )
    op.execute(
        sa.insert(sa.table("showcase_channels", sa.column("id"))).values(id="home")
    )

    op.create_table(
        "showcase_draft_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("section", sa.String(length=24), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("alt_text", sa.String(length=300), nullable=False),
        sa.Column("public_prompt", sa.String(length=2000), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=12), nullable=False),
        sa.Column("is_hero", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sort_order >= 0", name="ck_showcase_draft_sort_nonnegative"
        ),
        sa.CheckConstraint(
            "section IN ('video', 'template', 'challenge')",
            name="ck_showcase_draft_section",
        ),
        sa.CheckConstraint(
            "category IN ('广告魔法', '电影叙事', '风格艺术', '动漫剧场', "
            "'数字人', '教育学习', '商品展示')",
            name="ck_showcase_draft_category",
        ),
        sa.CheckConstraint(
            "aspect_ratio IN ('auto', '1:1', '3:4', '4:3', '9:16', '16:9')",
            name="ck_showcase_draft_aspect_ratio",
        ),
        sa.ForeignKeyConstraint(["media_id"], ["showcase_media.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_showcase_draft_items_media_id", "showcase_draft_items", ["media_id"]
    )
    op.create_index(
        "ix_showcase_draft_active_order",
        "showcase_draft_items",
        ["retired_at", "sort_order", "id"],
    )

    op.create_table(
        "showcase_release_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("release_id", sa.String(length=36), nullable=False),
        sa.Column("source_draft_item_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("section", sa.String(length=24), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("alt_text", sa.String(length=300), nullable=False),
        sa.Column("public_prompt", sa.String(length=2000), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=12), nullable=False),
        sa.Column("is_hero", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name="ck_showcase_release_item_position"
        ),
        sa.CheckConstraint(
            "section IN ('video', 'template', 'challenge')",
            name="ck_showcase_release_item_section",
        ),
        sa.CheckConstraint(
            "category IN ('广告魔法', '电影叙事', '风格艺术', '动漫剧场', "
            "'数字人', '教育学习', '商品展示')",
            name="ck_showcase_release_item_category",
        ),
        sa.CheckConstraint(
            "aspect_ratio IN ('auto', '1:1', '3:4', '4:3', '9:16', '16:9')",
            name="ck_showcase_release_item_aspect_ratio",
        ),
        sa.ForeignKeyConstraint(
            ["release_id"], ["showcase_releases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["media_id"], ["showcase_media.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "release_id", "position", name="uq_showcase_release_item_position"
        ),
        sa.UniqueConstraint(
            "release_id",
            "source_draft_item_id",
            name="uq_showcase_release_item_source",
        ),
    )
    op.create_index(
        "ix_showcase_release_items_release_id",
        "showcase_release_items",
        ["release_id"],
    )

    op.create_table(
        "showcase_publication_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("previous_release_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("expected_draft_version", sa.BigInteger(), nullable=False),
        sa.Column("publication_version", sa.BigInteger(), nullable=False),
        sa.Column("release_note", sa.String(length=500), nullable=False),
        sa.Column(
            "unpublished_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action = 'unpublish'",
            name="ck_showcase_publication_event_action",
        ),
        sa.CheckConstraint(
            "expected_draft_version >= 0",
            name="ck_showcase_publication_event_draft_nonnegative",
        ),
        sa.CheckConstraint(
            "publication_version > 0",
            name="ck_showcase_publication_event_version_positive",
        ),
        *_sha256_constraints(
            "request_fingerprint",
            "ck_showcase_publication_event_request_sha256_hex",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_release_id"],
            ["showcase_releases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_version",
            name="uq_showcase_publication_event_version",
        ),
        sa.UniqueConstraint(
            "actor_user_id",
            "idempotency_key",
            name="uq_showcase_publication_event_owner_idempotency",
        ),
    )
    op.create_index(
        "ix_showcase_publication_events_actor_user_id",
        "showcase_publication_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_showcase_publication_events_previous_release_id",
        "showcase_publication_events",
        ["previous_release_id"],
    )
    op.create_index(
        "ix_showcase_publication_events_unpublished_at",
        "showcase_publication_events",
        ["unpublished_at"],
    )


def _install_immutable_guards() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        schema_name = connection.scalar(text("SELECT current_schema()"))
        if not isinstance(schema_name, str) or not schema_name:
            raise RuntimeError("Platform migration schema is unavailable")
        quoted_schema = connection.dialect.identifier_preparer.quote_schema(
            schema_name
        )
        guard_function = (
            f"{quoted_schema}.{_quote_identifier('showcase_immutable_guard_v1')}"
        )
        op.execute(
            text(
                f"CREATE FUNCTION {guard_function}() "
                "RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER "
                "SET search_path = pg_catalog AS $$ BEGIN "
                "RAISE EXCEPTION 'showcase published records are immutable' "
                "USING ERRCODE = '55000'; END; $$"
            )
        )
        op.execute(
            text(
                "REVOKE ALL PRIVILEGES ON FUNCTION "
                f"{guard_function}() FROM PUBLIC"
            )
        )
        for table_name in (
            "showcase_media",
            "showcase_publication_events",
            "showcase_releases",
            "showcase_release_items",
        ):
            op.execute(
                text(
                    f"CREATE TRIGGER {_quote_identifier('trg_' + table_name + '_immutable')} "
                    "BEFORE UPDATE OR DELETE OR TRUNCATE ON "
                    f"{quoted_schema}.{_quote_identifier(table_name)} "
                    f"FOR EACH STATEMENT EXECUTE FUNCTION {guard_function}()"
                )
            )
    elif connection.dialect.name == "sqlite":
        for table_name in (
            "showcase_media",
            "showcase_publication_events",
            "showcase_releases",
            "showcase_release_items",
        ):
            for operation in ("UPDATE", "DELETE"):
                trigger_name = f"trg_{table_name}_{operation.lower()}_immutable"
                op.execute(
                    text(
                        f'CREATE TRIGGER "{trigger_name}" BEFORE {operation} ON "{table_name}" '
                        "BEGIN SELECT RAISE(ABORT, 'showcase published records are immutable'); END"
                    )
                )


def _apply_protected_acl() -> None:
    api_role = policy_v5.DATABASE_ROLE_BY_PROCESS["platform-api"]
    runtime_roles = {
        policy_v5.DATABASE_ROLE_BY_PROCESS[process]
        for process in policy_v5.PRIVILEGES_BY_PROCESS
    }
    for table_name in _SHOWCASE_TABLES:
        for role in runtime_roles:
            op.execute(
                text(
                    f"REVOKE ALL PRIVILEGES ON TABLE public.{_quote_identifier(table_name)} "
                    f"FROM {_quote_identifier(role)}"
                )
            )
        privileges = policy_v5.PRIVILEGES_BY_PROCESS["platform-api"][table_name]
        op.execute(
            text(
                f"GRANT {', '.join(sorted(privileges))} ON TABLE "
                f"public.{_quote_identifier(table_name)} TO {_quote_identifier(api_role)}"
            )
        )


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v5()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v5)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v5,
        )
    _create_tables()
    _install_immutable_guards()
    if protected_postgres:
        _apply_protected_acl()
        evidence = collect_platform_database_evidence(connection, policy=policy_v5)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v5,
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        schema_name = connection.scalar(text("SELECT current_schema()"))
        if not isinstance(schema_name, str) or not schema_name:
            raise RuntimeError("Platform migration schema is unavailable")
        quoted_schema = connection.dialect.identifier_preparer.quote_schema(
            schema_name
        )
        guard_function = (
            f"{quoted_schema}.{_quote_identifier('showcase_immutable_guard_v1')}"
        )
        op.execute(text(f"DROP FUNCTION {guard_function}() CASCADE"))
    for table_name in (
        "showcase_publication_events",
        "showcase_release_items",
        "showcase_draft_items",
        "showcase_channels",
        "showcase_releases",
        "showcase_media",
    ):
        op.drop_table(table_name)
