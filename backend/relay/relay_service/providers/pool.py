from __future__ import annotations

from abc import ABC, abstractmethod
from asyncio import Lock
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from .base import ProviderRouteManifest


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccountAcquireReason(StrEnum):
    ACQUIRED = "acquired"
    BUSY = "busy"
    RATE_LIMITED = "rate_limited"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"
    ASSIGNMENT_CONFLICT = "assignment_conflict"
    JOB_NOT_SUBMITTING = "job_not_submitting"


@dataclass(frozen=True)
class AccountAcquireResult:
    reason: AccountAcquireReason
    retry_after_seconds: float | None = None

    @property
    def acquired(self) -> bool:
        return self.reason == AccountAcquireReason.ACQUIRED


@dataclass(frozen=True)
class ProviderAccountSnapshot:
    """Secret-free shared state used only for routing and operations."""

    route_id: str
    admission_enabled: bool
    active_jobs: int
    consecutive_failures: int
    cooldown_until: datetime | None
    rate_window_started_at: datetime | None
    rate_window_count: int
    successful_submissions: int
    last_acquired_at: datetime | None
    last_error_code: str | None
    admission_disabled_reason: str | None = None

    def accepts_new_jobs(self, *, at: datetime | None = None) -> bool:
        current = at or _now()
        return self.admission_enabled and not (
            self.cooldown_until is not None
            and self.cooldown_until > current
        )


class ProviderAccountPool(ABC):
    """Cross-worker admission state for concrete provider accounts.

    A production implementation must make every method atomic across Relay
    processes. Acquiring an account is tied to one generation job. A successful
    provider POST retains that assignment until the upstream job reaches a
    terminal state; a proven non-creation may release it for failover.
    """

    @abstractmethod
    async def register_routes(
        self, manifests: list[ProviderRouteManifest]
    ) -> None: ...

    @abstractmethod
    async def snapshots(
        self, route_ids: list[str]
    ) -> dict[str, ProviderAccountSnapshot]: ...

    @abstractmethod
    async def acquire(
        self,
        job_id: UUID,
        manifest: ProviderRouteManifest,
        *,
        owner_token: UUID | None = None,
    ) -> AccountAcquireResult: ...

    @abstractmethod
    async def release_assignment(
        self,
        job_id: UUID,
        route_id: str,
        *,
        owner_token: UUID | None = None,
    ) -> bool:
        """Release only after proving the provider did not create a task."""

    @abstractmethod
    async def record_failure(
        self,
        route_id: str,
        *,
        error_code: str,
        failure_threshold: int,
        cooldown: timedelta,
        disable_account: bool,
        job_id: UUID | None = None,
        release_assignment: bool = False,
        owner_token: UUID | None = None,
    ) -> bool:
        """Update route state and optionally release a proven-safe attempt."""

    @abstractmethod
    async def record_success(
        self, route_id: str, *, submission: bool = False
    ) -> None: ...

    @abstractmethod
    async def record_channel_failure(
        self,
        provider_name: str,
        *,
        error_code: str,
        cooldown: timedelta,
    ) -> int:
        """Cool every account of one provider after a proven channel outage."""

    @abstractmethod
    async def complete_job(self, job_id: UUID, route_id: str) -> None:
        """Release an active slot after an upstream terminal event commits."""

    @abstractmethod
    async def set_admission_enabled(
        self, route_id: str, *, enabled: bool
    ) -> bool:
        """Stop/resume new submissions without breaking sticky polling."""


@dataclass
class _MemoryState:
    manifest: ProviderRouteManifest
    admission_enabled: bool = True
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
    rate_window_started_at: datetime | None = None
    rate_window_count: int = 0
    successful_submissions: int = 0
    last_acquired_at: datetime | None = None
    last_error_code: str | None = None
    admission_disabled_reason: str | None = None


