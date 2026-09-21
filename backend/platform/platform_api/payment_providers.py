from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal, Mapping, Protocol, runtime_checkable
from uuid import UUID


MAX_PAYMENT_AMOUNT_CENTS = 9_000_000_000_000_000
MAX_PAYMENT_POINTS = 9_000_000_000_000_000

PAYMENT_PROVIDER_KEY_PATTERN = r"[a-z][a-z0-9._-]{0,63}"
PAYMENT_PROVIDER_RESOURCE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
PAYMENT_MERCHANT_ACCOUNT_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}"
_PROVIDER_KEY = re.compile(rf"^{PAYMENT_PROVIDER_KEY_PATTERN}$")
_PROVIDER_RESOURCE_ID = re.compile(rf"^{PAYMENT_PROVIDER_RESOURCE_ID_PATTERN}$")
_MERCHANT_ACCOUNT = re.compile(rf"^{PAYMENT_MERCHANT_ACCOUNT_PATTERN}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$")


class PaymentProviderConfigurationError(ValueError):
    """Raised when a provider boundary is configured unsafely."""


class PaymentProviderUnavailableError(LookupError):
    """Raised when no explicitly configured provider matches a request."""


class PaymentProviderRequestError(ValueError):
    """Raised before an invalid financial intent reaches a provider."""


