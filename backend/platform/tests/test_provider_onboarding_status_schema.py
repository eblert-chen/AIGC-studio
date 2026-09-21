from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from platform_api.schemas import (
    ProviderOnboardingCompanyStatus,
    ProviderOnboardingPersonalStatus,
    ProviderOnboardingStatusBatchResponse,
    ProviderOnboardingStatusResponseItem,
)


HEX64 = "a" * 64


def _personal(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "price_status": "active",
        "grant_status": "active",
        "price_points": 12,
        "grant_id": "personal-grant-a",
    }
    payload.update(overrides)
    return payload


def _company(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "price_status": "active",
        "grant_status": "active",
        "grant_count": 2,
        "enabled_grant_count": 2,
        "active_grant_count": 2,
        "active_price_count": 2,
    }
    payload.update(overrides)
    return payload


def _response_item(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_key": "account-a:model-a",
        "public_model_id": "model-a",
        "route_identity_sha256": HEX64,
        "publication_status": "released",
        "blocker_code": None,
        "plan_id": "plan-a",
        "execution_id": "execution-a",
        "publication_receipt_sha256": HEX64,
        "personal": _personal(),
        "company": _company(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_personal(price_points=None), "personal price evidence"),
        (
            _personal(price_status="not_configured", price_points=12),
            "personal price evidence",
        ),
        (_personal(grant_id=None), "personal grant evidence"),
        (
            _personal(grant_status="not_granted", grant_id="unexpected"),
            "personal grant evidence",
        ),
    ],
)
def test_personal_status_rejects_state_evidence_mismatch(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProviderOnboardingPersonalStatus.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _company(enabled_grant_count=1),
            "company grants must be enabled",
        ),
        (
            _company(price_status="not_applicable"),
            "not-applicable evidence",
        ),
        (
            _company(
                price_status="not_applicable",
                grant_status="not_applicable",
            ),
            "not-applicable evidence",
        ),
        (_company(active_price_count=1), "company price evidence"),
        (
            _company(price_status="partial", active_price_count=0),
            "company price evidence",
        ),
        (
            _company(price_status="partial", active_price_count=2),
            "company price evidence",
        ),
        (
            _company(price_status="not_configured", active_price_count=1),
            "company price evidence",
        ),
        (_company(active_grant_count=1), "company grant evidence"),
        (
            _company(grant_status="partial", active_grant_count=0),
            "company grant evidence",
        ),
        (
            _company(grant_status="partial", active_grant_count=2),
            "company grant evidence",
        ),
        (
            _company(grant_status="disabled", active_grant_count=1),
            "company grant evidence",
        ),
    ],
)
def test_company_status_rejects_count_and_state_mismatch(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProviderOnboardingCompanyStatus.model_validate(payload)


@pytest.mark.parametrize("publication_status", ["released", "disabled"])
@pytest.mark.parametrize(
    "missing_field",
    ["plan_id", "execution_id", "publication_receipt_sha256"],
)
def test_final_publication_requires_all_immutable_evidence(
    publication_status: str,
    missing_field: str,
) -> None:
    payload = _response_item(publication_status=publication_status)
    payload[missing_field] = None

    with pytest.raises(ValidationError, match="immutable evidence"):
        ProviderOnboardingStatusResponseItem.model_validate(payload)


@pytest.mark.parametrize(
    "observed_at",
    [
        datetime(2026, 9, 8, 8, 9, 10),
        datetime(
            2026,
            9,
            8,
            16,
            9,
            10,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    ],
)
def test_status_batch_requires_utc_observed_at(observed_at: datetime) -> None:
    with pytest.raises(ValidationError, match="observed_at must be UTC"):
        ProviderOnboardingStatusBatchResponse.model_validate(
            {
                "schema_version": 1,
                "observed_at": observed_at,
                "request_identity_sha256": HEX64,
                "items": [_response_item()],
            }
        )


def test_status_schema_accepts_consistent_utc_evidence() -> None:
    response = ProviderOnboardingStatusBatchResponse.model_validate(
        {
            "schema_version": 1,
            "observed_at": datetime(2026, 9, 8, 8, 9, 10, tzinfo=timezone.utc),
            "request_identity_sha256": HEX64,
            "items": [_response_item()],
        }
    )

    assert response.items[0].publication_status == "released"

