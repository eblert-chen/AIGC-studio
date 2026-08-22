from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from asyncio import Lock
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from .callback import (
    CallbackConfigurationError,
    CallbackTransport,
    CallbackTransportError,
    sign_callback,
)


logger = logging.getLogger("relay.provider-monitor")


class ProviderAlertKind(StrEnum):
    SUCCESS_RATE_DROP = "provider_success_rate_drop"
    WIDESPREAD_CHANNEL_FAILURE = "widespread_channel_failure"
    BATCH_ACCOUNT_INVALIDATION = "batch_account_invalidation"


class ProviderAlertEventType(StrEnum):
    TRIGGERED = "triggered"
    RECOVERED = "recovered"


class ProviderAlertDeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class ProviderHealthSample:
    route_id: str
    provider_name: str
    account_id: str
    channel_type: str
    healthy: bool
    admission_enabled: bool
    checked_at: datetime
    error_code: str | None = None
    latency_ms: int | None = None
    admission_disabled_reason: str | None = None


@dataclass(frozen=True)
class ProviderOutcomeSummary:
    route_id: str
    provider_name: str
    succeeded: int
    failed: int

    @property
    def total(self) -> int:
        return self.succeeded + self.failed


@dataclass(frozen=True)
class ProviderAlertEvent:
    id: UUID
    fingerprint: str
    kind: ProviderAlertKind
    event_type: ProviderAlertEventType
    provider_name: str
    occurred_at: datetime
    details: dict[str, object]
    delivery_status: ProviderAlertDeliveryStatus = (
        ProviderAlertDeliveryStatus.PENDING
    )
    attempts: int = 0
    response_status: int | None = None
    last_error: str | None = None

    @property
    def severity(self) -> str:
        if self.kind in {
            ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE,
            ProviderAlertKind.BATCH_ACCOUNT_INVALIDATION,
        }:
            return "critical"
        return "warning"


@dataclass(frozen=True)
class ProviderAlertClaim:
    event: ProviderAlertEvent
    token: UUID


@dataclass(frozen=True)
class ProviderMonitoringStatus:
    observed_at: datetime
    last_successful_cycle_at: datetime | None
    active_alert_count: int
    pending_delivery_count: int
    oldest_pending_at: datetime | None
    dead_letter_count: int
    oldest_dead_letter_at: datetime | None


@dataclass(frozen=True)
class ProviderMonitorCycleClaim:
    token: UUID
    observed_at: datetime


@dataclass(frozen=True)
class ProviderAlertCondition:
    fingerprint: str
    kind: ProviderAlertKind
    provider_name: str
    # None means that this cycle did not contain enough evidence to advance
    # either the breach or recovery counter.
    breached: bool | None
    details: dict[str, object]


