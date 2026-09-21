from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from platform_api.database import Base
from platform_api.models import (
    AuditLog,
    ModelDefinition,
    PersonalRetailModelGrant,
    PersonalModelGrantBatchJournal,
    User,
)
from platform_api.services.personal import (
    PersonalRetailGrantService,
    PersonalWorkspaceService,
)

from .test_model_capability_v1_contract import (
    _admin_headers,
    _create_model,
    _mode,
    canonical_capability,
)
from .test_relay_capability_sync import (
    CatalogRelayClient,
    _approve_candidate,
    _catalog,
    _sync_candidate,
)
from .test_model_commercial_release import _commercial_body


def _approved_personal_image_model(
    app, client, headers, *, suffix: str = "personal-distribution"
):
    capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[1],
                resolutions=["1024x1024"],
                output_counts=[1],
                input_media_types=[],
            )
        }
    )
    created = _create_model(
        client,
        headers,
        suffix=suffix,
        capability=capability,
        billing_mode="per_item",
    )
    assert created.status_code == 201, created.text
    model = created.json()
    next_catalog = _catalog(model["slug"], capability)
    prior_client = app.state.relay_client
    prior_catalog = getattr(prior_client, "catalog", None)
    if prior_catalog is not None:
        next_catalog = next_catalog.model_copy(
            update={"data": [*prior_catalog.data, *next_catalog.data]}
        )
    app.state.relay_client = CatalogRelayClient(next_catalog)
    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Record personal image model candidate",
    )
    assert synced.status_code == 200, synced.text
    approved = _approve_candidate(
        client,
        headers,
        model,
        reason="Approve personal image model capability",
    )
    assert approved.status_code == 200, approved.text
    model = approved.json()["model"]
    # This fixture exercises real distribution policy against a deterministic
    # Relay double: capability approval alone no longer authorizes a price.
    plan_body = _commercial_body(
        model=model, cost_kind="output_item", formula_billing_unit="per_item",
        assumptions={"quantity_basis": "relay_effective_capability_ceiling", "enforced_limits": {"max_output_count": 1}},
        components=[{"component": "output_item", "rate_micros": 10_000, "quantity_numerator": 1, "quantity_denominator": 1}],
    )
    plan_body["provider_cost_evidence_sha256"] = "c" * 64
    plan = client.put(f"/api/v1/platform-admin/models/{model['id']}/commercial-release-plan", headers=headers, json=plan_body)
    assert plan.status_code == 200, plan.text
    released = client.post("/api/v1/platform-admin/model-commercial-releases/reconcile", headers=headers)
    assert released.status_code == 200 and released.json()["released_count"] == 1, released.text
    published = client.get(f"/api/v1/platform-admin/models/{model['id']}", headers=headers).json()
    with app.state.session_factory.begin() as session:
        stored = session.get(ModelDefinition, model["id"])
        grant = session.scalar(select(PersonalRetailModelGrant).where(PersonalRetailModelGrant.model_id == stored.id))
        current = PersonalRetailGrantService.response(session, model=stored, grant=grant)
        PersonalRetailGrantService.upsert(
            session, model_id=stored.id, expected_capability_version=stored.capability_version,
            expected_quote_revision=current["quote_revision"], enabled=False,
            price_per_item_points=grant.price_per_item_points, price_per_second_points=None,
            config_override={}, require_relay_approval=True,
        )
    initial_rows = client.get("/api/v1/platform-admin/personal-model-grants", headers=headers).json()
    published["initial_quote_revision"] = next(row["quote_revision"] for row in initial_rows if row["model_id"] == model["id"])
    return published


def test_personal_model_distribution_is_explicit_audited_and_conflict_safe(
    app, client
) -> None:
    headers = _admin_headers(client, "personal-distribution")
    model = _approved_personal_image_model(app, client, headers)

    listed = client.get(
        "/api/v1/platform-admin/personal-model-grants", headers=headers
    )
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["model_id"] == model["id"])
    assert row["enabled"] is False
    assert row["quote_revision"] == model["initial_quote_revision"]

    enabled = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": model["initial_quote_revision"],
            "enabled": True,
            "price_per_item_points": 9,
            "price_per_second_points": None,
            "config_override": {},
            "reason": "Open verified Seedream retail distribution",
        },
    )
    assert enabled.status_code == 200, enabled.text
    enabled_row = enabled.json()
    assert enabled_row["enabled"] is True
    assert enabled_row["price_per_item_points"] == 9
    assert enabled_row["quote_revision"].startswith("sha256:")
    assert "text_to_image" in enabled_row["effective_capabilities"]["modes"]

    stale = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": None,
            "enabled": False,
            "price_per_item_points": 9,
            "config_override": {},
            "reason": "Stale editor must not overwrite current retail grant",
        },
    )
    assert stale.status_code == 409
    assert "已变化" in stale.text

    wrong_billing = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": enabled_row["quote_revision"],
            "enabled": True,
            "price_per_second_points": 3,
            "price_per_item_points": None,
            "config_override": {},
            "reason": "Reject mismatched retail billing mode",
        },
    )
    assert wrong_billing.status_code == 409
    assert "计费方式不一致" in wrong_billing.text

    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "personal_model_grant.upsert",
                AuditLog.target_id == model["id"],
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        )
        assert audit is not None
        assert (
            audit.after_summary["reason"]
            == "Open verified Seedream retail distribution"
        )


