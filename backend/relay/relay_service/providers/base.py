from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Mapping

from ..models import GenerationJob, ModelCapability, ProviderWebhookEvent


_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
PROVIDER_ADAPTER_CONTRACT_VERSION = 1


class ProviderChannelType(StrEnum):
    """Operational class of one upstream channel.

    Mock is intentionally separate from the three production channel classes
    so a development adapter can never be mistaken for a real route.
    """

    REVERSE = "reverse"
    THIRD_PARTY_API = "third_party_api"
    OFFICIAL = "official"
    MOCK = "mock"


class ProviderFailureScope(StrEnum):
    """How broadly a proven pre-creation provider failure applies."""

    REQUEST = "request"
    ACCOUNT = "account"
    CHANNEL = "channel"


@dataclass(frozen=True)
class ProviderRouteManifest:
    """Secret-free metadata declared by one configured provider account."""

    contract_version: int
    route_id: str
    provider_name: str
    account_id: str
    channel_type: ProviderChannelType
    priority: int
    max_concurrency: int | None
    requests_per_minute: int | None
    production_ready: bool


class ProviderContractError(RuntimeError):
    """A configured adapter violates the versioned Relay plugin contract."""


def validate_provider_identity(
    *,
    name: str,
    account_id: str,
    max_concurrency: int | None,
    requests_per_minute: int | None = None,
) -> None:
    for field_name, value in (("name", name), ("account_id", account_id)):
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 64
            or _IDENTITY_PATTERN.fullmatch(value) is None
        ):
            raise ValueError(
                f"provider {field_name} must be a 1-64 character safe identifier"
            )
    route_id = name if account_id == "default" else f"{name}@{account_id}"
    if len(route_id) > 128:
        raise ValueError("provider route identity exceeds 128 characters")
    if (
        max_concurrency is not None
        and (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        )
    ):
        raise ValueError("provider max_concurrency must be a positive integer")
    if (
        requests_per_minute is not None
        and (
            isinstance(requests_per_minute, bool)
            or not isinstance(requests_per_minute, int)
            or requests_per_minute < 1
        )
    ):
        raise ValueError(
            "provider requests_per_minute must be a positive integer"
        )


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        fail_job: bool = False,
        submission_outcome_unknown: bool = False,
        account_unavailable: bool | None = None,
        disable_account: bool = False,
        retry_after_seconds: float | None = None,
        failure_scope: ProviderFailureScope | str | None = None,
    ) -> None:
        if not isinstance(code, str) or _ERROR_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(
                "provider error code must be a stable uppercase identifier"
            )
        if submission_outcome_unknown and (
            retryable or account_unavailable is True or disable_account
        ):
            raise ValueError(
                "an unknown submission outcome cannot be retried or failed over"
            )
        if disable_account and account_unavailable is not True:
            raise ValueError(
                "disable_account requires account_unavailable=True"
            )
        try:
            explicit_scope = (
                ProviderFailureScope(failure_scope)
                if failure_scope is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("failure_scope is invalid") from exc
        resolved_account_unavailable = account_unavailable
        if resolved_account_unavailable is None:
            if explicit_scope is not None:
                resolved_account_unavailable = (
                    explicit_scope == ProviderFailureScope.ACCOUNT
                )
            else:
                # Backward-compatible default for existing adapters. New
                # adapters should always declare the failure scope explicitly.
                resolved_account_unavailable = retryable
        normalized_scope = explicit_scope or (
            ProviderFailureScope.ACCOUNT
            if resolved_account_unavailable
            else ProviderFailureScope.REQUEST
        )
        if normalized_scope == ProviderFailureScope.CHANNEL and (
            resolved_account_unavailable or disable_account
        ):
            raise ValueError(
                "channel failures cannot disable one concrete account"
            )
        if retry_after_seconds is not None and (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, (int, float))
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds <= 0
        ):
            raise ValueError("retry_after_seconds must be positive")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.fail_job = fail_job
        self.submission_outcome_unknown = submission_outcome_unknown
        self.account_unavailable = resolved_account_unavailable
        self.disable_account = disable_account
        self.retry_after_seconds = (
            float(retry_after_seconds)
            if retry_after_seconds is not None
            else None
        )
        self.failure_scope = normalized_scope
        self.route_id: str | None = None