@dataclass(frozen=True)
class ProviderMonitorPolicy:
    outcome_window: timedelta = timedelta(minutes=5)
    min_outcomes: int = 20
    min_success_rate: float = 0.80
    widespread_failure_ratio: float = 0.50
    widespread_failure_min_routes: int = 2
    batch_disabled_threshold: int = 3
    breach_cycles: int = 2
    recovery_cycles: int = 2
    cycle_lease: timedelta = timedelta(seconds=120)
    cycle_interval: timedelta = timedelta(seconds=30)
    sample_retention: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        if self.outcome_window <= timedelta(0):
            raise ValueError("outcome_window must be positive")
        if self.min_outcomes < 1:
            raise ValueError("min_outcomes must be positive")
        if not 0 <= self.min_success_rate <= 1:
            raise ValueError("min_success_rate must be between zero and one")
        if not 0 <= self.widespread_failure_ratio <= 1:
            raise ValueError(
                "widespread_failure_ratio must be between zero and one"
            )
        if self.widespread_failure_min_routes < 2:
            raise ValueError(
                "widespread_failure_min_routes must be at least two"
            )
        for field_name in (
            "batch_disabled_threshold",
            "breach_cycles",
            "recovery_cycles",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be positive")
        for field_name in (
            "cycle_lease",
            "cycle_interval",
            "sample_retention",
        ):
            if getattr(self, field_name) <= timedelta(0):
                raise ValueError(f"{field_name} must be positive")
        if self.cycle_lease < self.cycle_interval:
            raise ValueError("cycle_lease must not be shorter than cycle_interval")


class HealthProbeRouter(Protocol):
    async def probe_health(
        self, *, at: datetime | None = None
    ) -> list[ProviderHealthSample]: ...


class ProviderMonitoringRepository(ABC):
    @abstractmethod
    async def claim_provider_monitor_cycle(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        minimum_interval: timedelta,
    ) -> ProviderMonitorCycleClaim | None: ...

    @abstractmethod
    async def finish_provider_monitor_cycle(self, token: UUID) -> bool: ...

    @abstractmethod
    async def commit_provider_monitor_cycle(
        self,
        token: UUID,
        samples: list[ProviderHealthSample],
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        retention_before: datetime,
        now: datetime,
        lease_valid_at: datetime,
    ) -> tuple[bool, list[ProviderAlertEvent]]:
        """Atomically fence, persist, evaluate and release one cycle."""

    @abstractmethod
    async def record_provider_health_samples(
        self, samples: list[ProviderHealthSample]
    ) -> None: ...

    @abstractmethod
    async def prune_provider_health_samples(self, *, before: datetime) -> int: ...

    @abstractmethod
    async def provider_outcome_summaries(
        self, *, since: datetime
    ) -> list[ProviderOutcomeSummary]: ...

    @abstractmethod
    async def apply_provider_alert_conditions(
        self,
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        now: datetime,
    ) -> list[ProviderAlertEvent]: ...

    @abstractmethod
    async def claim_provider_alert_deliveries(
        self,
        *,
        batch_size: int,
        exclude_ids: set[UUID] | None = None,
        lease: timedelta = timedelta(seconds=60),
        max_attempts: int | None = None,
    ) -> list[ProviderAlertClaim]: ...

    @abstractmethod
    async def mark_provider_alert_delivered(
        self,
        event_id: UUID,
        *,
        token: UUID,
        response_status: int,
    ) -> bool: ...

    @abstractmethod
    async def release_provider_alert_delivery(
        self,
        event_id: UUID,
        *,
        token: UUID,
        error: str,
        retry_delay: timedelta,
        dead_letter: bool,
        response_status: int | None = None,
    ) -> bool: ...

    @abstractmethod
    async def list_provider_alert_events(
        self, *, limit: int = 100
    ) -> list[ProviderAlertEvent]: ...

    @abstractmethod
    async def provider_monitoring_status(self) -> ProviderMonitoringStatus: ...


@dataclass
class _MemoryAlertState:
    condition: ProviderAlertCondition
    active: bool = False
    breach_count: int = 0
    recovery_count: int = 0
    opened_event: ProviderAlertEvent | None = None


@dataclass
class _MemoryDelivery:
    status: ProviderAlertDeliveryStatus = ProviderAlertDeliveryStatus.PENDING
    attempts: int = 0
    available_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    claim_token: UUID | None = None
    claim_expires_at: datetime | None = None
    response_status: int | None = None
    last_error: str | None = None


class InMemoryProviderMonitoringRepository(ProviderMonitoringRepository):
    """Deterministic monitor repository for tests and local development."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._samples: list[ProviderHealthSample] = []
        self._outcome_summaries: list[ProviderOutcomeSummary] = []
        self._states: dict[str, _MemoryAlertState] = {}
        self._events: list[ProviderAlertEvent] = []
        self._deliveries: dict[UUID, _MemoryDelivery] = {}
        self._lease_token: UUID | None = None
        self._lease_expires_at: datetime | None = None
        self._next_cycle_at: datetime | None = None
        self._last_successful_cycle_at: datetime | None = None

    async def claim_provider_monitor_cycle(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        minimum_interval: timedelta,
    ) -> ProviderMonitorCycleClaim | None:
        del worker_id
        async with self._lock:
            if (
                self._lease_token is not None
                and self._lease_expires_at is not None
                and self._lease_expires_at > now
            ):
                return None
            if self._next_cycle_at is not None and self._next_cycle_at > now:
                return None
            token = uuid4()
            self._lease_token = token
            self._lease_expires_at = now + lease
            self._next_cycle_at = now + minimum_interval
            return ProviderMonitorCycleClaim(token=token, observed_at=now)

    async def finish_provider_monitor_cycle(self, token: UUID) -> bool:
        async with self._lock:
            if self._lease_token != token:
                return False
            self._lease_token = None
            self._lease_expires_at = None
            return True

    async def commit_provider_monitor_cycle(
        self,
        token: UUID,
        samples: list[ProviderHealthSample],
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        retention_before: datetime,
        now: datetime,
        lease_valid_at: datetime,
    ) -> tuple[bool, list[ProviderAlertEvent]]:
        async with self._lock:
            if (
                self._lease_token != token
                or self._lease_expires_at is None
                or self._lease_expires_at <= lease_valid_at
            ):
                return False, []
            self._samples.extend(samples)
            self._samples = [
                sample
                for sample in self._samples
                if sample.checked_at >= retention_before
            ]
            transitions = self._apply_conditions_locked(
                conditions,
                breach_cycles=breach_cycles,
                recovery_cycles=recovery_cycles,
                now=now,
            )
            self._lease_token = None
            self._lease_expires_at = None
            self._last_successful_cycle_at = now
            return True, transitions

    async def record_provider_health_samples(
        self, samples: list[ProviderHealthSample]
    ) -> None:
        async with self._lock:
            self._samples.extend(samples)

    async def prune_provider_health_samples(self, *, before: datetime) -> int:
        async with self._lock:
            previous = len(self._samples)
            self._samples = [
                sample for sample in self._samples if sample.checked_at >= before
            ]
            return previous - len(self._samples)

    async def provider_outcome_summaries(
        self, *, since: datetime
    ) -> list[ProviderOutcomeSummary]:
        del since
        async with self._lock:
            return list(self._outcome_summaries)

    async def set_outcome_summaries(
        self, summaries: list[ProviderOutcomeSummary]
    ) -> None:
        async with self._lock:
            self._outcome_summaries = list(summaries)

    async def apply_provider_alert_conditions(
        self,
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        now: datetime,
    ) -> list[ProviderAlertEvent]:
        async with self._lock:
            return self._apply_conditions_locked(
                conditions,
                breach_cycles=breach_cycles,
                recovery_cycles=recovery_cycles,
                now=now,
            )

    def _apply_conditions_locked(
        self,
        conditions: list[ProviderAlertCondition],
        *,
        breach_cycles: int,
        recovery_cycles: int,
        now: datetime,
    ) -> list[ProviderAlertEvent]:
        transitions: list[ProviderAlertEvent] = []
        for condition in sorted(
            conditions, key=lambda item: item.fingerprint
        ):
            state = self._states.setdefault(
                condition.fingerprint, _MemoryAlertState(condition)
            )
            state.condition = condition
            if condition.breached is None:
                continue
            if condition.breached:
                state.recovery_count = 0
                if state.active:
                    continue
                state.breach_count += 1
                if state.breach_count < breach_cycles:
                    continue
                state.active = True
                state.breach_count = 0
                event = _new_alert_event(
                    condition,
                    ProviderAlertEventType.TRIGGERED,
                    now=now,
                )
                state.opened_event = event
                self._events.append(event)
                self._deliveries[event.id] = _MemoryDelivery(available_at=now)
                transitions.append(event)
                continue
            state.breach_count = 0
            if not state.active:
                continue
            state.recovery_count += 1
            if state.recovery_count < recovery_cycles:
                continue
            state.active = False
            state.recovery_count = 0
            event = _new_alert_event(
                condition,
                ProviderAlertEventType.RECOVERED,
                now=now,
            )
            state.opened_event = None
            self._events.append(event)
            self._deliveries[event.id] = _MemoryDelivery(available_at=now)
            transitions.append(event)
        return transitions

    async def claim_provider_alert_deliveries(
        self,
        *,
        batch_size: int,
        exclude_ids: set[UUID] | None = None,
        lease: timedelta = timedelta(seconds=60),
        max_attempts: int | None = None,
    ) -> list[ProviderAlertClaim]:
        now = datetime.now(timezone.utc)
        excluded = exclude_ids or set()
        claims: list[ProviderAlertClaim] = []
        async with self._lock:
            for event in self._events:
                if len(claims) >= batch_size or event.id in excluded:
                    continue
                delivery = self._deliveries[event.id]
                reclaimable = (
                    delivery.status == ProviderAlertDeliveryStatus.DELIVERING
                    and delivery.claim_expires_at is not None
                    and delivery.claim_expires_at <= now
                )
                if (
                    delivery.status == ProviderAlertDeliveryStatus.PENDING
                    and max_attempts is not None
                    and delivery.attempts >= max_attempts
                ):
                    delivery.status = ProviderAlertDeliveryStatus.DEAD_LETTER
                    delivery.last_error = "Alert delivery attempts exhausted"
                    delivery.claim_token = None
                    delivery.claim_expires_at = None
                    continue
                if not (
                    delivery.status == ProviderAlertDeliveryStatus.PENDING
                    or reclaimable
                ) or delivery.available_at > now:
                    continue
                token = uuid4()
                delivery.status = ProviderAlertDeliveryStatus.DELIVERING
                delivery.claim_token = token
                delivery.claim_expires_at = now + lease
                claims.append(
                    ProviderAlertClaim(
                        event=replace(
                            event,
                            delivery_status=delivery.status,
                            attempts=delivery.attempts,
                        ),
                        token=token,
                    )
                )
        return claims

    async def mark_provider_alert_delivered(
        self,
        event_id: UUID,
        *,
        token: UUID,
        response_status: int,
    ) -> bool:
        async with self._lock:
            delivery = self._deliveries.get(event_id)
            if delivery is None or delivery.claim_token != token:
                return False
            delivery.status = ProviderAlertDeliveryStatus.DELIVERED
            delivery.response_status = response_status
            delivery.claim_token = None
            delivery.claim_expires_at = None
            return True

    async def release_provider_alert_delivery(
        self,
        event_id: UUID,
        *,
        token: UUID,
        error: str,
        retry_delay: timedelta,
        dead_letter: bool,
        response_status: int | None = None,
    ) -> bool:
        async with self._lock:
            delivery = self._deliveries.get(event_id)
            if delivery is None or delivery.claim_token != token:
                return False
            delivery.status = (
                ProviderAlertDeliveryStatus.DEAD_LETTER
                if dead_letter
                else ProviderAlertDeliveryStatus.PENDING
            )
            delivery.attempts += 1
            delivery.available_at = datetime.now(timezone.utc) + retry_delay
            delivery.response_status = response_status
            delivery.last_error = error[:256]
            delivery.claim_token = None
            delivery.claim_expires_at = None
            return True

    async def list_provider_alert_events(
        self, *, limit: int = 100
    ) -> list[ProviderAlertEvent]:
        async with self._lock:
            return list(reversed(self._events[-limit:]))

    async def provider_monitoring_status(self) -> ProviderMonitoringStatus:
        async with self._lock:
            pending = [
                event.occurred_at
                for event in self._events
                if self._deliveries[event.id].status
                in {
                    ProviderAlertDeliveryStatus.PENDING,
                    ProviderAlertDeliveryStatus.DELIVERING,
                }
            ]
            dead_letters = [
                event.occurred_at
                for event in self._events
                if self._deliveries[event.id].status
                == ProviderAlertDeliveryStatus.DEAD_LETTER
            ]
            return ProviderMonitoringStatus(
                observed_at=datetime.now(timezone.utc),
                last_successful_cycle_at=self._last_successful_cycle_at,
                active_alert_count=sum(
                    1 for state in self._states.values() if state.active
                ),
                pending_delivery_count=len(pending),
                oldest_pending_at=min(pending, default=None),
                dead_letter_count=len(dead_letters),
                oldest_dead_letter_at=min(dead_letters, default=None),
            )

    async def samples(self) -> list[ProviderHealthSample]:
        async with self._lock:
            return list(self._samples)

    async def alert_events(self) -> list[ProviderAlertEvent]:
        async with self._lock:
            return list(self._events)

    async def active_alerts(self) -> list[ProviderAlertEvent]:
        async with self._lock:
            return [
                state.opened_event
                for state in self._states.values()
                if state.active and state.opened_event is not None
            ]


def _new_alert_event(
    condition: ProviderAlertCondition,
    event_type: ProviderAlertEventType,
    *,
    now: datetime,
) -> ProviderAlertEvent:
    return ProviderAlertEvent(
        id=uuid4(),
        fingerprint=condition.fingerprint,
        kind=condition.kind,
        event_type=event_type,
        provider_name=condition.provider_name,
        occurred_at=now,
        details=dict(condition.details),
    )


class ProviderMonitor:
    def __init__(
        self,
        router: HealthProbeRouter,
        repository: ProviderMonitoringRepository,
        *,
        policy: ProviderMonitorPolicy | None = None,
        worker_id: str = "provider-monitor",
        retired_provider_names: frozenset[str] = frozenset(),
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must be a non-empty short identifier")
        self.router = router
        self.repository = repository
        self.policy = policy or ProviderMonitorPolicy()
        self.worker_id = worker_id
        self.retired_provider_names = retired_provider_names

    async def run_cycle(self, *, now: datetime | None = None) -> bool:
        explicit_time = now is not None
        requested_at = now or datetime.now(timezone.utc)
        claim = await self.repository.claim_provider_monitor_cycle(
            worker_id=self.worker_id,
            now=requested_at,
            lease=self.policy.cycle_lease,
            minimum_interval=self.policy.cycle_interval,
        )
        if claim is None:
            return False
        token = claim.token
        current = claim.observed_at
        committed = False
        try:
            samples = await self.router.probe_health(at=current)
            summaries = await self.repository.provider_outcome_summaries(
                since=current - self.policy.outcome_window
            )
            committed, transitions = (
                await self.repository.commit_provider_monitor_cycle(
                token,
                samples,
                self._conditions(samples, summaries),
                breach_cycles=self.policy.breach_cycles,
                recovery_cycles=self.policy.recovery_cycles,
                retention_before=current - self.policy.sample_retention,
                now=current,
                lease_valid_at=(
                    current
                    if explicit_time
                    else datetime.now(timezone.utc)
                ),
            )
            )
            if not committed:
                return False
            for event in transitions:
                # Only stable, secret-free identifiers are logged. The details
                # payload is intentionally omitted from logs.
                log = logger.error if event.severity == "critical" else logger.warning
                log(
                    "Provider alert %s: kind=%s provider=%s fingerprint=%s",
                    event.event_type.value,
                    event.kind.value,
                    event.provider_name,
                    event.fingerprint,
                )
            return True
        finally:
            if not committed:
                await self.repository.finish_provider_monitor_cycle(token)

    def _conditions(
        self,
        samples: list[ProviderHealthSample],
        summaries: list[ProviderOutcomeSummary],
    ) -> list[ProviderAlertCondition]:
        samples_by_provider: dict[str, list[ProviderHealthSample]] = defaultdict(list)
        for sample in samples:
            samples_by_provider[sample.provider_name].append(sample)
        outcomes_by_provider: dict[str, list[ProviderOutcomeSummary]] = defaultdict(list)
        for summary in summaries:
            outcomes_by_provider[summary.provider_name].append(summary)
        # Current route probes define whether a provider is still configured.
        # Terminal outcomes can legitimately remain in the reporting window
        # for several minutes after an explicit retirement.
        overlap = set(samples_by_provider) & self.retired_provider_names
        if overlap:
            raise RuntimeError(
                "A provider cannot be configured as both active and retired"
            )
        providers = sorted(
            (set(samples_by_provider) | set(outcomes_by_provider))
            - self.retired_provider_names
        )
        conditions: list[ProviderAlertCondition] = []
        for provider_name in providers:
            provider_samples = samples_by_provider.get(provider_name, [])
            provider_outcomes = outcomes_by_provider.get(provider_name, [])
            succeeded = sum(item.succeeded for item in provider_outcomes)
            failed = sum(item.failed for item in provider_outcomes)
            total = succeeded + failed
            success_rate = succeeded / total if total else None
            conditions.append(
                ProviderAlertCondition(
                    fingerprint=(
                        f"{ProviderAlertKind.SUCCESS_RATE_DROP.value}:"
                        f"{provider_name}"
                    ),
                    kind=ProviderAlertKind.SUCCESS_RATE_DROP,
                    provider_name=provider_name,
                    breached=(
                        success_rate < self.policy.min_success_rate
                        if total >= self.policy.min_outcomes
                        and success_rate is not None
                        else None
                    ),
                    details={
                        "window_seconds": int(
                            self.policy.outcome_window.total_seconds()
                        ),
                        "succeeded": succeeded,
                        "failed": failed,
                        "total": total,
                        "success_rate": success_rate,
                        "threshold": self.policy.min_success_rate,
                    },
                )
            )

            route_count = len(provider_samples)
            unhealthy_count = sum(
                not sample.healthy for sample in provider_samples
            )
            unhealthy_ratio = (
                unhealthy_count / route_count if route_count else None
            )
            conditions.append(
                ProviderAlertCondition(
                    fingerprint=(
                        f"{ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE.value}:"
                        f"{provider_name}"
                    ),
                    kind=ProviderAlertKind.WIDESPREAD_CHANNEL_FAILURE,
                    provider_name=provider_name,
                    breached=(
                        unhealthy_ratio >= self.policy.widespread_failure_ratio
                        if route_count
                        >= self.policy.widespread_failure_min_routes
                        and unhealthy_ratio is not None
                        else None
                    ),
                    details={
                        "routes": route_count,
                        "unhealthy_routes": unhealthy_count,
                        "unhealthy_ratio": unhealthy_ratio,
                        "threshold": self.policy.widespread_failure_ratio,
                    },
                )
            )

            invalid_accounts = [
                sample
                for sample in provider_samples
                if (
                    not sample.admission_enabled
                    and bool(sample.error_code)
                    and sample.admission_disabled_reason
                    in {None, "provider_error"}
                )
            ]
            error_counts: dict[str, int] = defaultdict(int)
            for sample in invalid_accounts:
                assert sample.error_code is not None
                error_counts[sample.error_code] += 1
            conditions.append(
                ProviderAlertCondition(
                    fingerprint=(
                        f"{ProviderAlertKind.BATCH_ACCOUNT_INVALIDATION.value}:"
                        f"{provider_name}"
                    ),
                    kind=ProviderAlertKind.BATCH_ACCOUNT_INVALIDATION,
                    provider_name=provider_name,
                    breached=(
                        len(invalid_accounts)
                        >= self.policy.batch_disabled_threshold
                    ),
                    details={
                        "disabled_with_error": len(invalid_accounts),
                        "threshold": self.policy.batch_disabled_threshold,
                        "error_counts": dict(sorted(error_counts.items())),
                    },
                )
            )
        for provider_name in sorted(self.retired_provider_names):
            conditions.extend(self._retired_conditions(provider_name))
        return conditions

    def _retired_conditions(
        self, provider_name: str
    ) -> list[ProviderAlertCondition]:
        """Explicitly resolve alerts for a deliberately removed provider."""

        return [
            ProviderAlertCondition(
                fingerprint=f"{kind.value}:{provider_name}",
                kind=kind,
                provider_name=provider_name,
                breached=False,
                details={"reason": "provider_retired"},
            )
            for kind in ProviderAlertKind
        ]


def serialize_provider_alert(event: ProviderAlertEvent) -> bytes:
    payload = {
        "id": str(event.id),
        "object": "relay.provider_alert",
        "version": "1",
        "type": event.kind.value,
        "severity": event.severity,
        "status": event.event_type.value,
        "occurred_at": event.occurred_at.isoformat(),
        "fingerprint": event.fingerprint,
        "provider": {"name": event.provider_name},
        "observed": event.details,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class ProviderAlertDispatcher:
    def __init__(
        self,
        repository: ProviderMonitoringRepository,
        *,
        webhook_url: str,
        signing_secret: str,
        production: bool,
        transport: CallbackTransport,
        max_attempts: int = 8,
        claim_lease_seconds: float = 60,
        base_delay_seconds: float = 5,
        max_delay_seconds: float = 900,
        now=None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if claim_lease_seconds <= 0:
            raise ValueError("claim_lease_seconds must be positive")
        if base_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("alert retry delays must be positive")
        self.repository = repository
        self.webhook_url = webhook_url
        self.signing_secret = signing_secret
        self.production = production
        self.transport = transport
        self.max_attempts = max_attempts
        self.claim_lease = timedelta(seconds=claim_lease_seconds)
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _retry_delay(self, attempts: int) -> timedelta:
        return timedelta(
            seconds=min(
                self.base_delay_seconds * (2 ** max(attempts - 1, 0)),
                self.max_delay_seconds,
            )
        )

    async def dispatch_once(self, *, batch_size: int = 50) -> int:
        delivered = 0
        attempted_ids: set[UUID] = set()
        for _ in range(max(batch_size, 0)):
            claims = await self.repository.claim_provider_alert_deliveries(
                batch_size=1,
                exclude_ids=attempted_ids,
                lease=self.claim_lease,
                max_attempts=self.max_attempts,
            )
            if not claims:
                break
            claim = claims[0]
            event = claim.event
            attempted_ids.add(event.id)
            response_status: int | None = None
            try:
                body = serialize_provider_alert(event)
                timestamp = str(int(self.now().timestamp()))
                response_status = await self.transport.post(
                    self.webhook_url,
                    body,
                    {
                        "Content-Type": "application/json",
                        "User-Agent": "ai-video-relay-alert/1.0",
                        "X-Relay-Alert-ID": str(event.id),
                        "X-Relay-Alert-Timestamp": timestamp,
                        "X-Relay-Alert-Signature": sign_callback(
                            self.signing_secret,
                            timestamp=timestamp,
                            event_id=event.id,
                            body=body,
                        ),
                    },
                    production=self.production,
                )
                if 200 <= response_status < 300:
                    if await self.repository.mark_provider_alert_delivered(
                        event.id,
                        token=claim.token,
                        response_status=response_status,
                    ):
                        delivered += 1
                    continue
                error = f"HTTP {response_status}"
            except CallbackConfigurationError:
                error = "Alert configuration rejected delivery"
            except CallbackTransportError:
                error = "Alert transport failed"
            except Exception:
                error = "Alert delivery failed"
            await self.repository.release_provider_alert_delivery(
                event.id,
                token=claim.token,
                error=error,
                retry_delay=self._retry_delay(event.attempts + 1),
                dead_letter=event.attempts + 1 >= self.max_attempts,
                response_status=response_status,
            )
        return delivered