def test_personal_limit_mutations_distinguish_omission_from_explicit_null(
    app, client
) -> None:
    headers = _admin_headers(client, "personal-limit-mutation")
    model = _approved_personal_image_model(
        app, client, headers, suffix="personal-limit-mutation"
    )

    def mutation(
        quote_revision: str,
        *,
        enabled: bool,
        limits: dict | None,
    ) -> dict:
        payload = {
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": quote_revision,
            "enabled": enabled,
            "price_per_item_points": 9,
            "price_per_second_points": None,
            "config_override": {},
            "reason": "Exercise explicit personal usage limit mutation semantics",
        }
        if limits is not None:
            payload.update(limits)
        return payload

    configured = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json=mutation(
            model["initial_quote_revision"],
            enabled=True,
            limits={"call_quota": 20, "concurrency_limit": 3},
        ),
    )
    assert configured.status_code == 200, configured.text
    configured_row = configured.json()
    assert (configured_row["call_quota"], configured_row["concurrency_limit"]) == (
        20,
        3,
    )

    # An omitted field preserves the current value, even when another grant
    # property changes and advances the quote revision.
    omitted = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json=mutation(
            configured_row["quote_revision"], enabled=False, limits=None
        ),
    )
    assert omitted.status_code == 200, omitted.text
    omitted_row = omitted.json()
    assert (omitted_row["call_quota"], omitted_row["concurrency_limit"]) == (20, 3)
    assert omitted_row["quote_revision"] != configured_row["quote_revision"]

    # Explicit null clears both limits in one request.
    cleared = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json=mutation(
            omitted_row["quote_revision"],
            enabled=False,
            limits={"call_quota": None, "concurrency_limit": None},
        ),
    )
    assert cleared.status_code == 200, cleared.text
    cleared_row = cleared.json()
    assert (cleared_row["call_quota"], cleared_row["concurrency_limit"]) == (
        None,
        None,
    )
    assert cleared_row["quote_revision"] != omitted_row["quote_revision"]

    reset = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json=mutation(
            cleared_row["quote_revision"],
            enabled=True,
            limits={"call_quota": 30, "concurrency_limit": 4},
        ),
    )
    assert reset.status_code == 200, reset.text
    reset_row = reset.json()

    def batch_change(
        quote_revision: str,
        *,
        enabled: bool,
        limits: dict | None,
    ) -> dict:
        payload = mutation(
            quote_revision, enabled=enabled, limits=limits
        )
        payload.pop("reason")
        payload["model_id"] = model["id"]
        return payload

    omitted_change = batch_change(
        reset_row["quote_revision"], enabled=False, limits=None
    )
    omitted_preview = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/preview",
        headers=headers,
        json={"changes": [omitted_change]},
    )
    assert omitted_preview.status_code == 200, omitted_preview.text
    omitted_preview_body = omitted_preview.json()
    assert omitted_preview_body["cells"][0]["after"]["call_quota"] == 30
    assert omitted_preview_body["cells"][0]["after"]["concurrency_limit"] == 4
    omitted_execute = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
        headers=headers,
        json={
            "changes": [omitted_change],
            "expected_snapshot": omitted_preview_body["snapshot"],
            "reason": "Batch omission must preserve personal usage limits",
            "idempotency_key": "personal-limit-batch-omit-001",
        },
    )
    assert omitted_execute.status_code == 200, omitted_execute.text
    omitted_batch_row = omitted_execute.json()["items"][0]
    assert (
        omitted_batch_row["call_quota"],
        omitted_batch_row["concurrency_limit"],
    ) == (30, 4)

    clear_change = batch_change(
        omitted_batch_row["quote_revision"],
        enabled=False,
        limits={"call_quota": None, "concurrency_limit": None},
    )
    clear_preview = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/preview",
        headers=headers,
        json={"changes": [clear_change]},
    )
    assert clear_preview.status_code == 200, clear_preview.text
    clear_preview_body = clear_preview.json()
    assert clear_preview_body["snapshot"] != omitted_preview_body["snapshot"]
    assert clear_preview_body["cells"][0]["after"]["call_quota"] is None
    assert clear_preview_body["cells"][0]["after"]["concurrency_limit"] is None
    clear_execute = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
        headers=headers,
        json={
            "changes": [clear_change],
            "expected_snapshot": clear_preview_body["snapshot"],
            "reason": "Batch explicit null must clear personal usage limits",
            "idempotency_key": "personal-limit-batch-clear-001",
        },
    )
    assert clear_execute.status_code == 200, clear_execute.text
    clear_batch_row = clear_execute.json()["items"][0]
    assert (
        clear_batch_row["call_quota"],
        clear_batch_row["concurrency_limit"],
    ) == (None, None)
    assert clear_batch_row["quote_revision"] != omitted_batch_row["quote_revision"]

    with app.state.session_factory() as session:
        latest = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "personal_model_grant.upsert",
                AuditLog.target_id == model["id"],
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        )
        assert latest is not None
        assert latest.before_summary["call_quota"] == 30
        assert latest.before_summary["concurrency_limit"] == 4
        assert latest.after_summary["call_quota"] is None
        assert latest.after_summary["concurrency_limit"] is None


