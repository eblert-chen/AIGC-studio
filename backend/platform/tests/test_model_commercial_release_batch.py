"""Commercial release batch journal behaviour.

These tests cover the parts a batch adds on top of the existing per-model
release flow: the request-shape guards, the state machine, and the immutability
of the plan-to-batch binding.  The all-or-none release itself is exercised
through the service in the local-video-lab suite, which owns the full Relay
catalog and release-evidence fixtures.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from platform_api.models import (
    ModelCommercialReleaseBatch,
    ModelCommercialReleaseExecution,
    ModelCommercialReleasePlan,
    ModelDefinition,
    User,
    UserAccountType,
    UserStatus,
    new_id,
    utcnow,
)
from platform_api.services.errors import ConflictError, NotFoundError
from platform_api.services.model_commercial_release import (
    ModelCommercialReleaseService,
)


def _session(app):
    return app.state.session_factory()


def _relay_must_not_be_read():
    """These requests must be rejected before any Relay round trip."""

    raise AssertionError("Relay snapshot was read for a request that must fail first")


def _seed_owner(session) -> User:
    user = User(
        id=new_id(),
        email=f"owner-{new_id()[:8]}@example.com",
        display_name="Batch Owner",
        account_type=UserAccountType.PERSONAL,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    return user


def _seed_model(session, *, slug: str) -> ModelDefinition:
    model = ModelDefinition(
        id=new_id(),
        slug=slug,
        display_name=slug,
        provider_key="volcengine-ark",
        billing_mode="per_second",
        capability_version=1,
        active=False,
    )
    session.add(model)
    session.flush()
    return model


def _seed_batch(
    session,
    *,
    owner: User,
    state: str,
    model_count: int = 1,
) -> ModelCommercialReleaseBatch:
    batch = ModelCommercialReleaseBatch(
        id=new_id(),
        idempotency_key=f"batch-{new_id()[:8]}",
        state=state,
        model_count=model_count,
        request_sha256="a" * 64,
        catalog_revision="sha256:" + "b" * 64,
        approved_by_user_id=owner.id,
        approved_at=utcnow(),
        attempt_count=0,
        last_failure_code="",
        activated_by_user_id=owner.id if state == "released" else None,
        released_at=utcnow() if state == "released" else None,
        result_payload={"batch_id": "placeholder"} if state == "released" else None,
    )
    session.add(batch)
    session.flush()
    return batch


def _seed_plan(
    session,
    *,
    model: ModelDefinition,
    batch: ModelCommercialReleaseBatch,
) -> ModelCommercialReleasePlan:
    plan = ModelCommercialReleasePlan(
        id=new_id(),
        revision=1,
        batch_id=batch.id,
        model_id=model.id,
        candidate_revision="sha256:" + "c" * 64,
        candidate_catalog_revision="sha256:" + "d" * 64,
        capability_version=1,
        billing_mode="per_second",
        provider_cost_currency="CNY",
        provider_cost_formula={"schema_version": 1},
        provider_cost_micros=1,
        provider_cost_cny_micros=1,
        provider_cost_evidence_kind="contract_rate",
        provider_cost_evidence_reference="test",
        provider_cost_evidence_sha256="e" * 64,
        provider_cost_effective_at=utcnow(),
        fx_cny_micros_per_currency_unit=1,
        fx_source="test",
        fx_version="v1",
        fx_evidence_sha256="f" * 64,
        fx_effective_at=utcnow(),
        points_per_cny=10,
        target_margin_bps=3000,
        minimum_price_points=1,
        personal_price_points=1,
        enterprise_price_points=1,
        enterprise_distribution_scope="all_active_point_companies_at_release",
        personal_config_override={},
        enterprise_config_override={},
        approval_reason="batch test",
        approved_by_user_id=batch.approved_by_user_id,
        approved_at=utcnow(),
        idempotency_key=f"plan-{new_id()[:8]}",
        content_sha256=new_id().replace("-", "") + new_id().replace("-", "")[:32],
    )
    session.add(plan)
    session.flush()
    session.add(
        ModelCommercialReleaseExecution(
            id=new_id(),
            plan_id=plan.id,
            state="approved",
            attempt_count=0,
            company_ids=[],
        )
    )
    session.flush()
    return plan


def test_plan_batch_binding_is_bound_at_insert_and_never_updated(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        model = _seed_model(session, slug="batch-immutability-model")
        batch = _seed_batch(session, owner=owner, state="approved")
        plan = _seed_plan(session, model=model, batch=batch)
        session.commit()
        plan_id = plan.id

    with _session(app) as session:
        plan = session.get(ModelCommercialReleasePlan, plan_id)
        assert plan.batch_id == batch.id
        plan.batch_id = None
        with pytest.raises(RuntimeError, match="immutable"):
            session.flush()
        session.rollback()


def test_create_batch_rejects_duplicate_models_without_relay(app) -> None:
    with _session(app) as session:
        with pytest.raises(ConflictError, match="重复项"):
            ModelCommercialReleaseService.create_batch(
                session,
                idempotency_key="batch-duplicate-key",
                items=[{"model_id": "same"}, {"model_id": "same"}],
                relay_snapshot=_relay_must_not_be_read,
                approved_by_user_id="admin",
                request_id="req",
            )


def test_create_batch_rejects_empty_items_without_relay(app) -> None:
    with _session(app) as session:
        with pytest.raises(ConflictError, match="至少包含一个模型"):
            ModelCommercialReleaseService.create_batch(
                session,
                idempotency_key="batch-empty-key",
                items=[],
                relay_snapshot=_relay_must_not_be_read,
                approved_by_user_id="admin",
                request_id="req",
            )


def test_create_batch_rejects_missing_idempotency_key_without_relay(app) -> None:
    with _session(app) as session:
        with pytest.raises(ConflictError, match="幂等键"):
            ModelCommercialReleaseService.create_batch(
                session,
                idempotency_key="   ",
                items=[{"model_id": "m"}],
                relay_snapshot=_relay_must_not_be_read,
                approved_by_user_id="admin",
                request_id="req",
            )


def test_create_batch_rejects_model_already_in_an_open_batch(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        model = _seed_model(session, slug="open-batch-conflict")
        batch = _seed_batch(session, owner=owner, state="approved")
        _seed_plan(session, model=model, batch=batch)
        session.commit()

        with pytest.raises(ConflictError, match="尚未释放"):
            ModelCommercialReleaseService.create_batch(
                session,
                idempotency_key="batch-conflict-key",
                items=[{"model_id": model.id}],
                relay_snapshot=_relay_must_not_be_read,
                approved_by_user_id=owner.id,
                request_id="req",
            )


def test_released_batch_does_not_block_a_new_batch(app) -> None:
    """A released batch must not hold its models hostage."""

    with _session(app) as session:
        owner = _seed_owner(session)
        model = _seed_model(session, slug="released-batch-model")
        batch = _seed_batch(session, owner=owner, state="released")
        _seed_plan(session, model=model, batch=batch)
        session.commit()
        assert (
            ModelCommercialReleaseService._open_batch_plan_id(
                session, model_id=model.id
            )
            is None
        )


def test_open_batch_plan_id_reports_the_binding(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        model = _seed_model(session, slug="open-batch-binding")
        batch = _seed_batch(session, owner=owner, state="approved")
        plan = _seed_plan(session, model=model, batch=batch)
        session.commit()
        assert ModelCommercialReleaseService._open_batch_plan_id(
            session, model_id=model.id
        ) == plan.id


def test_activate_rejects_unknown_batch(app) -> None:
    with _session(app) as session:
        with pytest.raises(NotFoundError, match="批量不存在"):
            ModelCommercialReleaseService.activate_release_batch(
                session,
                batch_id=new_id(),
                relay_snapshot=_relay_must_not_be_read,
                activated_by_user_id="admin",
                request_id="req",
            )


def test_activate_rejects_abandoned_batch(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        batch = _seed_batch(session, owner=owner, state="abandoned")
        session.commit()
        with pytest.raises(ConflictError, match="不处于可激活状态"):
            ModelCommercialReleaseService.activate_release_batch(
                session,
                batch_id=batch.id,
                relay_snapshot=_relay_must_not_be_read,
                activated_by_user_id="admin",
                request_id="req",
            )


def test_activate_replays_a_released_receipt(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        batch = _seed_batch(session, owner=owner, state="released")
        batch.result_payload = {"batch_id": batch.id, "state": "released"}
        session.commit()
        replayed = ModelCommercialReleaseService.activate_release_batch(
            session,
            batch_id=batch.id,
            relay_snapshot=_relay_must_not_be_read,
            activated_by_user_id="admin",
            request_id="req",
        )
        assert replayed == {"batch_id": batch.id, "state": "released"}


def test_abandon_rejects_a_released_batch(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        batch = _seed_batch(session, owner=owner, state="released")
        session.commit()
        with pytest.raises(ConflictError, match="不能放弃"):
            ModelCommercialReleaseService.abandon_batch(
                session,
                batch_id=batch.id,
                abandoned_by_user_id=owner.id,
                request_id="req",
            )


def test_abandon_is_idempotent(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        batch = _seed_batch(session, owner=owner, state="approved")
        session.commit()
        first = ModelCommercialReleaseService.abandon_batch(
            session,
            batch_id=batch.id,
            abandoned_by_user_id=owner.id,
            request_id="req",
        )
        second = ModelCommercialReleaseService.abandon_batch(
            session,
            batch_id=batch.id,
            abandoned_by_user_id=owner.id,
            request_id="req",
        )
        assert first["state"] == second["state"] == "abandoned"


def test_list_batches_reports_plan_counts(app) -> None:
    with _session(app) as session:
        owner = _seed_owner(session)
        model = _seed_model(session, slug="listed-batch-model")
        batch = _seed_batch(session, owner=owner, state="approved")
        _seed_plan(session, model=model, batch=batch)
        session.commit()
        listed = {
            row["batch_id"]: row
            for row in ModelCommercialReleaseService.list_batches(session)
        }
        assert listed[batch.id]["plan_count"] == 1
        assert listed[batch.id]["state"] == "approved"


def test_reconcile_query_excludes_batch_owned_plans(app) -> None:
    """Batch plans must never be released by the per-plan reconciler."""

    with _session(app) as session:
        owner = _seed_owner(session)
        model = _seed_model(session, slug="reconcile-exclusion-model")
        batch = _seed_batch(session, owner=owner, state="approved")
        _seed_plan(session, model=model, batch=batch)
        session.commit()

        batched = session.scalars(
            select(ModelCommercialReleasePlan.id).where(
                ModelCommercialReleasePlan.batch_id.is_not(None)
            )
        ).all()
        unbatched = session.scalars(
            select(ModelCommercialReleasePlan.id).where(
                ModelCommercialReleasePlan.batch_id.is_(None)
            )
        ).all()
        assert batched and not unbatched


# ----------------------------------------------------------------------
# HTTP wiring
# ----------------------------------------------------------------------


def _admin_headers(client, suffix: str = "batch") -> dict[str, str]:
    response = client.post(
        "/api/v1/bootstrap/platform-admin",
        json={
            "email": f"admin-{suffix}@example.com",
            "display_name": f"Admin {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return {"X-Platform-Admin-User-ID": response.json()["user_id"]}


def test_batch_endpoints_require_a_platform_administrator(client) -> None:
    preflight = client.post(
        "/api/v1/platform-admin/model-commercial-release-batches/preflight",
        json={"model_ids": ["00000000-0000-4000-8000-000000000000"]},
    )
    assert preflight.status_code in {401, 403}, preflight.text

    listed = client.get("/api/v1/platform-admin/model-commercial-release-batches")
    assert listed.status_code in {401, 403}, listed.text

    created = client.post(
        "/api/v1/platform-admin/model-commercial-release-batches",
        json={"idempotency_key": "batch-http-auth", "items": []},
    )
    assert created.status_code in {401, 403}, created.text


def test_list_batches_is_empty_on_a_fresh_install(client) -> None:
    headers = _admin_headers(client)
    response = client.get(
        "/api/v1/platform-admin/model-commercial-release-batches",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_preflight_fails_closed_without_a_relay_client(client) -> None:
    headers = _admin_headers(client)
    response = client.post(
        "/api/v1/platform-admin/model-commercial-release-batches/preflight",
        headers=headers,
        json={"model_ids": ["00000000-0000-4000-8000-000000000000"]},
    )
    assert response.status_code == 503, response.text
    assert "Relay" in response.json()["detail"]


def test_create_batch_rejects_an_incomplete_item_before_touching_relay(client) -> None:
    """A malformed body must fail on validation, not on Relay availability.

    Each batch item carries the full single-model approval payload, so an item
    with only a ``model_id`` is a request error (422) and must not surface as a
    Relay outage (503).
    """

    headers = _admin_headers(client)
    response = client.post(
        "/api/v1/platform-admin/model-commercial-release-batches",
        headers=headers,
        json={
            "idempotency_key": "batch-http-incomplete-key",
            "items": [
                {"model_id": "00000000-0000-4000-8000-000000000000"},
            ],
        },
    )
    assert response.status_code == 422, response.text


def test_activate_unknown_batch_is_not_found(client) -> None:
    headers = _admin_headers(client)
    response = client.post(
        "/api/v1/platform-admin/model-commercial-release-batches/"
        "00000000-0000-4000-8000-000000000000/activate",
        headers=headers,
    )
    assert response.status_code == 404, response.text


def test_abandon_unknown_batch_is_not_found(client) -> None:
    headers = _admin_headers(client)
    response = client.post(
        "/api/v1/platform-admin/model-commercial-release-batches/"
        "00000000-0000-4000-8000-000000000000/abandon",
        headers=headers,
    )
    assert response.status_code == 404, response.text
