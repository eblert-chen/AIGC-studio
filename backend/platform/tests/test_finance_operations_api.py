from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from platform_api.models import (
    PaymentSettlementBatch,
    ProviderCostStatementBatch,
    User,
    UserAccountType,
)


INTERNAL_HEADERS = {"X-Internal-Service-Token": "test-internal-token"}
START = datetime(2033, 5, 1, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def _settlement_body(*, gross: int = 100) -> dict:
    document = json.dumps(
        {
            "schema_version": 1,
            "provider": "testpay",
            "merchant_account": "merchant-main",
            "source_kind": "psp_statement",
            "period_start": START.isoformat(),
            "period_end": END.isoformat(),
            "lines": [
                {
                    "provider_line_id": "statement-line-0001",
                    "provider_transaction_id": "pay_transaction_0001",
                    "related_provider_reference": None,
                    "line_type": "capture",
                    "gross_amount_cents": gross,
                    "fee_amount_cents": 2,
                    "net_amount_cents": gross - 2,
                    "currency": "CNY",
                    "occurred_at": START.isoformat(),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "provider": "testpay",
        "merchant_account": "merchant-main",
        "source_kind": "psp_statement",
        "period_start": START.isoformat(),
        "period_end": END.isoformat(),
        "provider_document_id": "statement-0001",
        "source_document_sha256": hashlib.sha256(document).hexdigest(),
        "source_document_base64": base64.b64encode(document).decode("ascii"),
        "source_object_key": "settlements/testpay/statement-0001.json",
        "source_object_version": "version-1",
        "source_size_bytes": len(document),
        "parser_version": "payment-settlement-json-v1",
    }


def _provider_cost_body() -> dict:
    document = json.dumps(
        {
            "schema_version": 1,
            "supplier": "relay-supplier",
            "supplier_account": "supplier-main",
            "period_start": START.isoformat(),
            "period_end": END.isoformat(),
            "lines": [
                {
                    "provider_line_id": "supplier-line-0001",
                    "provider_job_reference": "job-reference-0001",
                    "channel_key": "official-channel",
                    "task_id": None,
                    "amount_cents": 42,
                    "currency": "CNY",
                    "occurred_at": START.isoformat(),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "supplier": "relay-supplier",
        "supplier_account": "supplier-main",
        "period_start": START.isoformat(),
        "period_end": END.isoformat(),
        "provider_document_id": "supplier-statement-0001",
        "source_document_sha256": hashlib.sha256(document).hexdigest(),
        "source_document_base64": base64.b64encode(document).decode("ascii"),
        "source_object_key": "provider-cost/supplier-statement-0001.json",
        "source_object_version": "version-1",
        "source_size_bytes": len(document),
        "parser_version": "provider-cost-json-v1",
    }


def test_settlement_import_is_exactly_idempotent_and_internal_only(app, client) -> None:
    unauthorized = client.post(
        "/internal/finance/payment-settlements/import",
        json=_settlement_body(),
    )
    assert unauthorized.status_code == 401

    first = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=_settlement_body(),
    )
    assert first.status_code == 200, first.text
    assert first.json()["created_count"] == 1
    assert first.json()["total_count"] == 1
    assert first.json()["verification_method"] == "uploaded_file_digest"
    assert first.json()["source_authenticity"] == "unverified"
    with app.state.session_factory() as session:
        batch = session.get(PaymentSettlementBatch, first.json()["batch_id"])
        assert batch is not None
        assert batch.source_document_bytes == base64.b64decode(_settlement_body()["source_document_base64"])
        assert batch.source_size_bytes == len(batch.source_document_bytes)
        assert batch.source_document_sha256 == hashlib.sha256(batch.source_document_bytes).hexdigest()
        assert batch.verification_method == "uploaded_file_digest"
        assert abs((batch.verified_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()) < 30
    replay = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=_settlement_body(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["created_count"] == 0
    assert replay.json()["entries"][0]["id"] == first.json()["entries"][0]["id"]

    conflict = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=_settlement_body(gross=110),
    )
    assert conflict.status_code == 409


def test_settlement_import_binds_actual_bytes_hash_size_and_parsed_scope(client) -> None:
    body = _settlement_body()

    wrong_size = {**body, "source_size_bytes": body["source_size_bytes"] + 1}
    response = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=wrong_size,
    )
    assert response.status_code == 409

    original_bytes = base64.b64decode(body["source_document_base64"])
    tampered_bytes = original_bytes.replace(b'"gross_amount_cents":100', b'"gross_amount_cents":101')
    assert tampered_bytes != original_bytes
    tampered = {
        **body,
        "source_document_base64": base64.b64encode(tampered_bytes).decode("ascii"),
        "source_size_bytes": len(tampered_bytes),
    }
    response = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=tampered,
    )
    assert response.status_code == 409

    wrong_scope_document = json.loads(original_bytes)
    wrong_scope_document["merchant_account"] = "another-merchant"
    wrong_scope_bytes = json.dumps(
        wrong_scope_document, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    wrong_scope = {
        **body,
        "source_document_base64": base64.b64encode(wrong_scope_bytes).decode("ascii"),
        "source_document_sha256": hashlib.sha256(wrong_scope_bytes).hexdigest(),
        "source_size_bytes": len(wrong_scope_bytes),
    }
    response = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=wrong_scope,
    )
    assert response.status_code == 409

    manual = {**body, "verification_method": "manual"}
    response = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=manual,
    )
    assert response.status_code == 422

    for forged_fields in (
        {"verification_method": "provider_signature"},
        {"verified_at": END.isoformat()},
        {"lines": [{"gross_amount_cents": 999999}]},
    ):
        response = client.post(
            "/internal/finance/payment-settlements/import",
            headers=INTERNAL_HEADERS,
            json={**body, **forged_fields},
        )
        assert response.status_code == 422

    object_key_only = {
        key: value
        for key, value in body.items()
        if key not in {"source_document_base64", "source_document_sha256"}
    }
    response = client.post(
        "/internal/finance/payment-settlements/import",
        headers=INTERNAL_HEADERS,
        json=object_key_only,
    )
    assert response.status_code == 422


def test_provider_cost_import_archives_bytes_without_claiming_supplier_authenticity(app, client) -> None:
    body = _provider_cost_body()
    first = client.post(
        "/internal/finance/provider-cost-statements/import",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert first.status_code == 200, first.text
    assert first.json()["created_batch"] is True
    assert first.json()["created_count"] == 1
    assert first.json()["total_cost_cents"] == 42
    assert first.json()["verification_method"] == "uploaded_file_digest"
    assert first.json()["source_authenticity"] == "unverified"
    with app.state.session_factory() as session:
        batch = session.get(ProviderCostStatementBatch, first.json()["batch_id"])
        assert batch is not None
        assert batch.source_document_bytes == base64.b64decode(body["source_document_base64"])
        assert batch.source_document_sha256 == hashlib.sha256(batch.source_document_bytes).hexdigest()
        assert abs((batch.verified_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()) < 30

    replay = client.post(
        "/internal/finance/provider-cost-statements/import",
        headers=INTERNAL_HEADERS,
        json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["created_batch"] is False
    assert replay.json()["created_count"] == 0

    wrong_hash = {**body, "source_document_sha256": "f" * 64}
    response = client.post(
        "/internal/finance/provider-cost-statements/import",
        headers=INTERNAL_HEADERS,
        json=wrong_hash,
    )
    assert response.status_code == 409


def test_reconciliation_requires_statement_watermark_and_never_masks_unavailability(app, client) -> None:
    missing_watermark = client.post(
        "/internal/finance/reconciliations",
        headers=INTERNAL_HEADERS,
        json={
            "run_kind": "daily",
            "period_start": START.isoformat(),
            "period_end": END.isoformat(),
            "idempotency_key": "finance-api-watermark-missing",
            "provider": "testpay",
            "merchant_account": "merchant-main",
            "provider_statement_available": True,
            "payment_settlement_batch_ids": [],
            "provider_cost_statement_available": False,
            "provider_cost_batch_ids": [],
            "source_watermarks": {},
        },
    )
    assert missing_watermark.status_code == 422

    unavailable = client.post(
        "/internal/finance/reconciliations",
        headers=INTERNAL_HEADERS,
        json={
            "run_kind": "daily",
            "period_start": START.isoformat(),
            "period_end": END.isoformat(),
            "idempotency_key": "finance-api-source-unavailable",
            "provider": "testpay",
            "merchant_account": "merchant-main",
            "provider_statement_available": False,
            "payment_settlement_batch_ids": [],
            "provider_cost_statement_available": False,
            "provider_cost_batch_ids": [],
            "source_watermarks": {"fetch_error": "provider_timeout"},
        },
    )
    assert unavailable.status_code == 200, unavailable.text
    payload = unavailable.json()
    assert payload["status"] == "balanced_with_exceptions"
    cash = next(item for item in payload["snapshots"] if item["dimension"] == "cash")
    assert cash["status"] == "source_unavailable"
    assert any(
        item["code"] == "PAYMENT_SETTLEMENT_SOURCE_UNAVAILABLE"
        for item in payload["exceptions"]
    )
    with app.state.session_factory.begin() as session:
        actor = User(
            email="finance-resolution@example.test",
            display_name="Finance operator",
            account_type=UserAccountType.COMPANY,
        )
        session.add(actor)
        session.flush()
        actor_id = actor.id
    exception_id = next(
        item["id"]
        for item in payload["exceptions"]
        if item["code"] == "PAYMENT_SETTLEMENT_SOURCE_UNAVAILABLE"
    )
    resolution_body = {
        "action": "escalated",
        "note": "Provider statement endpoint timed out; retry ticket opened.",
        "evidence_sha256": "b" * 64,
        "actor_user_id": actor_id,
        "idempotency_key": "resolution-source-timeout-0001",
    }
    resolved = client.post(
        f"/internal/finance/reconciliations/exceptions/{exception_id}/resolutions",
        headers=INTERNAL_HEADERS,
        json=resolution_body,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["created"] is True
    replay = client.post(
        f"/internal/finance/reconciliations/exceptions/{exception_id}/resolutions",
        headers=INTERNAL_HEADERS,
        json=resolution_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["created"] is False