class InMemoryProviderAccountPool(ProviderAccountPool):
    """Contract implementation for tests and single-process development."""

    def __init__(self) -> None:
        self._states: dict[str, _MemoryState] = {}
        self._assignments: dict[UUID, str] = {}
        self._lock = Lock()

    async def register_routes(
        self, manifests: list[ProviderRouteManifest]
    ) -> None:
        async with self._lock:
            for manifest in manifests:
                existing = self._states.get(manifest.route_id)
                if existing is None:
                    self._states[manifest.route_id] = _MemoryState(manifest)
                else:
                    existing.manifest = manifest

    async def snapshots(
        self, route_ids: list[str]
    ) -> dict[str, ProviderAccountSnapshot]:
        async with self._lock:
            active: dict[str, int] = {route_id: 0 for route_id in route_ids}
            for route_id in self._assignments.values():
                if route_id in active:
                    active[route_id] += 1
            return {
                route_id: self._snapshot(state, active[route_id])
                for route_id in route_ids
                if (state := self._states.get(route_id)) is not None
            }

    async def acquire(
        self,
        job_id: UUID,
        manifest: ProviderRouteManifest,
        *,
        owner_token: UUID | None = None,
    ) -> AccountAcquireResult:
        now = _now()
        async with self._lock:
            state = self._states.get(manifest.route_id)
            if state is None:
                state = _MemoryState(manifest)
                self._states[manifest.route_id] = state
            else:
                state.manifest = manifest

            assigned = self._assignments.get(job_id)
            if assigned == manifest.route_id:
                return AccountAcquireResult(AccountAcquireReason.ACQUIRED)
            if assigned is not None:
                return AccountAcquireResult(
                    AccountAcquireReason.ASSIGNMENT_CONFLICT
                )
            if not state.admission_enabled:
                return AccountAcquireResult(AccountAcquireReason.DISABLED)
            if state.cooldown_until is not None:
                if state.cooldown_until > now:
                    return AccountAcquireResult(
                        AccountAcquireReason.COOLDOWN,
                        (state.cooldown_until - now).total_seconds(),
                    )
                state.cooldown_until = None
                state.consecutive_failures = 0

            active = sum(
                1
                for route_id in self._assignments.values()
                if route_id == manifest.route_id
            )
            if (
                manifest.max_concurrency is not None
                and active >= manifest.max_concurrency
            ):
                return AccountAcquireResult(AccountAcquireReason.BUSY)

            rate_result = self._consume_rate_limit(state, now)
            if rate_result is not None:
                return rate_result
            self._assignments[job_id] = manifest.route_id
            state.last_acquired_at = now
            return AccountAcquireResult(AccountAcquireReason.ACQUIRED)

    async def release_assignment(
        self,
        job_id: UUID,
        route_id: str,
        *,
        owner_token: UUID | None = None,
    ) -> bool:
        async with self._lock:
            assigned = self._assignments.get(job_id)
            if assigned is None:
                return True
            if assigned != route_id:
                return False
            del self._assignments[job_id]
            return True

    async def record_failure(
        self,
        route_id: str,
        *,
        error_code: str,
        failure_threshold: int,
        cooldown: timedelta,
        disable_account: bool,
        job_id: UUID | None = None,
        release_assignment: bool = False,
        owner_token: UUID | None = None,
    ) -> bool:
        now = _now()
        async with self._lock:
            state = self._states.get(route_id)
            if state is None:
                return False
            if release_assignment and job_id is not None:
                assigned = self._assignments.get(job_id)
                if assigned not in {None, route_id}:
                    return False
                self._assignments.pop(job_id, None)
            state.consecutive_failures += 1
            state.last_error_code = error_code[:128]
            if disable_account:
                state.admission_enabled = False
                state.admission_disabled_reason = "provider_error"
                state.cooldown_until = None
            elif state.consecutive_failures >= failure_threshold:
                proposed_cooldown = now + cooldown
                if (
                    state.cooldown_until is None
                    or state.cooldown_until < proposed_cooldown
                ):
                    state.cooldown_until = proposed_cooldown
            return True

    async def record_success(
        self, route_id: str, *, submission: bool = False
    ) -> None:
        async with self._lock:
            state = self._states.get(route_id)
            if state is None:
                return
            state.consecutive_failures = 0
            state.cooldown_until = None
            state.last_error_code = None
            if submission:
                state.successful_submissions += 1

    async def record_channel_failure(
        self,
        provider_name: str,
        *,
        error_code: str,
        cooldown: timedelta,
    ) -> int:
        now = _now()
        affected = 0
        async with self._lock:
            for state in self._states.values():
                if (
                    state.manifest.provider_name != provider_name
                    or not state.admission_enabled
                ):
                    continue
                affected += 1
                state.consecutive_failures += 1
                state.last_error_code = error_code[:128]
                proposed = now + cooldown
                if (
                    state.cooldown_until is None
                    or state.cooldown_until < proposed
                ):
                    state.cooldown_until = proposed
        return affected

    async def complete_job(self, job_id: UUID, route_id: str) -> None:
        async with self._lock:
            if self._assignments.get(job_id) == route_id:
                del self._assignments[job_id]

    async def set_admission_enabled(
        self, route_id: str, *, enabled: bool
    ) -> bool:
        async with self._lock:
            state = self._states.get(route_id)
            if state is None:
                return False
            state.admission_enabled = enabled
            state.admission_disabled_reason = None if enabled else "manual"
            if enabled:
                state.consecutive_failures = 0
                state.cooldown_until = None
                state.last_error_code = None
            return True

    @staticmethod
    def _snapshot(
        state: _MemoryState, active_jobs: int
    ) -> ProviderAccountSnapshot:
        return ProviderAccountSnapshot(
            route_id=state.manifest.route_id,
            admission_enabled=state.admission_enabled,
            active_jobs=active_jobs,
            consecutive_failures=state.consecutive_failures,
            cooldown_until=state.cooldown_until,
            rate_window_started_at=state.rate_window_started_at,
            rate_window_count=state.rate_window_count,
            successful_submissions=state.successful_submissions,
            last_acquired_at=state.last_acquired_at,
            last_error_code=state.last_error_code,
            admission_disabled_reason=state.admission_disabled_reason,
        )

    @staticmethod
    def _consume_rate_limit(
        state: _MemoryState, now: datetime
    ) -> AccountAcquireResult | None:
        limit = state.manifest.requests_per_minute
        if limit is None:
            return None
        window = timedelta(minutes=1)
        if (
            state.rate_window_started_at is None
            or state.rate_window_started_at + window <= now
        ):
            state.rate_window_started_at = now
            state.rate_window_count = 0
        if state.rate_window_count >= limit:
            assert state.rate_window_started_at is not None
            retry_after = (
                state.rate_window_started_at + window - now
            ).total_seconds()
            return AccountAcquireResult(
                AccountAcquireReason.RATE_LIMITED,
                max(0.0, retry_after),
            )
        state.rate_window_count += 1
        return None