def test_personal_catalog_pauses_a_drifted_relay_candidate_until_approval(
    app, client
) -> None:
    headers = _admin_headers(client, "personal-drift-gate")
    model = _approved_personal_image_model(app, client, headers)
    enabled = client.put(
        f"/api/v1/platform-admin/personal-model-grants/{model['id']}",
        headers=headers,
        json={
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": model["initial_quote_revision"],
            "enabled": True,
            "price_per_item_points": 9,
            "price_per_second_points": None,
            "config_override": {},
            "reason": "Open model before Relay drift exercise",
        },
    )
    assert enabled.status_code == 200, enabled.text
    with app.state.session_factory.begin() as session:
        user = User(
            email="personal-drift-gate@example.com",
            display_name="Personal Drift Gate",
        )
        session.add(user)
        session.flush()
        PersonalWorkspaceService.ensure(session, user_id=user.id)
        personal_headers = {"X-User-ID": user.id}

    before = client.get("/api/v1/personal/models", headers=personal_headers)
    assert before.status_code == 200, before.text
    assert any(item["id"] == model["id"] for item in before.json())

    next_revision = "sha256:" + ("e" * 64)
    next_catalog_revision = "sha256:" + ("f" * 64)
    next_capability = canonical_capability(
        modes={
            "text_to_image": _mode(
                max_images=0,
                max_videos=0,
                max_audio=0,
                supports_face=False,
                durations=[1],
                resolutions=["1024x1024"],
                output_counts=[1, 2],
                input_media_types=[],
            )
        }
    )
    app.state.relay_client = CatalogRelayClient(
        _catalog(
            model["slug"],
            next_capability,
            capability_revision=next_revision,
            catalog_revision=next_catalog_revision,
        )
    )
    synced = _sync_candidate(
        client,
        headers,
        model,
        reason="Pause personal distribution for changed Relay capability",
        capability_revision=next_revision,
        catalog_revision=next_catalog_revision,
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["requires_approval"] is True

    paused = client.get("/api/v1/personal/models", headers=personal_headers)
    assert paused.status_code == 200, paused.text
    assert all(item["id"] != model["id"] for item in paused.json())

    approved = _approve_candidate(
        client,
        headers,
        model,
        reason="Restore personal distribution after reviewed Relay change",
        capability_revision=next_revision,
        catalog_revision=next_catalog_revision,
    )
    assert approved.status_code == 200, approved.text
    restored = client.get("/api/v1/personal/models", headers=personal_headers)
    assert restored.status_code == 200, restored.text
    assert any(item["id"] == model["id"] for item in restored.json())


def test_personal_model_distribution_batch_is_atomic_previewed_and_idempotent(
    app, client
) -> None:
    headers = _admin_headers(client, "personal-distribution-batch")
    first = _approved_personal_image_model(
        app, client, headers, suffix="personal-distribution-batch-a"
    )
    second = _approved_personal_image_model(
        app, client, headers, suffix="personal-distribution-batch-b"
    )
    catalog = client.get(
        "/api/v1/platform-admin/personal-model-grants", headers=headers
    )
    assert catalog.status_code == 200, catalog.text
    by_id = {item["model_id"]: item for item in catalog.json()}
    changes = [
        {
            "model_id": model["id"],
            "expected_capability_version": model["capability_version"],
            "expected_quote_revision": by_id[model["id"]]["quote_revision"],
            "enabled": True,
            "price_per_item_points": points,
            "price_per_second_points": None,
            "config_override": {},
        }
        for model, points in ((first, 7), (second, 11))
    ]
    preview = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/preview",
        headers=headers,
        json={"changes": changes},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["changed_cells"] == 2
    assert preview_body["snapshot"]

    operation = {
        "changes": changes,
        "expected_snapshot": preview_body["snapshot"],
        "reason": "Open two reviewed personal models as one atomic retail release",
        "idempotency_key": "personal-batch-release-001",
    }
    executed = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
        headers=headers,
        json=operation,
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["applied_cell_count"] == 2
    assert executed.json()["idempotent_replay"] is False

    replay = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
        headers=headers,
        json=operation,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True

    reused_for_different_request = client.post(
        "/api/v1/platform-admin/personal-model-grants/batch/execute",
        headers=headers,
        json={
            **operation,
            "reason": "A different change must not reuse the prior operation key",
        },
    )
    assert reused_for_different_request.status_code == 409

    with app.state.session_factory() as session:
        assert session.scalar(
            select(func.count(PersonalModelGrantBatchJournal.id)).where(
                PersonalModelGrantBatchJournal.idempotency_key
                == "personal-batch-release-001"
            )
        ) == 1
        journal = session.scalar(
            select(PersonalModelGrantBatchJournal).where(
                PersonalModelGrantBatchJournal.idempotency_key
                == "personal-batch-release-001"
            )
        )
        assert journal is not None
        assert journal.state == "succeeded"
        assert journal.result_payload["applied_cell_count"] == 2
        batch_audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "personal_model_grant.batch",
                AuditLog.target_id == "personal-batch-release-001",
            )
        )
        assert batch_audit is not None
        assert batch_audit.after_summary["result"]["applied_cell_count"] == 2
        item_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.action == "personal_model_grant.upsert",
                AuditLog.target_id.in_([first["id"], second["id"]]),
            )
        ).all()
        assert len(item_audits) == 2


