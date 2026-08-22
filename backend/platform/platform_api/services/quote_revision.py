from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from ..models import CompanyModelGrant, ModelDefinition


QUOTE_REVISION_PREFIX = "sha256:"


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def model_grant_quote_revision(
    *,
    model: ModelDefinition,
    grant: CompanyModelGrant,
) -> str:
    """Return a stable revision for every grant field affecting admission.

    Capability catalog changes use their existing integer version. This revision
    protects the company-specific pricing and grant policy that can change
    independently of that catalog version.
    """

    payload: dict[str, Any] = {
        "schema_version": 1,
        "company_id": grant.company_id,
        "model_id": model.id,
        "model_billing_mode": model.billing_mode,
        "grant_id": grant.id,
        "enabled": grant.enabled,
        "price_per_second_cents": grant.price_per_second_cents,
        "price_per_item_cents": grant.price_per_item_cents,
        "config_override": grant.config_override,
        "call_quota": grant.call_quota,
        "concurrency_limit": grant.concurrency_limit,
        "effective_at": _timestamp(grant.effective_at),
        "expires_at": _timestamp(grant.expires_at),
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return QUOTE_REVISION_PREFIX + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