def _require_canonical_uuid(value: str, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise PaymentProviderRequestError(f"{field_name} must be a canonical UUID")
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise PaymentProviderRequestError(
            f"{field_name} must be a canonical UUID"
        ) from None
    if canonical != value:
        raise PaymentProviderRequestError(f"{field_name} must be a canonical UUID")


def _require_positive_integer(
    value: int,
    *,
    field_name: str,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise PaymentProviderRequestError(f"{field_name} must be a positive integer")


def _require_currency(value: str) -> None:
    if not isinstance(value, str) or _CURRENCY.fullmatch(value) is None:
        raise PaymentProviderRequestError("currency must be a three-letter ISO code")


def _require_idempotency_key(value: str) -> None:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise PaymentProviderRequestError("idempotency_key is invalid")


def _require_provider_resource_id(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or _PROVIDER_RESOURCE_ID.fullmatch(value) is None:
        raise PaymentProviderRequestError(f"{field_name} is invalid")


@dataclass(frozen=True, slots=True)
class CreatePaymentRequest:
    """Minimal, non-sensitive intent passed to an external payment provider."""

    order_id: str
    amount_cents: int
    points: int
    currency: str
    idempotency_key: str
    purpose: Literal["point_purchase", "invoice_payment"] = "point_purchase"
    off_session: bool = False
    provider_customer_reference: str | None = None
    provider_payment_method_reference: str | None = None
    provider_mandate_reference: str | None = None

    def __post_init__(self) -> None:
        _require_canonical_uuid(self.order_id, field_name="order_id")
        _require_positive_integer(
            self.amount_cents,
            field_name="amount_cents",
            maximum=MAX_PAYMENT_AMOUNT_CENTS,
        )
        if isinstance(self.points, bool) or not isinstance(self.points, int):
            raise PaymentProviderRequestError("points must be an integer")
        if self.purpose == "point_purchase":
            _require_positive_integer(
                self.points,
                field_name="points",
                maximum=MAX_PAYMENT_POINTS,
            )
        elif self.purpose == "invoice_payment":
            if self.points != 0:
                raise PaymentProviderRequestError(
                    "invoice payment points must be zero"
                )
        else:  # pragma: no cover - Literal callers still need runtime defence.
            raise PaymentProviderRequestError("purpose is invalid")
        _require_currency(self.currency)
        _require_idempotency_key(self.idempotency_key)
        mandate_references = (
            self.provider_customer_reference,
            self.provider_payment_method_reference,
            self.provider_mandate_reference,
        )
        if not isinstance(self.off_session, bool):
            raise PaymentProviderRequestError("off_session must be a boolean")
        if self.off_session:
            if self.purpose != "point_purchase":
                raise PaymentProviderRequestError(
                    "off-session payment is only valid for point purchases"
                )
            if any(reference is None for reference in mandate_references):
                raise PaymentProviderRequestError(
                    "off-session payment requires verified mandate references"
                )
            for field_name, reference in zip(
                (
                    "provider_customer_reference",
                    "provider_payment_method_reference",
                    "provider_mandate_reference",
                ),
                mandate_references,
                strict=True,
            ):
                _require_provider_resource_id(reference, field_name=field_name)  # type: ignore[arg-type]
        elif any(reference is not None for reference in mandate_references):
            raise PaymentProviderRequestError(
                "interactive payment must not contain off-session mandate references"
            )


@dataclass(frozen=True, slots=True)
class CreatePaymentResult:
    provider: str
    provider_payment_id: str
    status: Literal["pending", "captured"]
    checkout_url: str | None = None

    def __post_init__(self) -> None:
        if _PROVIDER_KEY.fullmatch(self.provider) is None:
            raise PaymentProviderConfigurationError("payment provider key is invalid")
        _require_provider_resource_id(
            self.provider_payment_id,
            field_name="provider_payment_id",
        )
        if self.checkout_url is not None and not self.checkout_url.startswith("https://"):
            raise PaymentProviderConfigurationError(
                "payment checkout URL must use HTTPS"
            )


@dataclass(frozen=True, slots=True)
class CreateRefundRequest:
    refund_id: str
    order_id: str
    provider_payment_id: str
    amount_cents: int
    currency: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_canonical_uuid(self.refund_id, field_name="refund_id")
        _require_canonical_uuid(self.order_id, field_name="order_id")
        _require_provider_resource_id(
            self.provider_payment_id,
            field_name="provider_payment_id",
        )
        _require_positive_integer(
            self.amount_cents,
            field_name="amount_cents",
            maximum=MAX_PAYMENT_AMOUNT_CENTS,
        )
        _require_currency(self.currency)
        _require_idempotency_key(self.idempotency_key)


@dataclass(frozen=True, slots=True)
class CreateRefundResult:
    provider: str
    provider_refund_id: str
    status: Literal["pending", "succeeded", "failed"]

    def __post_init__(self) -> None:
        if _PROVIDER_KEY.fullmatch(self.provider) is None:
            raise PaymentProviderConfigurationError("payment provider key is invalid")
        _require_provider_resource_id(
            self.provider_refund_id,
            field_name="provider_refund_id",
        )


@dataclass(frozen=True, slots=True)
class QueryPaymentRequest:
    """Lookup a previously submitted payment by its stable server intent."""

    order_id: str
    idempotency_key: str
    provider_payment_id: str | None = None

    def __post_init__(self) -> None:
        _require_canonical_uuid(self.order_id, field_name="order_id")
        _require_idempotency_key(self.idempotency_key)
        if self.provider_payment_id is not None:
            _require_provider_resource_id(
                self.provider_payment_id,
                field_name="provider_payment_id",
            )


@dataclass(frozen=True, slots=True)
class QueryPaymentResult:
    provider: str
    found: bool
    provider_payment_id: str | None = None
    status: Literal[
        "pending",
        "requires_action",
        "captured",
        "failed",
        "cancelled",
        "expired",
    ] | None = None
    amount_cents: int | None = None
    currency: str | None = None
    checkout_url: str | None = None
    occurred_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if _PROVIDER_KEY.fullmatch(self.provider) is None:
            raise PaymentProviderConfigurationError("payment provider key is invalid")
        if self.status not in {
            "pending",
            "requires_action",
            "captured",
            "failed",
            "cancelled",
            "expired",
            None,
        }:
            raise PaymentProviderConfigurationError("payment query status is invalid")
        values = (
            self.provider_payment_id,
            self.status,
            self.amount_cents,
            self.currency,
        )
        if not self.found:
            if (
                any(value is not None for value in values)
                or self.checkout_url is not None
                or self.occurred_at is not None
                or self.failure_code is not None
            ):
                raise PaymentProviderConfigurationError(
                    "missing payment query result must not contain financial data"
                )
            return
        if any(value is None for value in values):
            raise PaymentProviderConfigurationError(
                "found payment query result is incomplete"
            )
        _require_provider_resource_id(
            self.provider_payment_id,  # type: ignore[arg-type]
            field_name="provider_payment_id",
        )
        _require_positive_integer(
            self.amount_cents,  # type: ignore[arg-type]
            field_name="amount_cents",
            maximum=MAX_PAYMENT_AMOUNT_CENTS,
        )
        _require_currency(self.currency)  # type: ignore[arg-type]
        if self.status == "requires_action":
            if self.checkout_url is None or not self.checkout_url.startswith("https://"):
                raise PaymentProviderConfigurationError(
                    "requires-action payment query result requires an HTTPS checkout URL"
                )
        elif self.checkout_url is not None:
            raise PaymentProviderConfigurationError(
                "non-action payment query result must not contain a checkout URL"
            )
        if self.status in {"captured", "failed", "cancelled", "expired"}:
            if self.occurred_at is None or self.occurred_at.tzinfo is None:
                raise PaymentProviderConfigurationError(
                    "terminal payment query result requires occurred_at"
                )
            if self.status == "failed" and self.failure_code is None:
                raise PaymentProviderConfigurationError(
                    "failed payment query result requires failure_code"
                )
            if self.status == "captured" and self.failure_code is not None:
                raise PaymentProviderConfigurationError(
                    "captured payment query result must not contain failure_code"
                )
        elif self.failure_code is not None:
            raise PaymentProviderConfigurationError(
                "non-terminal payment query result must not contain failure_code"
            )


@dataclass(frozen=True, slots=True)
class QueryRefundRequest:
    """Lookup a refund even when create_refund lost its response."""

    refund_id: str
    order_id: str
    provider_payment_id: str
    idempotency_key: str
    provider_refund_id: str | None = None

    def __post_init__(self) -> None:
        _require_canonical_uuid(self.refund_id, field_name="refund_id")
        _require_canonical_uuid(self.order_id, field_name="order_id")
        _require_provider_resource_id(
            self.provider_payment_id,
            field_name="provider_payment_id",
        )
        _require_idempotency_key(self.idempotency_key)
        if self.provider_refund_id is not None:
            _require_provider_resource_id(
                self.provider_refund_id,
                field_name="provider_refund_id",
            )


@dataclass(frozen=True, slots=True)
class QueryRefundResult:
    provider: str
    found: bool
    provider_refund_id: str | None = None
    status: Literal["pending", "succeeded", "failed"] | None = None
    amount_cents: int | None = None
    currency: str | None = None
    occurred_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if _PROVIDER_KEY.fullmatch(self.provider) is None:
            raise PaymentProviderConfigurationError("payment provider key is invalid")
        if self.status not in {"pending", "succeeded", "failed", None}:
            raise PaymentProviderConfigurationError("refund query status is invalid")
        values = (
            self.provider_refund_id,
            self.status,
            self.amount_cents,
            self.currency,
        )
        if not self.found:
            if (
                any(value is not None for value in values)
                or self.occurred_at is not None
                or self.failure_code is not None
            ):
                raise PaymentProviderConfigurationError(
                    "missing refund query result must not contain financial data"
                )
            return
        if any(value is None for value in values):
            raise PaymentProviderConfigurationError(
                "found refund query result is incomplete"
            )
        _require_provider_resource_id(
            self.provider_refund_id,  # type: ignore[arg-type]
            field_name="provider_refund_id",
        )
        _require_positive_integer(
            self.amount_cents,  # type: ignore[arg-type]
            field_name="amount_cents",
            maximum=MAX_PAYMENT_AMOUNT_CENTS,
        )
        _require_currency(self.currency)  # type: ignore[arg-type]
        if self.status in {"succeeded", "failed"}:
            if self.occurred_at is None or self.occurred_at.tzinfo is None:
                raise PaymentProviderConfigurationError(
                    "terminal refund query result requires occurred_at"
                )
            if self.status == "failed" and self.failure_code is None:
                raise PaymentProviderConfigurationError(
                    "failed refund query result requires failure_code"
                )
            if self.status == "succeeded" and self.failure_code is not None:
                raise PaymentProviderConfigurationError(
                    "succeeded refund query result must not contain failure_code"
                )
        elif self.failure_code is not None:
            raise PaymentProviderConfigurationError(
                "pending refund query result must not contain failure_code"
            )


@runtime_checkable
class PaymentProvider(Protocol):
    """Outbound provider boundary.

    Implementations receive only server-created financial identifiers and
    integer amounts. Card, bank-account, credential, and provider response
    bodies are intentionally outside this protocol.
    """

    provider_key: str
    merchant_account: str
    test_only: bool

    async def create_payment(
        self,
        request: CreatePaymentRequest,
    ) -> CreatePaymentResult: ...

    async def create_refund(
        self,
        request: CreateRefundRequest,
    ) -> CreateRefundResult: ...

    async def query_payment(
        self,
        request: QueryPaymentRequest,
    ) -> QueryPaymentResult: ...

    async def query_refund(
        self,
        request: QueryRefundRequest,
    ) -> QueryRefundResult: ...


class PaymentProviderRegistry:
    """Resolve only explicitly registered providers; there is no fallback."""

    def __init__(
        self,
        providers: (
            Mapping[tuple[str, str], PaymentProvider] | Iterable[PaymentProvider]
        ) = (),
        *,
        allow_test_providers: bool = False,
    ) -> None:
        registered: dict[tuple[str, str], PaymentProvider] = {}
        if isinstance(providers, Mapping):
            candidates = tuple(providers.items())
        else:
            candidates = tuple(
                (
                    (
                        getattr(provider, "provider_key", ""),
                        getattr(provider, "merchant_account", ""),
                    ),
                    provider,
                )
                for provider in providers
            )
        for configured_identity, provider in candidates:
            provider_key = getattr(provider, "provider_key", "")
            merchant_account = getattr(provider, "merchant_account", "")
            identity = (provider_key, merchant_account)
            if (
                not isinstance(provider_key, str)
                or _PROVIDER_KEY.fullmatch(provider_key) is None
                or not isinstance(merchant_account, str)
                or _MERCHANT_ACCOUNT.fullmatch(merchant_account) is None
                or configured_identity != identity
            ):
                raise PaymentProviderConfigurationError(
                    "payment provider registry provider/merchant identity is invalid"
                )
            if identity in registered:
                raise PaymentProviderConfigurationError(
                    "payment provider/merchant is registered more than once"
                )
            required_methods = (
                "create_payment",
                "create_refund",
                "query_payment",
                "query_refund",
            )
            if any(
                not callable(getattr(provider, method_name, None))
                for method_name in required_methods
            ):
                raise PaymentProviderConfigurationError(
                    "payment provider does not implement the required boundary"
                )
            if bool(getattr(provider, "test_only", False)) and not allow_test_providers:
                raise PaymentProviderConfigurationError(
                    "test-only payment providers require an explicit test opt-in"
                )
            registered[identity] = provider
        self._registered = registered

    @property
    def provider_keys(self) -> tuple[str, ...]:
        return tuple(sorted({provider for provider, _ in self._registered}))

    @property
    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._registered))

    def resolve(
        self,
        provider_key: str,
        merchant_account: str | None = None,
    ) -> PaymentProvider:
        if (
            not isinstance(provider_key, str)
            or _PROVIDER_KEY.fullmatch(provider_key) is None
            or not isinstance(merchant_account, str)
            or _MERCHANT_ACCOUNT.fullmatch(merchant_account) is None
        ):
            raise PaymentProviderUnavailableError("payment provider is unavailable")
        provider = self._registered.get((provider_key, merchant_account))
        if provider is None:
            raise PaymentProviderUnavailableError("payment provider is unavailable")
        return provider


class DeterministicFakePaymentProvider:
    """Deterministic provider double guarded by an explicit test-only switch."""

    provider_key = "deterministic-test"
    merchant_account = "merchant-cny-main"
    test_only = True

    def __init__(self, *, enabled_for_tests: bool = False) -> None:
        if not enabled_for_tests:
            raise PaymentProviderConfigurationError(
                "deterministic fake payment provider is test-only"
            )
        self._payments_by_key: dict[
            str, tuple[CreatePaymentRequest, CreatePaymentResult]
        ] = {}
        self._refunds_by_key: dict[
            str, tuple[CreateRefundRequest, CreateRefundResult]
        ] = {}

    @staticmethod
    def _digest(kind: str, payload: dict[str, object]) -> str:
        canonical = json.dumps(
            {"kind": kind, **payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()[:32]

    async def create_payment(
        self,
        request: CreatePaymentRequest,
    ) -> CreatePaymentResult:
        digest = self._digest(
            "payment",
            {
                "order_id": request.order_id,
                "amount_cents": request.amount_cents,
                "points": request.points,
                "purpose": request.purpose,
                "currency": request.currency,
                "idempotency_key": request.idempotency_key,
                "off_session": request.off_session,
                "provider_customer_reference": request.provider_customer_reference,
                "provider_payment_method_reference": (
                    request.provider_payment_method_reference
                ),
                "provider_mandate_reference": request.provider_mandate_reference,
            },
        )
        result = CreatePaymentResult(
            provider=self.provider_key,
            provider_payment_id=f"pay_{digest}",
            status="pending",
            checkout_url=None,
        )
        existing = self._payments_by_key.get(request.idempotency_key)
        if existing is not None and existing[0] != request:
            raise PaymentProviderRequestError(
                "payment idempotency key was reused for a different intent"
            )
        self._payments_by_key[request.idempotency_key] = (request, result)
        return result

    async def create_refund(
        self,
        request: CreateRefundRequest,
    ) -> CreateRefundResult:
        digest = self._digest(
            "refund",
            {
                "refund_id": request.refund_id,
                "order_id": request.order_id,
                "provider_payment_id": request.provider_payment_id,
                "amount_cents": request.amount_cents,
                "currency": request.currency,
                "idempotency_key": request.idempotency_key,
            },
        )
        result = CreateRefundResult(
            provider=self.provider_key,
            provider_refund_id=f"re_{digest}",
            status="pending",
        )
        existing = self._refunds_by_key.get(request.idempotency_key)
        if existing is not None and existing[0] != request:
            raise PaymentProviderRequestError(
                "refund idempotency key was reused for a different intent"
            )
        self._refunds_by_key[request.idempotency_key] = (request, result)
        return result

    async def query_payment(
        self,
        request: QueryPaymentRequest,
    ) -> QueryPaymentResult:
        stored = self._payments_by_key.get(request.idempotency_key)
        if stored is None:
            return QueryPaymentResult(provider=self.provider_key, found=False)
        intent, result = stored
        if intent.order_id != request.order_id or (
            request.provider_payment_id is not None
            and request.provider_payment_id != result.provider_payment_id
        ):
            raise PaymentProviderRequestError(
                "payment query does not match the submitted intent"
            )
        return QueryPaymentResult(
            provider=self.provider_key,
            found=True,
            provider_payment_id=result.provider_payment_id,
            status=result.status,
            amount_cents=intent.amount_cents,
            currency=intent.currency,
            checkout_url=result.checkout_url,
        )

    async def query_refund(
        self,
        request: QueryRefundRequest,
    ) -> QueryRefundResult:
        stored = self._refunds_by_key.get(request.idempotency_key)
        if stored is None:
            return QueryRefundResult(provider=self.provider_key, found=False)
        intent, result = stored
        if (
            intent.refund_id != request.refund_id
            or intent.order_id != request.order_id
            or intent.provider_payment_id != request.provider_payment_id
            or (
                request.provider_refund_id is not None
                and request.provider_refund_id != result.provider_refund_id
            )
        ):
            raise PaymentProviderRequestError(
                "refund query does not match the submitted intent"
            )
        return QueryRefundResult(
            provider=self.provider_key,
            found=True,
            provider_refund_id=result.provider_refund_id,
            status=result.status,
            amount_cents=intent.amount_cents,
            currency=intent.currency,
        )
