"""Persist trusted media metadata and director previs normalization evidence.

Revision ID: 0054_input_asset_media_metadata
Revises: 0053_director_shot_packages
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0054_input_asset_media_metadata"
down_revision: str | None = "0053_director_shot_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _capture_and_drop_sqlite_triggers() -> tuple[str, ...]:
    if op.get_bind().dialect.name != "sqlite":
        return ()
    connection = op.get_bind()
    statements = tuple(
        str(sql)
        for sql in connection.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND sql IS NOT NULL ORDER BY name"
            )
        ).scalars()
    )
    names = tuple(
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        ).scalars()
    )
    for name in names:
        connection.execute(sa.text(f'DROP TRIGGER "{name}"'))
    return statements


def _restore_sqlite_triggers(statements: tuple[str, ...]) -> None:
    for statement in statements:
        op.execute(sa.text(statement))


def _source_sha_check() -> str:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        return "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'"
    if dialect == "sqlite":
        return (
            "source_sha256 IS NULL OR (length(source_sha256) = 64 AND "
            "lower(source_sha256) = source_sha256 AND "
            "source_sha256 NOT GLOB '*[^0-9a-f]*')"
        )
    return (
        "source_sha256 IS NULL OR (length(source_sha256) = 64 AND "
        "lower(source_sha256) = source_sha256)"
    )


def upgrade() -> None:
    sqlite_triggers = _capture_and_drop_sqlite_triggers()
    with op.batch_alter_table("input_assets") as batch:
        batch.add_column(sa.Column("media_metadata_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("width_px", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("height_px", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("media_container", sa.String(32), nullable=True))
        batch.add_column(sa.Column("video_codec", sa.String(32), nullable=True))
        batch.add_column(sa.Column("video_fps", sa.Float(), nullable=True))
        batch.add_column(sa.Column("duration_ms", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("video_has_audio", sa.Boolean(), nullable=True))
        batch.add_column(
            sa.Column("normalization_profile", sa.String(64), nullable=True)
        )
        batch.add_column(sa.Column("source_sha256", sa.String(64), nullable=True))
        batch.create_check_constraint(
            "ck_input_asset_media_metadata_version",
            "media_metadata_version IS NULL OR media_metadata_version = 1",
        )
        batch.create_check_constraint(
            "ck_input_asset_dimensions_positive",
            "(width_px IS NULL AND height_px IS NULL) OR "
            "(width_px > 0 AND height_px > 0)",
        )
        batch.create_check_constraint(
            "ck_input_asset_trusted_metadata_shape",
            "media_metadata_version IS NULL OR "
            "(width_px IS NOT NULL AND height_px IS NOT NULL AND "
            "((media_type = 'image' AND media_container IS NULL AND "
            "video_codec IS NULL AND video_fps IS NULL AND duration_ms IS NULL "
            "AND video_has_audio IS NULL) OR "
            "(media_type = 'video' AND media_container IS NOT NULL AND "
            "video_codec IS NOT NULL AND video_fps > 0 AND duration_ms > 0 "
            "AND video_has_audio IS NOT NULL)))",
        )
        batch.create_check_constraint(
            "ck_input_asset_normalization_profile",
            "normalization_profile IS NULL OR "
            "(normalization_profile = 'director_previs_mp4_v1' AND "
            "source_sha256 IS NOT NULL AND source_sha256 <> sha256 AND "
            "media_metadata_version = 1 AND "
            "media_type = 'video' AND content_type = 'video/mp4' AND "
            "media_container = 'mp4' AND video_codec = 'h264' AND "
            "video_fps >= 23.99 AND video_fps <= 24.01 AND "
            "video_has_audio = false)",
        )
        batch.create_check_constraint(
            "ck_input_asset_source_sha256",
            _source_sha_check(),
        )
    _restore_sqlite_triggers(sqlite_triggers)


def downgrade() -> None:
    normalized_count = int(
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM input_assets "
                "WHERE normalization_profile IS NOT NULL"
            )
        )
        or 0
    )
    if normalized_count:
        raise RuntimeError(
            "Cannot downgrade while normalized director previs assets exist"
        )
    sqlite_triggers = _capture_and_drop_sqlite_triggers()
    with op.batch_alter_table("input_assets") as batch:
        batch.drop_constraint("ck_input_asset_source_sha256", type_="check")
        batch.drop_constraint("ck_input_asset_normalization_profile", type_="check")
        batch.drop_constraint("ck_input_asset_trusted_metadata_shape", type_="check")
        batch.drop_constraint("ck_input_asset_dimensions_positive", type_="check")
        batch.drop_constraint(
            "ck_input_asset_media_metadata_version", type_="check"
        )
        for column in (
            "source_sha256",
            "normalization_profile",
            "video_has_audio",
            "duration_ms",
            "video_fps",
            "video_codec",
            "media_container",
            "height_px",
            "width_px",
            "media_metadata_version",
        ):
            batch.drop_column(column)
    _restore_sqlite_triggers(sqlite_triggers)
