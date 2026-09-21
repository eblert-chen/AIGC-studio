"""add durable company entitlement batch idempotency journal

Revision ID: 0042_entitlement_batch_journal
Revises: 0041_model_capability_releases
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from platform_api import database_privileges_v7 as policy_v7
from platform_api.database_privileges_behavior_v7 import (
    attest_platform_database_connection,
    collect_platform_database_evidence,
    protected_platform_runtime_requested_v7,
    validate_platform_database_acl_evidence,
    validate_platform_migration_source_state,
)


revision: str = "0042_entitlement_batch_journal"
down_revision: str | None = "0041_model_capability_releases"
branch_labels: str | None = None
depends_on: str | None = None


_JOURNAL = "company_entitlement_batch_journals"


def _legacy_digest(namespace: str, idempotency_key: str) -> str:
    return hashlib.sha256(
        f"{namespace}:{idempotency_key}".encode("utf-8")
    ).hexdigest()


def _legacy_created_at(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _legacy_summary(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _freeze_legacy_audit_keys(connection) -> None:
    rows = connection.execute(
        text(
            "SELECT id, target_id, actor_user_id, after_summary, created_at "
            "FROM audit_logs "
            "WHERE action = 'company.entitlements.batch' "
            "AND target_type = 'entitlement_batch' "
            "ORDER BY target_id, created_at, id"
        )
    ).mappings().all()
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[str(row["target_id"])].append(row)
    if not grouped:
        return
    journal_table = sa.table(
        _JOURNAL,
        sa.column("id", sa.String(length=36)),
        sa.column("idempotency_key", sa.String(length=120)),
        sa.column("actor_user_id", sa.String(length=36)),
        sa.column("request_sha256", sa.String(length=64)),
        sa.column("expected_snapshot", sa.String(length=64)),
        sa.column("state", sa.String(length=16)),
        sa.column("result_payload", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    frozen_rows = []
    for idempotency_key, legacy_rows in sorted(grouped.items()):
        first = legacy_rows[0]
        actor_ids = sorted({str(row["actor_user_id"]) for row in legacy_rows})
        summaries = [_legacy_summary(row["after_summary"]) for row in legacy_rows]
        request_hashes = sorted(
            {
                str(summary.get("request_hash", "")).strip()
                for summary in summaries
                if str(summary.get("request_hash", "")).strip()
            }
        )
        legacy_audit_set_sha256 = hashlib.sha256(
            json.dumps(
                [
                    [
                        str(row["id"]),
                        str(row["actor_user_id"]),
                        str(summary.get("request_hash", "")),
                    ]
                    for row, summary in zip(legacy_rows, summaries, strict=True)
                ],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        frozen_rows.append(
            {
                "id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "ai-video:legacy-entitlement-batch:"
                        + idempotency_key,
                    )
                ),
                "idempotency_key": idempotency_key,
                # The row is a global reservation, not a successful replay.
                # Choose the actor deterministically only to satisfy the FK;
                # every caller is rejected while state remains ``frozen``.
                "actor_user_id": actor_ids[0],
                # The old audit hash was derived from mutable changes and
                # cannot prove the new raw HTTP intent. Reserve the key with
                # a deterministic non-matching digest and fail closed.
                "request_sha256": _legacy_digest(
                    "legacy-request-frozen", idempotency_key
                ),
                "expected_snapshot": _legacy_digest(
                    "legacy-snapshot-frozen", idempotency_key
                ),
                "state": "frozen",
                "result_payload": {
                    "legacy_frozen": True,
                    "audit_count": len(legacy_rows),
                    "actor_count": len(actor_ids),
                    "request_hash_count": len(request_hashes),
                    "conflicting_legacy_rows": (
                        len(legacy_rows) > 1
                        or len(actor_ids) > 1
                        or len(request_hashes) > 1
                    ),
                    "legacy_audit_set_sha256": legacy_audit_set_sha256,
                    "reason": (
                        "legacy audit rows cannot prove the stable raw intent; "
                        "automatic replay is forbidden"
                    ),
                },
                "created_at": _legacy_created_at(first["created_at"]),
                "updated_at": _legacy_created_at(first["created_at"]),
            }
        )
    op.bulk_insert(journal_table, frozen_rows)


def _apply_journal_acl() -> None:
    api_role = policy_v7.DATABASE_ROLE_BY_PROCESS["platform-api"]
    runtime_roles = {
        policy_v7.DATABASE_ROLE_BY_PROCESS[process]
        for process in policy_v7.PRIVILEGES_BY_PROCESS
    }
    for role in runtime_roles:
        op.execute(
            text(
                "REVOKE ALL PRIVILEGES ON TABLE "
                f"public.{_JOURNAL} FROM {role}"
            )
        )
    privileges = policy_v7.PRIVILEGES_BY_PROCESS["platform-api"][_JOURNAL]
    op.execute(
        text(
            f"GRANT {', '.join(sorted(privileges))} ON TABLE "
            f"public.{_JOURNAL} TO {api_role}"
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    protected_postgres = (
        connection.dialect.name == "postgresql"
        and protected_platform_runtime_requested_v7()
    )
    if protected_postgres:
        validate_platform_migration_source_state(connection, policy=policy_v7)
        attest_platform_database_connection(
            connection,
            "migration",
            require_runtime_acl=False,
            require_head=False,
            policy=policy_v7,
        )

    op.create_table(
        _JOURNAL,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_snapshot", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'completed', 'frozen')",
            name="ck_company_entitlement_batch_state",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64",
            name="ck_company_entitlement_batch_request_sha256",
        ),
        sa.CheckConstraint(
            "length(expected_snapshot) = 64",
            name="ck_company_entitlement_batch_snapshot_sha256",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND result_payload IS NULL) OR "
            "(state IN ('completed', 'frozen') "
            "AND result_payload IS NOT NULL)",
            name="ck_company_entitlement_batch_result_shape",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_company_entitlement_batch_idempotency",
        ),
    )
    op.create_index(
        "ix_company_entitlement_batch_actor_created",
        _JOURNAL,
        ["actor_user_id", "created_at"],
    )
    _freeze_legacy_audit_keys(connection)

    # The claim row and all entitlement/audit mutations share one transaction.
    # A committed pending row is therefore anomalous evidence of an unsafe
    # external/manual transaction boundary; runtime treats it as outcome
    # unknown and forbids automatic re-execution.
    if protected_postgres:
        _apply_journal_acl()
        evidence = collect_platform_database_evidence(connection, policy=policy_v7)
        validate_platform_database_acl_evidence(
            evidence,
            require_head=False,
            policy=policy_v7,
        )


def downgrade() -> None:
    op.drop_table(_JOURNAL)
