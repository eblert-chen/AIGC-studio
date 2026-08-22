from __future__ import annotations

from datetime import datetime, timezone

from .errors import ConflictError


MAX_CALL_QUOTA = 9_223_372_036_854_775_807
MAX_CONCURRENCY_LIMIT = 2_147_483_647


def normalize_entitlement_policy(
    *,
    call_quota: int | None,
    concurrency_limit: int | None,
    effective_at: datetime | None,
    expires_at: datetime | None,
) -> tuple[int | None, int | None, datetime | None, datetime | None]:
    """Validate and normalize the policy persisted on a company grant."""

    if call_quota is not None and (
        isinstance(call_quota, bool)
        or not isinstance(call_quota, int)
        or call_quota <= 0
        or call_quota > MAX_CALL_QUOTA
    ):
        raise ConflictError("call_quota must be a positive 64-bit integer")
    if concurrency_limit is not None and (
        isinstance(concurrency_limit, bool)
        or not isinstance(concurrency_limit, int)
        or concurrency_limit <= 0
        or concurrency_limit > MAX_CONCURRENCY_LIMIT
    ):
        raise ConflictError(
            "concurrency_limit must be a positive 32-bit integer"
        )

    def normalize_time(value: datetime | None, *, field_name: str):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ConflictError(f"{field_name} must include a UTC offset")
        return value.astimezone(timezone.utc)

    normalized_effective_at = normalize_time(
        effective_at, field_name="effective_at"
    )
    normalized_expires_at = normalize_time(expires_at, field_name="expires_at")
    if (
        normalized_effective_at is not None
        and normalized_expires_at is not None
        and normalized_effective_at >= normalized_expires_at
    ):
        raise ConflictError("effective_at must be before expires_at")
    return (
        call_quota,
        concurrency_limit,
        normalized_effective_at,
        normalized_expires_at,
    )
