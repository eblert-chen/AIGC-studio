from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from platform_api.config import get_settings
from platform_api.database import Base
from platform_api.database_privileges import (
    collect_platform_database_evidence,
    validate_platform_database_acl_evidence,
    validate_platform_migration_database_evidence,
    validate_platform_migration_source_state,
)
from platform_api.platform_database_release_proof import (
    attest_platform_database_release_proof,
)
from platform_api.process_secrets import protected_platform_runtime_requested
from platform_api import models  # noqa: F401 - registers model metadata
from platform_api import platform_admin_access_models  # noqa: F401 - registers admin RBAC metadata


config = context.config
protected_runtime = protected_platform_runtime_requested()
if protected_runtime:
    settings = get_settings("migration")
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
else:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        def include_object(
            object_,
            name,
            type_,
            reflected,
            compare_to,
        ) -> bool:
            del name, compare_to
            if (
                connection.dialect.name == "sqlite"
                and type_ == "foreign_key_constraint"
                and not reflected
                and getattr(getattr(object_, "table", None), "name", None)
                == "channel_cost_entries"
                and tuple(column.name for column in object_.columns)
                == ("personal_workspace_id",)
            ):
                # Revision 0034 deliberately implements this one SQLite FK as
                # an insert-time trigger. Rebuilding the immutable legacy
                # ledger table would discard dialect-specific evidence checks
                # and append-only guards. PostgreSQL retains the real FK.
                return False
            return True

        if protected_runtime:
            validate_platform_migration_source_state(connection)
            source_evidence = validate_platform_migration_database_evidence(
                connection
            )
            attest_platform_database_release_proof(connection, source_evidence)
            # The read-only source/proof attestation above starts SQLAlchemy's
            # implicit transaction. Alembic cannot own (and therefore cannot
            # commit) a transaction that was already open when
            # ``context.begin_transaction`` is entered. End only that read-only
            # snapshot while retaining the same checked-out physical session;
            # every protected role migration repeats its source/proof gate in
            # the Alembic-owned DDL transaction below.
            connection.rollback()
            if connection.in_transaction():
                raise RuntimeError(
                    "Protected Platform migration preflight transaction is still open"
                )
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
            if protected_runtime:
                validate_platform_database_acl_evidence(
                    collect_platform_database_evidence(connection),
                    require_head=True,
                )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
