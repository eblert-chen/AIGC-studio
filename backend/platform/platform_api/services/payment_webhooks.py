from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Callable, Iterable, Literal, Mapping
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from ..payment_providers import (
    MAX_PAYMENT_AMOUNT_CENTS,
    PAYMENT_MERCHANT_ACCOUNT_PATTERN,
    PAYMENT_PROVIDER_KEY_PATTERN,
    PAYMENT_PROVIDER_RESOURCE_ID_PATTERN,
)
from .errors import DomainError


PAYMENT_WEBHOOK_KEY_ID_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}"
MAX_PAYMENT_WEBHOOK_BODY_BYTES = 64 * 1024

_PROVIDER_KEY = re.compile(rf"^{PAYMENT_PROVIDER_KEY_PATTERN}$")
_KEY_ID = re.compile(rf"^{PAYMENT_WEBHOOK_KEY_ID_PATTERN}$")
_MERCHANT_ACCOUNT = re.compile(rf"^{PAYMENT_MERCHANT_ACCOUNT_PATTERN}$")
_CURRENCY_PATTERN = r"^[A-Z]{3}$"
_RESOURCE_ID_PATTERN = rf"^{PAYMENT_PROVIDER_RESOURCE_ID_PATTERN}$"
_FAILURE_CODE_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _canonical_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("UUID value must be a canonical string")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("UUID value must be a canonical string") from None
    if str(parsed) != value:
        raise ValueError("UUID value must be a canonical string")
    return parsed


class StrictPaymentWebhookModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PaymentCapturedData(StrictPaymentWebhookModel):
    order_id: UUID
    provider_payment_id: str = Field(pattern=_RESOURCE_ID_PATTERN)
    amount_cents: int = Field(strict=True, gt=0, le=MAX_PAYMENT_AMOUNT_CENTS)
    currency: str = Field(pattern=_CURRENCY_PATTERN)

    _validate_order_id = field_validator("order_id", mode="before")(
        _canonical_uuid
    )


class PaymentTerminalData(StrictPaymentWebhookModel):
    order_id: UUID
    provider_payment_id: str = Field(pattern=_RESOURCE_ID_PATTERN)
    failure_code: str | None = Field(default=None, pattern=_FAILURE_CODE_PATTERN)

    _validate_order_id = field_validator("order_id", mode="before")(
        _canonical_uuid
    )


class RefundEventData(StrictPaymentWebhookModel):
    order_id: UUID
    refund_id: UUID
    provider_payment_id: str = Field(pattern=_RESOURCE_ID_PATTERN)
    provider_refund_id: str = Field(pattern=_RESOURCE_ID_PATTERN)
    amount_cents: int = Field(strict=True, gt=0, le=MAX_PAYMENT_AMOUNT_CENTS)
    currency: str = Field(pattern=_CURRENCY_PATTERN)

    _validate_order_id = field_validator("order_id", mode="before")(
        _canonical_uuid
    )
    _validate_refund_id = field_validator("refund_id", mode="before")(
        _canonical_uuid
    )


class RefundFailedData(RefundEventData):
    failure_code: str = Field(pattern=_FAILURE_CODE_PATTERN)


class DisputeEventData(StrictPaymentWebhookModel):
    order_id: UUID
    provider_payment_id: str = Field(pattern=_RESOURCE_ID_PATTERN)
    provider_dispute_id: str = Field(pattern=_RESOURCE_ID_PATTERN)
    amount_cents: int = Field(strict=True, gt=0, le=MAX_PAYMENT_AMOUNT_CENTS)
    currency: str = Field(pattern=_CURRENCY_PATTERN)
    reason_code: str | None = Field(
        default=None,
        pattern=_FAILURE_CODE_PATTERN,
    )

    _validate_order_id = field_validator("order_id", mode="before")(
        _canonical_uuid
    )


class PaymentWebhookEventBase(StrictPaymentWebhookModel):
    api_version: Literal["v1"]
    schema_version: Literal[1]
    event_id: UUID
    provider: str = Field(pattern=rf"^{PAYMENT_PROVIDER_KEY_PATTERN}$")
    key_id: str = Field(pattern=rf"^{PAYMENT_WEBHOOK_KEY_ID_PATTERN}$")
    occurred_at: AwareDatetime

    _validate_event_id = field_validator("event_id", mode="before")(
        _canonical_uuid
    )

    @field_validator("occurred_at", mode="after")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class PaymentCapturedEvent(PaymentWebhookEventBase):
    type: Literal["payment.captured"]
    data: PaymentCapturedData


class PaymentFailedEvent(PaymentWebhookEventBase):
    type: Literal["payment.failed"]
    data: PaymentTerminalData


class PaymentCancelledEvent(PaymentWebhookEventBase):
    type: Literal["payment.cancelled"]
    data: PaymentTerminalData


class PaymentExpiredEvent(PaymentWebhookEventBase):
    type: Literal["payment.expired"]
    data: PaymentTerminalData


