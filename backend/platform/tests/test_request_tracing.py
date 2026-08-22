from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from platform_api.models import RelaySubmissionOutbox
from .test_relay_boundary import accepted_response
from .test_wallet_and_tasks import seed_model


def test_platform_request_id_is_safe_to_echo(client) -> None:
    trusted = client.get(
        "/health/live", headers={"X-Request-ID": "web-create-001:part_a"}
    )
    oversized = client.get(
        "/health/live", headers={"X-Request-ID": "r" * 512}
    )

    assert trusted.headers["X-Request-ID"] == "web-create-001:part_a"
    assert UUID(oversized.headers["X-Request-ID"])


def test_task_request_id_survives_outbox_and_relay_dispatch(
    app, client, tenant, tenant_headers, internal_headers
) -> None:
    company_id = tenant["company_id"]
    model_id = seed_model(app, company_id)
    assert client.post(
        f"/api/v1/companies/{company_id}/wallet/recharge",
        headers=tenant_headers,
        json={"amount_cents": 1000, "idempotency_key": "trace-recharge"},
    ).status_code == 200
    trace_id = "web-create-task-001"
    created = client.post(
        f"/api/v1/companies/{company_id}/tasks",
        headers={**tenant_headers, "X-Request-ID": trace_id},
        json={
            "model_id": model_id,
            "idempotency_key": "trace-task-create",
            "request_payload": {
                "prompt": "trace this request",
                "duration_seconds": 5,
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.headers["X-Request-ID"] == trace_id

    with app.state.session_factory() as session:
        outbox = session.scalar(
            select(RelaySubmissionOutbox).where(
                RelaySubmissionOutbox.task_id == created.json()["id"]
            )
        )
        assert outbox is not None
        assert outbox.relay_payload["metadata"]["platform_request_id"] == trace_id

    class TraceRelayClient:
        def __init__(self):
            self.request_id = None

        def submit(self, payload, *, idempotency_key, request_id=None):
            self.request_id = request_id
            return accepted_response("33333333-3333-3333-3333-333333333333")

    relay = TraceRelayClient()
    app.state.relay_client = relay
    dispatched = client.post(
        "/internal/relay/dispatch-once", headers=internal_headers
    )

    assert dispatched.status_code == 200, dispatched.text
    assert relay.request_id == trace_id