def test_personal_batch_journal_claim_is_unique_under_concurrency(tmp_path) -> None:
    """Two owner requests cannot both claim the same idempotency key.

    This exercises the database unique constraint with separate connections,
    rather than relying on a sequential API replay or a query-then-insert audit
    lookup.  The winner stores the durable result; the waiter replays it.
    """

    database_path = tmp_path / "personal-batch-concurrency.sqlite3"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        actor = User(
            email="personal-batch-concurrency@example.com",
            display_name="Personal Batch Concurrency",
        )
        session.add(actor)
        session.flush()
        actor_id = actor.id

    changes = [
        {
            "model_id": "model-concurrency-fixture",
            "expected_capability_version": 1,
            "expected_quote_revision": None,
            "enabled": True,
            "price_per_second_points": None,
            "price_per_item_points": 7,
            "config_override": {},
        }
    ]
    expected_snapshot = "sha256:" + ("a" * 64)
    start = Barrier(2)

    def claim_and_finish(request_id: str) -> bool:
        start.wait(timeout=5)
        with factory.begin() as session:
            claim = PersonalRetailGrantService.claim_batch(
                session,
                changes=changes,
                expected_snapshot=expected_snapshot,
                actor_user_id=actor_id,
                reason="Concurrent exact replay proof",
                request_id=request_id,
                idempotency_key="personal-batch-concurrency-001",
            )
            if claim["replay"] is not None:
                return True
            journal = claim["journal"]
            journal.state = "succeeded"
            journal.result_payload = {
                "batch_id": journal.idempotency_key,
                "snapshot": expected_snapshot,
                "applied_cell_count": 1,
                "items": [],
                "idempotent_replay": False,
            }
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        replay_flags = list(
            executor.map(claim_and_finish, ("request-a", "request-b"))
        )

    assert sorted(replay_flags) == [False, True]
    with factory() as session:
        journals = session.scalars(
            select(PersonalModelGrantBatchJournal).where(
                PersonalModelGrantBatchJournal.idempotency_key
                == "personal-batch-concurrency-001"
            )
        ).all()
        assert len(journals) == 1
        assert journals[0].state == "succeeded"
        assert journals[0].result_payload["applied_cell_count"] == 1
    engine.dispose()
