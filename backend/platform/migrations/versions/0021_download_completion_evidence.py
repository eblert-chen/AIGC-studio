"""Require source-signed evidence for trusted download completion.

Revision ID: 0021_download_completion_proof
Revises: 0020_publishing
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0021_download_completion_proof"
down_revision: str | None = "0020_publishing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_HEX_REMAINDER = (
    "replace(replace(replace(replace(replace(replace("
    "replace(replace(replace(replace(replace(replace(replace("
    "replace(replace(replace({column}, '0', ''), '1', ''), '2', ''), "
    "'3', ''), '4', ''), '5', ''), '6', ''), '7', ''), '8', ''), "
    "'9', ''), 'a', ''), 'b', ''), 'c', ''), 'd', ''), 'e', ''), "
    "'f', '')"
)


def _hex_check(column: str, *, nullable: bool = True) -> str:
    prefix = f"{column} IS NULL OR " if nullable else ""
    return (
        f"{prefix}(length({column}) = 64 "
        f"AND lower({column}) = {column} "
        f"AND {_HEX_REMAINDER.format(column=column)} = '')"
    )


def _uuid_check(column: str) -> str:
    remainder = _HEX_REMAINDER.format(column=column).replace(
        f"{column}, '0'", f"replace({column}, '-', ''), '0'", 1
    )
    return (
        f"{column} IS NULL OR (length({column}) = 36 "
        f"AND substr({column}, 9, 1) = '-' "
        f"AND substr({column}, 14, 1) = '-' "
        f"AND substr({column}, 19, 1) = '-' "
        f"AND substr({column}, 24, 1) = '-' "
        f"AND lower({column}) = {column} AND {remainder} = '')"
    )


def upgrade() -> None:
    op.add_column(
        "download_completions",
        sa.Column("verification_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "download_completions",
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.CheckConstraint(
                _hex_check("artifact_sha256"),
                name="ck_download_completion_artifact_sha256",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "download_completions",
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "download_completions",
        sa.Column("http_status", sa.Integer(), nullable=True),
    )
    op.add_column(
        "download_completions",
        sa.Column("transfer_scope", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "download_completions",
        sa.Column("source_evidence", sa.JSON(), nullable=True),
    )
    op.add_column(
        "download_completions",
        sa.Column(
            "signed_event_id",
            sa.String(length=36),
            sa.CheckConstraint(
                _uuid_check("signed_event_id"),
                name="ck_download_completion_signed_event_id",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "download_completions",
        sa.Column(
            "signed_event_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "download_completions",
        sa.Column(
            "signed_payload_sha256",
            sa.String(length=64),
            sa.CheckConstraint(
                _hex_check("signed_payload_sha256"),
                name="ck_download_completion_payload_sha256",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "download_completions",
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            sa.CheckConstraint(
                "(verification_version IS NULL "
                "AND artifact_sha256 IS NULL "
                "AND expected_size_bytes IS NULL "
                "AND http_status IS NULL "
                "AND transfer_scope IS NULL "
                "AND source_evidence IS NULL "
                "AND signed_event_id IS NULL "
                "AND signed_event_timestamp IS NULL "
                "AND signed_payload_sha256 IS NULL "
                "AND verified_at IS NULL) OR "
                "(verification_version = 1 "
                "AND artifact_sha256 IS NOT NULL "
                "AND expected_size_bytes IS NOT NULL "
                "AND expected_size_bytes = bytes_sent "
                "AND http_status = 200 "
                "AND transfer_scope = 'full_body' "
                "AND source_evidence IS NOT NULL "
                "AND signed_event_id IS NOT NULL "
                "AND signed_event_timestamp IS NOT NULL "
                "AND signed_payload_sha256 IS NOT NULL "
                "AND verified_at IS NOT NULL)",
                name="ck_download_completion_verified_evidence_complete",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_download_completion_signed_event",
        "download_completions",
        ["signed_event_id"],
        unique=True,
    )

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE download_completions ADD CONSTRAINT "
            "ck_download_completion_new_rows_verified "
            "CHECK (verification_version IS NOT NULL "
            "AND verification_version = 1 AND "
            "source IN ('EDGE_GATEWAY', 'OBS_ACCESS_LOG')) NOT VALID"
        )
    elif dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_download_completions_verified_insert "
            "BEFORE INSERT ON download_completions "
            "WHEN NEW.verification_version IS NULL "
            "OR NEW.source NOT IN ('EDGE_GATEWAY', 'OBS_ACCESS_LOG') "
            "BEGIN SELECT RAISE(ABORT, "
            "'new download completions require signed source evidence'); END"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE download_completions DROP CONSTRAINT IF EXISTS "
            "ck_download_completion_new_rows_verified"
        )
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_download_completions_verified_insert")
    op.drop_index(
        "uq_download_completion_signed_event",
        table_name="download_completions",
    )
    for column_name in (
        "verified_at",
        "signed_payload_sha256",
        "signed_event_timestamp",
        "signed_event_id",
        "source_evidence",
        "transfer_scope",
        "http_status",
        "expected_size_bytes",
        "artifact_sha256",
        "verification_version",
    ):
        op.drop_column("download_completions", column_name)
