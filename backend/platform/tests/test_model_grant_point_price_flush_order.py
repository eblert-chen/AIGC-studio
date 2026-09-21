"""Grant writes under the unchanged 0046 SQLite/PostgreSQL trigger contract.

PostgreSQL is opt-in via PLATFORM_GRANT_PRICE_POSTGRES_TEST_URL only. It uses
one connection and one never-committed outer transaction, creates a random
lab_test_<uuid> schema, excludes public from search_path, and verifies that
ROLLBACK removed the schema. No deployed business table is read or written.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from platform_api.database import Base
from platform_api.models import (
    Company,
    CompanyModelGrant,
    CompanyPointPriceVersion,
    ModelCapability,
    ModelDefinition,
    PointPriceVersionStatus,
    new_id,
)
from platform_api.services.models import ModelGrantService
from platform_api.services.quote_revision import model_grant_quote_revision


def _install_original_guards(connection) -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/0046_company_points_billing_v2.py"
    )
    spec = importlib.util.spec_from_file_location(
        f"grant_price_order_migration_{uuid.uuid4().hex}", source
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # Invoke the original guard installer, not a weakened test reconstruction
    # or the migration/ACL workflow for an already-running database.
    migration._execute = connection.exec_driver_sql
    if connection.dialect.name == "postgresql":
        migration._create_postgres_guards()
    else:
        migration._create_sqlite_guards()


@pytest.fixture(params=("sqlite", "postgresql"))
def guarded_price_connection(request):
    if request.param == "postgresql":
        database_url = os.getenv("PLATFORM_GRANT_PRICE_POSTGRES_TEST_URL", "")
        if not database_url.startswith("postgresql"):
            pytest.skip("requires an explicitly selected PostgreSQL regression DB")
    else:
        database_url = "sqlite+pysqlite://"
    engine = create_engine(database_url, poolclass=NullPool)
    schema_name = f"lab_test_{uuid.uuid4().hex}"
    assert re.fullmatch(r"lab_test_[0-9a-f]{32}", schema_name)
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                if request.param == "postgresql":
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                    connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
                    connection.exec_driver_sql(f'CREATE SCHEMA "{schema_name}"')
                    connection.exec_driver_sql(
                        f'SET LOCAL search_path = "{schema_name}"'
                    )
                    assert (
                        connection.scalar(text("SELECT current_schema()"))
                        == schema_name
                    )
                else:
                    connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                Base.metadata.create_all(connection)
                _install_original_guards(connection)
                yield connection
            finally:
                # Deliberately no commit or DROP ... CASCADE: the random schema
                # and every fixture write disappear with the outer transaction.
                assert outer.is_active, "a service must not end the caller transaction"
                outer.rollback()
                if request.param == "postgresql":
                    assert connection.scalar(
                        text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"),
                        {"name": schema_name},
                    ) == 0
    finally:
        engine.dispose()


@pytest.fixture
def price_session(guarded_price_connection):
    with Session(
        bind=guarded_price_connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        yield session


def _seed(session, *, billing_mode="per_item", billing_version=2):
    company = Company(
        name="Isolated grant ordering regression", billing_version=billing_version
    )
    model = ModelDefinition(
        slug=f"grant-price-order-{uuid.uuid4().hex}",
        display_name="Isolated grant ordering regression",
        provider_key="offline-grant-price-test",
        billing_mode=billing_mode,
    )
    session.add_all([company, model])
    session.flush()
    session.add(
        ModelCapability(
            model_id=model.id,
            capability_key="generation",
            config={
                "schema_version": 1,
                "modes": {
                    "text_to_video": {
                        "input_media_types": [],
                        "supports_face": False,
                        "required_resource_keys": [],
                        "limits": {
                            "max_prompt_length": 100,
                            "max_images": 0,
                            "max_videos": 0,
                            "max_audio": 0,
                            "duration_seconds": [5],
                            "aspect_ratios": ["16:9"],
                            "resolutions": ["720p"],
                            "output_counts": [1],
                        },
                    }
                },
            },
        )
    )
    session.flush()
    return company, model


def _upsert(session, company, model, price=17):
    return ModelGrantService.upsert_grant(
        session,
        company_id=company.id,
        model_id=model.id,
        enabled=True,
        price_per_second_cents=None,
        price_per_item_cents=None,
        price_per_second_points=price if model.billing_mode == "per_second" else None,
        price_per_item_points=price if model.billing_mode == "per_item" else None,
        config_override={},
    )


@pytest.mark.parametrize("billing_mode", ("per_item", "per_second"))
@pytest.mark.parametrize("autoflush", (False, True))
def test_new_point_grant_inserts_price_version_before_link_update(
    price_session, guarded_price_connection, billing_mode, autoflush
):
    price_session.autoflush = autoflush
    company, model = _seed(price_session, billing_mode=billing_mode)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(guarded_price_connection, "before_cursor_execute", capture)
    try:
        grant = _upsert(price_session, company, model)
    finally:
        event.remove(guarded_price_connection, "before_cursor_execute", capture)
    version = price_session.get(
        CompanyPointPriceVersion, grant.point_price_active_version_id
    )
    assert version is not None
    assert (version.company_id, version.grant_id, version.model_id) == (
        company.id, grant.id, model.id
    )
    assert version.status == PointPriceVersionStatus.ACTIVE
    assert version.billing_mode == billing_mode
    assert version.unit_price_points == 17
    assert version.supersedes_version_id is None
    assert grant.enabled is True
    version_insert = next(
        i for i, sql in enumerate(statements)
        if sql.startswith("INSERT INTO company_point_price_versions")
    )
    active_link = next(
        i for i, sql in enumerate(statements)
        if sql.startswith("UPDATE company_model_grants ")
        and "point_price_active_version_id" in sql.split(" WHERE ")[0]
    )
    assert version_insert < active_link


def test_replayed_price_reuses_version_and_changed_price_keeps_immutable_history(
    price_session,
):
    company, model = _seed(price_session)
    grant = _upsert(price_session, company, model)
    first_id = grant.point_price_active_version_id
    first_revision = model_grant_quote_revision(model=model, grant=grant)
    replay = _upsert(price_session, company, model)
    assert replay.point_price_active_version_id == first_id
    assert model_grant_quote_revision(model=model, grant=replay) == first_revision
    changed = _upsert(price_session, company, model, 18)
    second_id = changed.point_price_active_version_id
    assert second_id != first_id
    second = price_session.get(CompanyPointPriceVersion, second_id)
    assert second is not None and second.unit_price_points == 18
    assert second.supersedes_version_id == first_id
    assert (
        _upsert(price_session, company, model, 18).point_price_active_version_id
        == second_id
    )
    price_session.expire_all()
    first = price_session.get(CompanyPointPriceVersion, first_id)
    assert first is not None and first.unit_price_points == 17
    assert first.status == PointPriceVersionStatus.ACTIVE
    assert price_session.scalar(
        select(func.count()).select_from(CompanyPointPriceVersion)
    ) == 2


def test_candidate_can_be_superseded_without_mutating_or_relinking_its_scope(
    price_session,
):
    company, model = _seed(price_session)
    grant = CompanyModelGrant(company_id=company.id, model_id=model.id, enabled=False)
    price_session.add(grant)
    price_session.flush()
    candidate = CompanyPointPriceVersion(
        id=new_id(),
        company_id=company.id,
        grant_id=grant.id,
        model_id=model.id,
        status=PointPriceVersionStatus.CANDIDATE,
        billing_mode=model.billing_mode,
        unit_price_points=10,
        source_price_cents=100,
        formula_version="candidate-regression:v1",
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        created_by_system_key="offline-grant-price-test",
    )
    price_session.add(candidate)
    price_session.flush()
    grant.point_price_candidate_version_id = candidate.id
    price_session.flush()
    active = _upsert(price_session, company, model)
    version = price_session.get(
        CompanyPointPriceVersion, active.point_price_active_version_id
    )
    assert version is not None and version.supersedes_version_id == candidate.id
    assert active.point_price_candidate_version_id == candidate.id
    price_session.refresh(candidate)
    assert candidate.status == PointPriceVersionStatus.CANDIDATE
    assert candidate.unit_price_points == 10


def test_original_guards_still_reject_cross_scope_links_and_version_mutation(
    price_session,
):
    company, model = _seed(price_session)
    grant = _upsert(price_session, company, model)
    other_company, other_model = _seed(price_session)
    other_grant = _upsert(price_session, other_company, other_model)
    version_id = grant.point_price_active_version_id
    with pytest.raises(DBAPIError, match="scope mismatch"):
        with price_session.begin_nested():
            price_session.execute(
                update(CompanyModelGrant)
                .where(CompanyModelGrant.id == grant.id)
                .values(
                    point_price_active_version_id=(
                        other_grant.point_price_active_version_id
                    )
                )
            )
    with pytest.raises(DBAPIError, match="immutable"):
        with price_session.begin_nested():
            price_session.execute(
                update(CompanyPointPriceVersion)
                .where(CompanyPointPriceVersion.id == version_id)
                .values(unit_price_points=999)
            )
    price_session.refresh(grant)
    assert grant.point_price_active_version_id == version_id


@pytest.mark.parametrize("existing", (False, True))
def test_price_flush_does_not_commit_or_escape_caller_rollback(price_session, existing):
    company, model = _seed(price_session)
    first_id = (
        _upsert(price_session, company, model).point_price_active_version_id
        if existing else None
    )
    with pytest.raises(RuntimeError, match="later transaction failure"):
        with price_session.begin_nested():
            _upsert(price_session, company, model, 18)
            raise RuntimeError("later transaction failure")
    price_session.expire_all()
    grants = list(price_session.scalars(select(CompanyModelGrant)).all())
    versions = list(price_session.scalars(select(CompanyPointPriceVersion)).all())
    assert len(grants) == len(versions) == int(existing)
    if existing:
        assert grants[0].point_price_active_version_id == first_id
        assert versions[0].id == first_id and versions[0].unit_price_points == 17


def test_legacy_cent_grant_still_has_no_point_price_version(price_session):
    company, model = _seed(price_session, billing_version=1)
    grant = ModelGrantService.upsert_grant(
        price_session,
        company_id=company.id,
        model_id=model.id,
        enabled=True,
        price_per_second_cents=None,
        price_per_item_cents=25,
        config_override={},
    )
    assert grant.price_per_item_cents == 25
    assert grant.point_price_active_version_id is None
    assert price_session.scalar(
        select(func.count()).select_from(CompanyPointPriceVersion)
    ) == 0
