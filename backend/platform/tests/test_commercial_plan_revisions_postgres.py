"""Opt-in local PostgreSQL behavior tests; not production qualification."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError

from platform_api.main import create_app
from platform_api.database_privileges import PLATFORM_ALEMBIC_HEAD
from platform_api.models import Company, ModelCommercialReleaseExecution, ModelCommercialReleasePlan, ModelDefinition
from platform_api.services.model_commercial_release import ModelCommercialReleaseService
from platform_api.services.models import ModelGrantService
from platform_api.services.model_release_guard import build_model_release_readiness
from .conftest import TEST_BOOTSTRAP_TOKEN
from . import test_model_commercial_release as contract
from .test_commercial_plan_revisions import _approve, _rotate_and_accept
from .test_relay_capability_sync import CatalogRelayClient, _catalog


def test_postgres_approval_revisions_and_lock_order_red_green(app):
    url = os.getenv("PLATFORM_REVIEW_POSTGRES_URL")
    if not url:
        pytest.skip("isolated PostgreSQL review database not configured")
    engine = create_engine(url, hide_parameters=True)
    database_errors = []
    def record_error(context):
        error = context.original_exception
        database_errors.append((getattr(error, "sqlstate", None), getattr(getattr(error, "diag", None), "constraint_name", None)))
    event.listen(engine, "handle_error", record_error)
    with engine.connect() as connection:
        assert str(connection.scalar(text("select current_database()"))).startswith("review_")
        assert connection.scalar(text("select version_num from alembic_version")) == PLATFORM_ALEMBIC_HEAD
    settings = app.state.settings.model_copy(update={"database_url": url, "auto_create_tables": False})
    pg_app = create_app(settings=settings, engine=engine)
    try:
        with TestClient(pg_app, headers={"X-Bootstrap-Token": TEST_BOOTSTRAP_TOKEN}) as client:
            headers = contract._admin_headers(client, "pg-commercial-" + uuid4().hex)
            capability = contract.canonical_capability(modes={"text_to_video": contract._mode(
                max_images=0, max_videos=0, max_audio=0, supports_face=False,
                durations=[5, 10], resolutions=["768p", "2k"], output_counts=[1], input_media_types=[],
            )})
            catalog = _catalog("review-commercial-" + uuid4().hex, capability)
            pg_app.state.relay_client = CatalogRelayClient(catalog, evidence_status="blocked", provider_cost_billing_unit="output_second", provider_cost_source_sha256="1" * 64)
            discovered = client.post("/api/v1/platform-admin/relay-models/reconcile", headers=headers)
            assert discovered.status_code == 200, discovered.text
            model_id = discovered.json()["created_model_ids"][0]
            model = client.get(f"/api/v1/platform-admin/models/{model_id}", headers=headers).json()
            body = contract._commercial_body(model=model)
            first = _approve(client, headers, model, body)
            assert first.status_code == 200, (first.text, database_errors)
            _rotate_and_accept(pg_app)
            body["expected_routing_release_sha256"] = "sha256:" + "4" * 64
            body["supersedes_plan_id"] = first.json()["id"]
            body["idempotency_key"] += ":second"
            second = _approve(client, headers, model, body)
            assert second.status_code == 200, second.text
            # Unique predecessor plus the company/model lock serializes two
            # competing successors even on a database with real row locks.
            with ThreadPoolExecutor(max_workers=2) as executor:
                race_bodies = []
                for index in range(2):
                    candidate = dict(body, supersedes_plan_id=second.json()["id"], idempotency_key=body["idempotency_key"] + f":race-{index}")
                    race_bodies.append(candidate)
                responses = list(executor.map(lambda candidate: _approve(client, headers, model, candidate), race_bodies))
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = next(response.json() for response in responses if response.status_code == 200)
            assert winner["revision"] == 3
            released = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
            assert released.status_code == 200, released.text
            assert released.json()["released_count"] == 1
            by_id = {item["id"]: item for item in released.json()["items"]}
            assert by_id[winner["id"]]["state"] == "released"
            assert by_id[first.json()["id"]]["state"] == "superseded"

            with pg_app.state.session_factory.begin() as session:
                company = Company(id=str(uuid4()), name="Lock-order review", billing_version=2)
                session.add(company)
                company_id = company.id
                price_evidence = build_model_release_readiness(
                    model=session.get(ModelDefinition, model_id),
                    evidence=pg_app.state.relay_client.evidence.models[0].model_dump(mode="json"),
                ).expected_snapshot

            def run_race(legacy: bool):
                company_locked, release_started = Event(), Event()

                def grant():
                    with pg_app.state.session_factory() as session:
                        try:
                            session.execute(text("SET LOCAL statement_timeout='10s'"))
                            session.scalar(select(Company).where(Company.id == company_id).with_for_update())
                            company_locked.set()
                            assert release_started.wait(5)
                            ModelGrantService.upsert_grant(session, company_id=company_id, model_id=model_id, enabled=False,
                                price_per_second_cents=None, price_per_item_cents=None, price_per_second_points=12, config_override={},
                                expected_release_snapshot=price_evidence)
                            session.flush()
                            return "ok"
                        except DBAPIError as exc:
                            return exc.orig.sqlstate
                        finally:
                            session.rollback()

                def release():
                    assert company_locked.wait(5)
                    with pg_app.state.session_factory() as session:
                        try:
                            session.execute(text("SET LOCAL statement_timeout='10s'"))
                            if legacy:
                                # Exact former outer SELECT: PostgreSQL locks
                                # the joined model before _release_one's company.
                                session.execute(select(ModelCommercialReleasePlan, ModelCommercialReleaseExecution, ModelDefinition)
                                    .join(ModelCommercialReleaseExecution, ModelCommercialReleaseExecution.plan_id == ModelCommercialReleasePlan.id)
                                    .join(ModelDefinition, ModelDefinition.id == ModelCommercialReleasePlan.model_id)
                                    .where(ModelDefinition.id == model_id).with_for_update()).all()
                                release_started.set()
                                ModelCommercialReleaseService.lock_distribution_companies(session)
                            else:
                                release_started.set()
                                ModelCommercialReleaseService.reconcile(session, catalog=catalog,
                                    release_evidence=pg_app.state.relay_client.evidence,
                                    request_id="pg-lock-review", trigger="review")
                            return "ok"
                        except DBAPIError as exc:
                            return exc.orig.sqlstate
                        finally:
                            release_started.set()
                            session.rollback()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    grant_future = executor.submit(grant)
                    release_future = executor.submit(release)
                    return [grant_future.result(timeout=15), release_future.result(timeout=15)]

            old_results = run_race(True)
            assert "40P01" in old_results, old_results
            new_results = run_race(False)
            assert new_results == ["ok", "ok"], new_results
            print("PG_LOCK_RED", old_results, "PG_LOCK_GREEN", new_results)
    finally:
        engine.dispose()