class RefundSucceededEvent(PaymentWebhookEventBase):
    type: Literal["refund.succeeded"]
    data: RefundEventData


class RefundFailedEvent(PaymentWebhookEventBase):
    type: Literal["refund.failed"]
    data: RefundFailedData


class DisputeOpenedEvent(PaymentWebhookEventBase):
    type: Literal["dispute.opened"]
    data: DisputeEventData


class DisputeWonEvent(PaymentWebhookEventBase):
    type: Literal["dispute.won"]
    data: DisputeEventData


class DisputeLostEvent(PaymentWebhookEventBase):
    type: Literal["dispute.lost"]
    data: DisputeEventData


PaymentWebhookEvent = Annotated[
    PaymentCapturedEvent
    | PaymentFailedEvent
    | PaymentCancelledEvent
    | PaymentExpiredEvent
    | RefundSucceededEvent
    | RefundFailedEvent
    | DisputeOpenedEvent
    | DisputeWonEvent
    | DisputeLostEvent,
    Field(discriminator="type"),
]
_PAYMENT_WEBHOOK_EVENT_ADAPTER = TypeAdapter(PaymentWebhookEvent)


def parse_payment_webhook_payload(payload: Mapping[str, object]) -> PaymentWebhookEvent:
    """Rehydrate a previously verified, JSON-safe inbox payload strictly."""

    try:
        raw_body = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return _PAYMENT_WEBHOOK_EVENT_ADAPTER.validate_json(raw_body, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise PaymentWebhookPayloadError() from None


class PaymentWebhookVerificationError(DomainError):
    def __init__(self, message: str = "Payment webhook signature is invalid") -> None:
        super().__init__(message, "payment_webhook_unauthorized", 401)


class PaymentWebhookPayloadError(DomainError):
    def __init__(self, message: str = "Payment webhook payload is invalid") -> None:
        super().__init__(message, "payment_webhook_invalid", 422)


@dataclass(frozen=True, slots=True)
class PaymentWebhookEvidence:
    """Non-sensitive receipt evidence safe to persist or return internally."""

    provider: str
    merchant_account: str
    key_id: str
    event_id: str
    delivery_timestamp: datetime
    payload_sha256: str


def payment_webhook_signing_input(
    *,
    provider: str,
    key_id: str,
    timestamp: str,
    event_id: str,
    raw_body: bytes,
) -> bytes:
    """Return the exact v1 HMAC input without retaining the request body."""

    return (
        b"v1."
        + provider.encode("ascii")
        + b"."
        + key_id.encode("ascii")
        + b"."
        + timestamp.encode("ascii")
        + b"."
        + event_id.encode("ascii")
        + b"."
        + raw_body
    )


class PaymentWebhookVerifier:
    """Verify one provider/key-id HMAC v1 contract against the raw body."""

    def __init__(
        self,
        signing_secret: str,
        *,
        provider: str,
        merchant_account: str,
        key_id: str,
        max_age_seconds: int = 300,
        max_body_bytes: int = MAX_PAYMENT_WEBHOOK_BODY_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(provider, str) or _PROVIDER_KEY.fullmatch(provider) is None:
            raise ValueError("Payment webhook provider is invalid")
        if (
            not isinstance(merchant_account, str)
            or _MERCHANT_ACCOUNT.fullmatch(merchant_account) is None
        ):
            raise ValueError("Payment webhook merchant account is invalid")
        if not isinstance(key_id, str) or _KEY_ID.fullmatch(key_id) is None:
            raise ValueError("Payment webhook key id is invalid")
        if not isinstance(signing_secret, str):
            raise ValueError("Payment webhook signing secret is invalid")
        secret = signing_secret.encode("utf-8")
        if len(secret) < 32:
            raise ValueError(
                "Payment webhook signing secret must contain at least 32 bytes"
            )
        if isinstance(max_age_seconds, bool) or max_age_seconds < 30:
            raise ValueError("Payment webhook replay window must be at least 30 seconds")
        if (
            isinstance(max_body_bytes, bool)
            or max_body_bytes < 1024
            or max_body_bytes > 1024 * 1024
        ):
            raise ValueError("Payment webhook body-size limit is invalid")
        self.provider = provider
        self.merchant_account = merchant_account
        self.key_id = key_id
        self._secret = secret
        self._max_age_seconds = max_age_seconds
        self._max_body_bytes = max_body_bytes
        self._clock = clock

    @staticmethod
    def _parse_payload(raw_body: bytes) -> PaymentWebhookEvent:
        try:
            decoded_text = raw_body.decode("utf-8")
            decoded = json.loads(
                decoded_text,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json_constant,
            )
            if not isinstance(decoded, dict):
                raise ValueError("payment webhook root must be an object")
            return _PAYMENT_WEBHOOK_EVENT_ADAPTER.validate_json(
                raw_body,
                strict=True,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ):
            raise PaymentWebhookPayloadError() from None

    def verify(
        self,
        raw_body: bytes,
        *,
        provider: str | None,
        key_id: str | None,
        event_id: str | None,
        timestamp: str | None,
        signature: str | None,
    ) -> tuple[PaymentWebhookEvent, PaymentWebhookEvidence]:
        if (
            not isinstance(raw_body, bytes)
            or not raw_body
            or len(raw_body) > self._max_body_bytes
        ):
            raise PaymentWebhookPayloadError()
        if not provider or not key_id or not event_id or not timestamp or not signature:
            raise PaymentWebhookVerificationError()
        if (
            _PROVIDER_KEY.fullmatch(provider) is None
            or _KEY_ID.fullmatch(key_id) is None
            or not hmac.compare_digest(provider, self.provider)
            or not hmac.compare_digest(key_id, self.key_id)
        ):
            raise PaymentWebhookVerificationError()
        try:
            canonical_event_id = str(UUID(event_id))
            timestamp_value = int(timestamp)
            delivery_timestamp = datetime.fromtimestamp(
                timestamp_value,
                tz=timezone.utc,
            )
        except (OverflowError, OSError, TypeError, ValueError):
            raise PaymentWebhookVerificationError() from None
        if canonical_event_id != event_id or str(timestamp_value) != timestamp:
            raise PaymentWebhookVerificationError()
        if abs(self._clock() - timestamp_value) > self._max_age_seconds:
            raise PaymentWebhookVerificationError(
                "Payment webhook timestamp is outside the replay window"
            )
        if not signature.startswith("v1=") or len(signature) != 67:
            raise PaymentWebhookVerificationError()
        supplied_digest = signature[3:]
        if any(character not in "0123456789abcdef" for character in supplied_digest):
            raise PaymentWebhookVerificationError()
        signing_input = payment_webhook_signing_input(
            provider=provider,
            key_id=key_id,
            timestamp=timestamp,
            event_id=event_id,
            raw_body=raw_body,
        )
        expected_digest = hmac.new(
            self._secret,
            signing_input,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise PaymentWebhookVerificationError()

        payload = self._parse_payload(raw_body)
        if (
            str(payload.event_id) != event_id
            or payload.provider != provider
            or payload.key_id != key_id
        ):
            raise PaymentWebhookPayloadError(
                "Payment webhook headers do not match the signed payload"
            )
        return payload, PaymentWebhookEvidence(
            provider=provider,
            merchant_account=self.merchant_account,
            key_id=key_id,
            event_id=event_id,
            delivery_timestamp=delivery_timestamp,
            payload_sha256=hashlib.sha256(raw_body).hexdigest(),
        )


class PaymentWebhookVerifierRegistry:
    """Resolve a rotated webhook verifier by explicit provider and key id."""

    def __init__(
        self,
        verifiers: (
            Mapping[tuple[str, str], PaymentWebhookVerifier]
            | Iterable[PaymentWebhookVerifier]
        ) = (),
    ) -> None:
        registered: dict[tuple[str, str], PaymentWebhookVerifier] = {}
        secret_merchants: dict[bytes, tuple[str, str]] = {}
        if isinstance(verifiers, Mapping):
            candidates = tuple(verifiers.items())
        else:
            candidates = tuple(
                ((verifier.provider, verifier.key_id), verifier)
                for verifier in verifiers
            )
        for configured_identity, verifier in candidates:
            identity = (verifier.provider, verifier.key_id)
            if configured_identity != identity:
                raise ValueError("Payment webhook verifier registry identity is invalid")
            if identity in registered:
                raise ValueError("Payment webhook verifier is registered more than once")
            secret_fingerprint = hashlib.sha256(verifier._secret).digest()
            merchant_identity = (verifier.provider, verifier.merchant_account)
            previous_merchant = secret_merchants.get(secret_fingerprint)
            if previous_merchant is not None and previous_merchant != merchant_identity:
                raise ValueError(
                    "Payment webhook signing secret must not be shared across merchants"
                )
            secret_merchants[secret_fingerprint] = merchant_identity
            registered[identity] = verifier
        self._registered = registered

    @property
    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._registered))

    def resolve(self, provider: str, key_id: str) -> PaymentWebhookVerifier:
        if (
            not isinstance(provider, str)
            or not isinstance(key_id, str)
            or _PROVIDER_KEY.fullmatch(provider) is None
            or _KEY_ID.fullmatch(key_id) is None
        ):
            raise PaymentWebhookVerificationError()
        verifier = self._registered.get((provider, key_id))
        if verifier is None:
            raise PaymentWebhookVerificationError()
        return verifier

    def verify(
        self,
        raw_body: bytes,
        *,
        provider: str | None,
        key_id: str | None,
        event_id: str | None,
        timestamp: str | None,
        signature: str | None,
    ) -> tuple[PaymentWebhookEvent, PaymentWebhookEvidence]:
        if not provider or not key_id:
            raise PaymentWebhookVerificationError()
        return self.resolve(provider, key_id).verify(
            raw_body,
            provider=provider,
            key_id=key_id,
            event_id=event_id,
            timestamp=timestamp,
            signature=signature,
        )
