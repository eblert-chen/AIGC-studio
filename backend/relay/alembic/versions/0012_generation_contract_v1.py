"""Persist and fence the pinned generation capability revision.

Revision ID: 0012_generation_contract_v1
Revises: 0011_provider_monitoring
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_generation_contract_v1"
down_revision = "0011_provider_monitoring"
branch_labels = None
depends_on = None


_LEGACY_UNKNOWN_REVISION = "sha256:" + ("0" * 64)
_POSTGRES_FUNCTION = "relay_reject_capability_revision_update"
_POSTGRES_TRIGGER = "trg_generation_jobs_capability_revision_immutable"
_SQLITE_TRIGGER = "trg_generation_jobs_capability_revision_immutable"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    op.add_column(
        "generation_jobs",
        sa.Column(
            "expected_capability_revision",
            sa.String(71),
            nullable=True,
        ),
    )
    if dialect == "postgresql":
        op.execute(
            """
            UPDATE generation_jobs
            SET expected_capability_revision =
                metadata_json ->> 'relay_capability_revision'
            WHERE metadata_json ->> 'relay_capability_revision'
                  ~ '^sha256:[0-9a-f]{64}$'
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            UPDATE generation_jobs
            SET expected_capability_revision = json_extract(
                metadata_json,
                '$.relay_capability_revision'
            )
            WHERE json_valid(metadata_json)
              AND length(json_extract(
                    metadata_json,
                    '$.relay_capability_revision'
                  )) = 71
              AND json_extract(
                    metadata_json,
                    '$.relay_capability_revision'
                  ) LIKE 'sha256:%'
            """
        )
    else:
        raise RuntimeError(
            "0012 supports only PostgreSQL and SQLite capability fencing"
        )
    # Jobs accepted before pinning became mandatory cannot be assigned a real
    # revision after the fact. A valid-shaped sentinel makes that uncertainty
    # explicit and guarantees that pending legacy work fails the revision gate
    # before a provider call instead of silently running under current limits.
    op.execute(
        sa.text(
            "UPDATE generation_jobs "
            "SET expected_capability_revision = :legacy_revision "
            "WHERE expected_capability_revision IS NULL"
        ).bindparams(legacy_revision=_LEGACY_UNKNOWN_REVISION)
    )
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.alter_column(
            "expected_capability_revision",
            existing_type=sa.String(71),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_generation_jobs_capability_revision_shape",
            "length(expected_capability_revision) = 71 AND "
            "expected_capability_revision LIKE 'sha256:%'",
        )

    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION {_POSTGRES_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.expected_capability_revision IS DISTINCT FROM
                   OLD.expected_capability_revision THEN
                    RAISE EXCEPTION
                        'expected_capability_revision is immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {_POSTGRES_TRIGGER}
            BEFORE UPDATE OF expected_capability_revision
            ON generation_jobs
            FOR EACH ROW
            EXECUTE FUNCTION {_POSTGRES_FUNCTION}()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            f"""
            CREATE TRIGGER {_SQLITE_TRIGGER}
            BEFORE UPDATE OF expected_capability_revision
            ON generation_jobs
            FOR EACH ROW
            WHEN NEW.expected_capability_revision IS NOT
                 OLD.expected_capability_revision
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'expected_capability_revision is immutable'
                );
            END
            """
        )
    else:
        raise RuntimeError(
            "0012 supports only PostgreSQL and SQLite capability fencing"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {_POSTGRES_TRIGGER} ON generation_jobs"
        )
        op.execute(f"DROP FUNCTION IF EXISTS {_POSTGRES_FUNCTION}()")
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {_SQLITE_TRIGGER}")
    else:
        raise RuntimeError(
            "0012 supports only PostgreSQL and SQLite capability fencing"
        )

    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_constraint(
            "ck_generation_jobs_capability_revision_shape",
            type_="check",
        )
        batch_op.drop_column("expected_capability_revision")
