"""Add reusable subjects and the reference images that pin their identity.

Revision ID: 0064_subjects
Revises: 0063_member_spend_limits

Character consistency across shots needs a named thing to be consistent *about*.
Until now the only handle on that was whatever image an operator happened to
upload most recently. ``subjects`` gives the platform the missing layer: a
name bound to a set of reference images, scoped like every other customer-owned
row.

``subject_references`` carries the per-viewpoint images. Two constraints carry
the design:

* ``uq_subject_reference_asset`` -- an image pins a subject at most once, so a
  subject cannot silently double-weight one view.
* ``uq_subject_reference_order`` -- the ordering is total. Model support for
  reference images is uneven (Seedance 2.x takes nine, most models one or two),
  so callers truncate to a prefix of this order. A total order is what makes
  that truncation deterministic instead of arbitrary.

Subjects are archived, never deleted: generations already reference them.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0064_subjects"
down_revision: str | None = "0063_member_spend_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SUBJECT_KIND = sa.Enum(
    "CHARACTER",
    "STYLE",
    "LOCATION",
    "PROP",
    name="subjectkind",
    native_enum=False,
)
_SUBJECT_VIEW_ANGLE = sa.Enum(
    "FRONT",
    "SIDE",
    "BACK",
    "THREE_QUARTER",
    "CUSTOM",
    name="subjectviewangle",
    native_enum=False,
)

_SCOPE = (
    "(company_id IS NOT NULL AND personal_workspace_id IS NULL) OR "
    "(company_id IS NULL AND personal_workspace_id IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("id", sa.String(length=38), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("personal_workspace_id", sa.String(length=36), nullable=True),
        sa.Column("kind", _SUBJECT_KIND, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("cover_asset_id", sa.String(length=36), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_SCOPE, name="ck_subject_scope"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["personal_workspace_id"],
            ["personal_workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cover_asset_id"], ["input_assets.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("company_id", "name", name="uq_subject_company_name"),
        sa.UniqueConstraint(
            "personal_workspace_id", "name", name="uq_subject_personal_name"
        ),
    )
    op.create_index("ix_subjects_company_id", "subjects", ["company_id"], unique=False)
    op.create_index(
        "ix_subjects_personal_workspace_id",
        "subjects",
        ["personal_workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_subjects_created_by_user_id",
        "subjects",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_subject_company_kind",
        "subjects",
        ["company_id", "kind", "archived_at"],
        unique=False,
    )
    op.create_index(
        "ix_subject_personal_kind",
        "subjects",
        ["personal_workspace_id", "kind", "archived_at"],
        unique=False,
    )
    op.create_table(
        "subject_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_id", sa.String(length=38), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("view_angle", _SUBJECT_VIEW_ANGLE, nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sort_order >= 0", name="ck_subject_reference_order_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["input_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "subject_id", "asset_id", name="uq_subject_reference_asset"
        ),
        sa.UniqueConstraint(
            "subject_id", "sort_order", name="uq_subject_reference_order"
        ),
    )
    op.create_index(
        "ix_subject_references_subject_id",
        "subject_references",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_subject_reference_subject_order",
        "subject_references",
        ["subject_id", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subject_reference_subject_order", table_name="subject_references"
    )
    op.drop_index("ix_subject_references_subject_id", table_name="subject_references")
    op.drop_table("subject_references")
    op.drop_index("ix_subject_personal_kind", table_name="subjects")
    op.drop_index("ix_subject_company_kind", table_name="subjects")
    op.drop_index("ix_subjects_created_by_user_id", table_name="subjects")
    op.drop_index("ix_subjects_personal_workspace_id", table_name="subjects")
    op.drop_index("ix_subjects_company_id", table_name="subjects")
    op.drop_table("subjects")