@dataclass(frozen=True)
class ProviderSubmission:
    provider_task_id: str

    def __post_init__(self) -> None:
        value = self.provider_task_id
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 256
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("provider_task_id must be a valid non-empty string")


class ProviderAdapter(ABC):
    """Contract implemented once per real upstream provider."""

    contract_version: int = PROVIDER_ADAPTER_CONTRACT_VERSION
    name: str
    account_id: str = "default"
    # Every concrete adapter must choose a class. Keeping the base value empty
    # prevents an omitted declaration from being silently treated as a third-
    # party API channel.
    channel_type: ProviderChannelType | None = None
    priority: int = 100
    max_concurrency: int | None = None
    # Fixed-window admission limit shared by every Relay process. This counts
    # provider create attempts, not polling calls or local queue deliveries.
    requests_per_minute: int | None = None
    # Real adapters must explicitly opt in after their credentials, signature
    # verification and idempotent submission behavior have been implemented.
    production_ready: bool = False

    @property
    def route_id(self) -> str:
        """Stable internal route persisted on jobs for account stickiness."""

        validate_provider_identity(
            name=self.name,
            account_id=self.account_id,
            max_concurrency=self.max_concurrency,
            requests_per_minute=self.requests_per_minute,
        )
        if self.account_id == "default":
            return self.name
        return f"{self.name}@{self.account_id}"

    @property
    def manifest(self) -> ProviderRouteManifest:
        """Return validated routing metadata without credentials or URLs."""

        route_id = self.route_id
        if (
            isinstance(self.contract_version, bool)
            or not isinstance(self.contract_version, int)
            or self.contract_version != PROVIDER_ADAPTER_CONTRACT_VERSION
        ):
            raise ValueError(
                "provider adapter contract_version is not supported"
            )
        try:
            channel_type = ProviderChannelType(self.channel_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "provider channel_type must be declared explicitly"
            ) from exc
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or self.priority < 0
        ):
            raise ValueError("provider priority must be a non-negative integer")
        if not isinstance(self.production_ready, bool):
            raise ValueError("provider production_ready must be a boolean")
        return ProviderRouteManifest(
            contract_version=self.contract_version,
            route_id=route_id,
            provider_name=self.name,
            account_id=self.account_id,
            channel_type=channel_type,
            priority=self.priority,
            max_concurrency=self.max_concurrency,
            requests_per_minute=self.requests_per_minute,
            production_ready=self.production_ready,
        )

    @abstractmethod
    async def capabilities(self) -> list[ModelCapability]: ...

    @abstractmethod
    async def healthcheck(self) -> bool: ...

    @abstractmethod
    async def submit(self, job: GenerationJob) -> ProviderSubmission:
        """Submit one job with a stable upstream correlation identity.

        A delivery can be retried after an ambiguous network failure, process
        loss, or claim-renewal failure. The claim heartbeat only fences live
        Relay workers; it cannot prove that a cancelled HTTP request did not
        commit upstream. Where the provider officially guarantees idempotency,
        the adapter must pass the exact ``job.id`` to that facility. Otherwise
        it must fence ambiguous POST outcomes from automatic retries and expose
        an operator reconciliation path; a correlation field alone must not be
        described as an idempotency guarantee.
        """

    async def parse_webhook(
        self, body: bytes, headers: Mapping[str, str]
    ) -> ProviderWebhookEvent:
        """Verify the upstream signature before parsing. Raise on failure."""

        raise ProviderError(
            "WEBHOOK_NOT_SUPPORTED",
            "This provider does not expose a verified webhook contract",
            retryable=False,
        )

    async def poll(self, job: GenerationJob) -> ProviderWebhookEvent | None:
        """Return the latest upstream event, or ``None`` when polling is absent."""

        return None

    async def close(self) -> None:
        """Release adapter-owned transports."""
