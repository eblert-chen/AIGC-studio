from __future__ import annotations

import asyncio
from time import perf_counter
from collections.abc import Iterable
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from ..capabilities import build_model_catalog
from ..models import (
    GenerationJob,
    ModelCapability,
    ModelListResponse,
    ModeCapabilityResponse,
    ProviderWebhookEvent,
)
from ..provider_monitoring import ProviderHealthSample
from .base import (
    ProviderAdapter,
    ProviderContractError,
    ProviderError,
    ProviderFailureScope,
    ProviderRouteManifest,
    ProviderSubmission,
)
from .pool import (
    AccountAcquireReason,
    InMemoryProviderAccountPool,
    ProviderAccountPool,
    ProviderAccountSnapshot,
)


@dataclass(frozen=True)
class ProviderRouteProfile:
    """Validated adapter metadata and its immutable model declarations."""

    manifest: ProviderRouteManifest
    capabilities: tuple[ModelCapability, ...]


class ProviderRouter:
    """Capability routing plus a small circuit-breaker/failover policy."""

    def __init__(
        self,
        providers: list[ProviderAdapter],
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30,
        healthcheck_timeout_seconds: float = 5.0,
        account_pool: ProviderAccountPool | None = None,
    ) -> None:
        self.providers: dict[str, ProviderAdapter] = {}
        self._manifests: dict[str, ProviderRouteManifest] = {}
        self._provider_accounts: dict[
            str, dict[str, ProviderAdapter]
        ] = defaultdict(dict)
        for provider in providers:
            try:
                manifest = provider.manifest
            except (TypeError, ValueError) as exc:
                raise ValueError("Provider route metadata is invalid") from exc
            route_id = manifest.route_id
            if route_id in self.providers:
                raise ValueError(
                    f"Provider route identities must be unique: {route_id}"
                )
            self.providers[route_id] = provider
            self._manifests[route_id] = manifest
            self._provider_accounts[manifest.provider_name][
                manifest.account_id
            ] = provider
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        if healthcheck_timeout_seconds <= 0:
            raise ValueError("healthcheck_timeout_seconds must be positive")
        self.failure_threshold = failure_threshold
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.healthcheck_timeout_seconds = healthcheck_timeout_seconds
        self.account_pool = account_pool or InMemoryProviderAccountPool()
        self._pool_registered = False
        self._capability_cache: dict[
            str, tuple[ModelCapability, ...]
        ] = {}

    @property
    def provider_names(self) -> frozenset[str]:
        return frozenset(self._provider_accounts)

    def provider(self, route_or_provider_name: str) -> ProviderAdapter | None:
        direct = self.providers.get(route_or_provider_name)
        if direct is not None:
            return direct
        accounts = self._provider_accounts.get(route_or_provider_name, {})
        if len(accounts) == 1:
            return next(iter(accounts.values()))
        return None

    async def capability_profiles(self) -> list[ModelCapability]:
        """Return configured route profiles without transient health filtering."""

        profiles: list[ModelCapability] = []
        for route_id in sorted(self.providers):
            provider = self.providers[route_id]
            for capability in await self._capabilities_for(provider):
                profiles.append(
                    capability.model_copy(
                        deep=True,
                        update={"available_providers": [provider.name]},
                    )
                )
        return profiles

    async def validate_configuration(self) -> None:
        """Fail startup when any plugin has an invalid model declaration."""

        for route_id in sorted(self.providers):
            await self._capabilities_for(self.providers[route_id])
        await self._ensure_account_pool_registered()

    async def route_profiles(self) -> list[ProviderRouteProfile]:
        """Return secret-free, validated route declarations for operations."""

        await self._ensure_account_pool_registered()
        profiles: list[ProviderRouteProfile] = []
        for route_id in sorted(self.providers):
            capabilities = await self._capabilities_for(
                self.providers[route_id]
            )
            profiles.append(
                ProviderRouteProfile(
                    manifest=self._manifests[route_id],
                    capabilities=tuple(
                        item.model_copy(deep=True) for item in capabilities
                    ),
                )
            )
        return profiles

    async def model_catalog(self) -> ModelListResponse:
        return build_model_catalog(await self.capability_profiles())

    async def capabilities(self) -> list[ModelCapability]:
        """Legacy flat view kept for existing callers.

        Derive it from the same failover-safe public catalog and emit one row
        per model/mode. A single flat row cannot represent different T2V and
        I2V limits without over-advertising one of them. New callers should use
        ``model_catalog``.
        """

        flattened: list[ModelCapability] = []
        catalog = await self.model_catalog()
        for resource in catalog.data:
            for mode, capability in sorted(
                resource.capabilities.modes.items(),
                key=lambda item: item[0].value,
            ):
                flattened.append(
                    ModelCapability(
                        model=resource.id,
                        modes=[mode],
                        input_media_types=capability.input_media_types,
                        supports_face=capability.supports_face,
                        limits=capability.limits.model_copy(deep=True),
                        # This value is removed by ModelCapabilityResponse and
                        # never leaves Relay; it only satisfies the old model.
                        available_providers=["relay"],
                    )
                )
        return flattened

    async def submit(
        self,
        job: GenerationJob,
        *,
        owner_token: UUID | None = None,
    ) -> tuple[str, ProviderSubmission]:
        await self._ensure_account_pool_registered()
        expected_revision = job.expected_capability_revision
        catalog = await self.model_catalog()
        resource = next(
            (item for item in catalog.data if item.id == job.model), None
        )
        if resource is None:
            raise ProviderError(
                "MODEL_CAPABILITY_UNAVAILABLE",
                "Model capability is no longer available",
                retryable=False,
                account_unavailable=False,
            )
        if resource.capability_revision != expected_revision:
            raise ProviderError(
                "CAPABILITY_REVISION_MISMATCH",
                "Model capabilities changed; refresh the catalog and resubmit",
                retryable=False,
                account_unavailable=False,
            )
        if resource is not None:
            public_capability = resource.capabilities.modes.get(job.mode)
            if public_capability is None:
                raise ProviderError(
                    "MODE_NOT_SUPPORTED_BY_MODEL",
                    f"Model {job.model} does not support mode {job.mode.value}",
                    retryable=False,
                    account_unavailable=False,
                )
            public_error = self._validate_job(job, public_capability)
            if public_error is not None:
                raise ProviderError(
                    "REQUEST_NOT_SUPPORTED_BY_MODEL",
                    public_error,
                    retryable=False,
                    account_unavailable=False,
                )
        compatible: list[ProviderAdapter] = []
        model_seen = False
        mode_seen = False
        validation_errors: list[str] = []
        configured_providers = list(self.providers.values())
        for provider in configured_providers:
            capabilities = await self._capabilities_for(provider)
            for capability in capabilities:
                if capability.model != job.model:
                    continue
                model_seen = True
                if job.mode not in capability.modes:
                    continue
                mode_seen = True
                error = self._validate_job(job, capability)
                if error is None:
                    compatible.append(provider)
                    break
                validation_errors.append(error)

        if not compatible:
            if model_seen and mode_seen and validation_errors:
                raise ProviderError(
                    "REQUEST_NOT_SUPPORTED_BY_MODEL",
                    validation_errors[0],
                    retryable=False,
                )
            if model_seen and not mode_seen:
                raise ProviderError(
                    "MODE_NOT_SUPPORTED_BY_MODEL",
                    f"Model {job.model} does not support mode {job.mode.value}",
                    retryable=False,
                )
            raise ProviderError(
                "NO_PROVIDER_AVAILABLE",
                f"No healthy provider supports model {job.model}",
                retryable=True,
            )

        account_states = await self.account_pool.snapshots(
            [provider.route_id for provider in compatible]
        )
        if account_states and not any(
            snapshot.admission_enabled
            for snapshot in account_states.values()
        ):
            raise ProviderError(
                "PROVIDER_ACCOUNT_POOL_DISABLED",
                "All matching provider accounts are disabled",
                retryable=False,
                account_unavailable=False,
            )
        admission_candidates = [
            provider
            for provider in compatible
            if (
                (snapshot := account_states.get(provider.route_id))
                is not None
                and snapshot.accepts_new_jobs()
            )
        ]
        if not admission_candidates:
            raise ProviderError(
                "PROVIDER_ACCOUNT_POOL_BUSY",
                "All matching provider accounts are cooling down",
                retryable=True,
                account_unavailable=False,
                retry_after_seconds=self._cooldown_retry_after(
                    account_states.values()
                ),
            )
        eligibility = await asyncio.gather(
            *(
                self._eligible(
                    provider, account_states.get(provider.route_id)
                )
                for provider in admission_candidates
            )
        )
        candidates = [
            provider
            for provider, eligible in zip(
                admission_candidates, eligibility, strict=True
            )
            if eligible
        ]
        candidates.sort(
            key=lambda provider: self._candidate_order(
                provider, account_states.get(provider.route_id)
            )
        )
        if not candidates:
            raise ProviderError(
                "NO_PROVIDER_AVAILABLE",
                f"No healthy provider supports model {job.model}",
                retryable=True,
            )

        last_error: ProviderError | None = None
        unavailable_provider_names: set[str] = set()
        unavailable_reasons: set[AccountAcquireReason] = set()
        retry_after_seconds: list[float] = []
        for provider in candidates:
            route_id = provider.route_id
            provider_name = self._manifests[route_id].provider_name
            if provider_name in unavailable_provider_names:
                continue
            acquisition = await self.account_pool.acquire(
                job.id,
                self._manifests[route_id],
                owner_token=owner_token,
            )
            if not acquisition.acquired:
                unavailable_reasons.add(acquisition.reason)
                if acquisition.retry_after_seconds is not None:
                    retry_after_seconds.append(
                        acquisition.retry_after_seconds
                    )
                continue
            # Persisted SQL pools set the same route under the submission
            # fence. The local copy must mirror it so a cancelled/ambiguous
            # call is quarantined against the exact account.
            job.provider = route_id
            try:
                submission = await provider.submit(
                    self._provider_job_view(job)
                )
                if not isinstance(submission, ProviderSubmission):
                    raise TypeError("invalid ProviderSubmission")
            except ProviderError as exc:
                last_error = exc
                exc.route_id = route_id
                if exc.submission_outcome_unknown:
                    try:
                        await self._record_failure(route_id, exc)
                    except Exception:
                        # Pool telemetry must never replace the adapter's
                        # authoritative unknown-outcome signal after POST.
                        pass
                    raise
                try:
                    released = await self._release_known_non_creation(
                        job,
                        route_id,
                        exc,
                        owner_token=owner_token,
                    )
                except Exception as pool_error:
                    assignment_error = ProviderError(
                        "PROVIDER_ACCOUNT_ASSIGNMENT_LOST",
                        "Provider account assignment could not be safely changed",
                        retryable=False,
                        submission_outcome_unknown=True,
                        account_unavailable=False,
                    )
                    assignment_error.route_id = route_id
                    raise assignment_error from pool_error
                if not released:
                    assignment_error = ProviderError(
                        "PROVIDER_ACCOUNT_ASSIGNMENT_LOST",
                        "Provider account assignment could not be safely changed",
                        retryable=False,
                        submission_outcome_unknown=True,
                        account_unavailable=False,
                    )
                    assignment_error.route_id = route_id
                    raise assignment_error from exc
                job.provider = None
                if exc.failure_scope == ProviderFailureScope.CHANNEL:
                    unavailable_provider_names.add(provider_name)
                    try:
                        channel_cooldown = self.cooldown
                        if exc.retry_after_seconds is not None:
                            channel_cooldown = max(
                                channel_cooldown,
                                timedelta(seconds=exc.retry_after_seconds),
                            )
                        await self.account_pool.record_channel_failure(
                            provider_name,
                            error_code=exc.code,
                            cooldown=channel_cooldown,
                        )
                    except Exception:
                        # The assignment was already released under its fence.
                        # Shared cooldown telemetry must not block a safe
                        # failover to a different provider.
                        pass
                if not (exc.retryable or exc.account_unavailable):
                    raise
                continue
            except Exception as exc:
                try:
                    await self._record_failure(
                        route_id,
                        ProviderError(
                            "PROVIDER_ADAPTER_ERROR",
                            "Provider adapter returned an invalid submission result",
                            retryable=False,
                            account_unavailable=False,
                        ),
                    )
                except Exception:
                    # The upstream side effect is already unknowable. Preserve
                    # that result even when scheduler bookkeeping is down.
                    pass
                adapter_error = ProviderError(
                    "PROVIDER_ADAPTER_ERROR",
                    "Provider adapter returned an invalid submission result",
                    retryable=False,
                    submission_outcome_unknown=True,
                    account_unavailable=False,
                )
                adapter_error.route_id = route_id
                raise adapter_error from exc
            try:
                await self.account_pool.record_success(
                    route_id, submission=True
                )
            except Exception:
                # The concrete route was durably assigned before POST. A
                # non-critical counter failure must not hide the task id and
                # make the worker treat this as a safe pre-submit retry.
                pass
            return route_id, submission
        if last_error is None and unavailable_reasons:
            if unavailable_reasons == {AccountAcquireReason.RATE_LIMITED}:
                raise ProviderError(
                    "PROVIDER_ACCOUNT_POOL_RATE_LIMITED",
                    "All matching provider accounts reached their rate limit",
                    retryable=True,
                    account_unavailable=False,
                    retry_after_seconds=(
                        min(retry_after_seconds)
                        if retry_after_seconds
                        else None
                    ),
                )
            raise ProviderError(
                "PROVIDER_ACCOUNT_POOL_BUSY",
                "All matching provider accounts are busy or cooling down",
                retryable=True,
                account_unavailable=False,
            )
        assert last_error is not None
        raise last_error

    @staticmethod
    def _cooldown_retry_after(
        snapshots: Iterable[ProviderAccountSnapshot],
    ) -> float | None:
        now = datetime.now(timezone.utc)
        delays = [
            (snapshot.cooldown_until - now).total_seconds()
            for snapshot in snapshots
            if snapshot.cooldown_until is not None
            and snapshot.cooldown_until > now
        ]
        return min(delays) if delays else None

    async def poll(self, job: GenerationJob) -> ProviderWebhookEvent | None:
        await self._ensure_account_pool_registered()
        if not job.provider or not job.provider_task_id:
            raise ProviderError(
                "PROVIDER_TASK_NOT_ASSIGNED",
                "Job does not have an assigned provider task",
                retryable=False,
                fail_job=True,
            )
        provider = self.provider(job.provider)
        if provider is None:
            raise ProviderError(
                "PROVIDER_NOT_FOUND",
                "Assigned provider is not registered",
                retryable=False,
            )
        route_id = provider.route_id
        # Admission disable/cooldown applies only to new submissions. Existing
        # paid tasks stay sticky to their original account and must continue
        # polling until the upstream reports a terminal state.
        try:
            event = await provider.poll(self._provider_job_view(job))
        except ProviderError as exc:
            exc.route_id = route_id
            if exc.account_unavailable:
                await self._record_failure(route_id, exc)
            raise
        except Exception as exc:
            await self._record_failure(
                route_id,
                ProviderError(
                    "PROVIDER_POLL_FAILED",
                    "Provider task status could not be queried",
                    retryable=True,
                    account_unavailable=True,
                ),
            )
            raise ProviderError(
                "PROVIDER_POLL_FAILED",
                "Provider task status could not be queried",
                retryable=True,
                account_unavailable=False,
            ) from exc
        if event is not None and event.provider_task_id != job.provider_task_id:
            await self._record_failure(
                route_id,
                ProviderError(
                    "PROVIDER_TASK_MISMATCH",
                    "Provider returned a different task identifier",
                    retryable=False,
                    account_unavailable=True,
                ),
            )
            raise ProviderError(
                "PROVIDER_TASK_MISMATCH",
                "Provider returned a different task identifier",
                retryable=False,
                account_unavailable=False,
            )
        return event

    async def health(self) -> dict[str, bool]:
        await self._ensure_account_pool_registered()
        configured_providers = list(self.providers.values())
        snapshots = await self.account_pool.snapshots(
            [provider.route_id for provider in configured_providers]
        )
        results = await asyncio.gather(
            *(
                self._eligible(
                    provider, snapshots.get(provider.route_id)
                )
                for provider in configured_providers
            )
        )
        health = {
            provider_name: False
            for provider_name in self._provider_accounts
        }
        for provider, healthy in zip(
            configured_providers, results, strict=True
        ):
            provider_name = self._manifests[
                provider.route_id
            ].provider_name
            health[provider_name] = health[provider_name] or healthy
        return health

    async def probe_health(
        self, *, at: datetime | None = None
    ) -> list[ProviderHealthSample]:
        """Probe every concrete route without provider-level OR aggregation.

        The result contains only stable manifest/account-state metadata and a
        normalized error code. Provider exception text is never returned.
        Admission state is reported separately so a deliberate drain is not
        mistaken for a failed upstream health probe.
        """

        await self._ensure_account_pool_registered()
        checked_at = at or datetime.now(timezone.utc)
        configured = [self.providers[key] for key in sorted(self.providers)]
        snapshots = await self.account_pool.snapshots(
            [provider.route_id for provider in configured]
        )

        async def probe(provider: ProviderAdapter) -> ProviderHealthSample:
            started = perf_counter()
            healthy = False
            health_error: str | None = None
            try:
                result = await asyncio.wait_for(
                    provider.healthcheck(),
                    timeout=self.healthcheck_timeout_seconds,
                )
                if isinstance(result, bool):
                    healthy = result
                    if not result:
                        health_error = "HEALTHCHECK_UNHEALTHY"
                else:
                    health_error = "HEALTHCHECK_INVALID_RESPONSE"
            except TimeoutError:
                health_error = "HEALTHCHECK_TIMEOUT"
            except Exception:
                health_error = "HEALTHCHECK_FAILED"
            latency_ms = max(0, int((perf_counter() - started) * 1000))
            manifest = self._manifests[provider.route_id]
            snapshot = snapshots.get(provider.route_id)
            disabled_reason = (
                snapshot.admission_disabled_reason
                if snapshot is not None
                else "state_unavailable"
            )
            account_error = (
                snapshot.last_error_code
                if snapshot is not None
                and not snapshot.admission_enabled
                and disabled_reason == "provider_error"
                else None
            )
            return ProviderHealthSample(
                route_id=manifest.route_id,
                provider_name=manifest.provider_name,
                account_id=manifest.account_id,
                channel_type=manifest.channel_type,
                healthy=healthy,
                admission_enabled=(
                    snapshot.admission_enabled
                    if snapshot is not None
                    else False
                ),
                checked_at=checked_at,
                error_code=account_error or health_error,
                latency_ms=latency_ms,
                admission_disabled_reason=disabled_reason,
            )

        return list(await asyncio.gather(*(probe(item) for item in configured)))

    async def close(self) -> None:
        await asyncio.gather(
            *(provider.close() for provider in self.providers.values()),
            return_exceptions=True,
        )

    async def _eligible(
        self,
        provider: ProviderAdapter,
        snapshot: ProviderAccountSnapshot | None,
    ) -> bool:
        if snapshot is None or not snapshot.accepts_new_jobs():
            return False
        try:
            healthy = await asyncio.wait_for(
                provider.healthcheck(),
                timeout=self.healthcheck_timeout_seconds,
            )
        except Exception:
            return False
        if not isinstance(healthy, bool):
            return False
        return healthy

    def _candidate_order(
        self,
        provider: ProviderAdapter,
        snapshot: ProviderAccountSnapshot | None,
    ) -> tuple[int, int, int, float, str]:
        route_id = provider.route_id
        return (
            self._manifests[route_id].priority,
            snapshot.active_jobs if snapshot is not None else 0,
            snapshot.successful_submissions if snapshot is not None else 0,
            (
                snapshot.last_acquired_at.timestamp()
                if snapshot is not None
                and snapshot.last_acquired_at is not None
                else -1.0
            ),
            route_id,
        )

    async def _release_known_non_creation(
        self,
        job: GenerationJob,
        route_id: str,
        error: ProviderError,
        *,
        owner_token: UUID | None,
    ) -> bool:
        if error.account_unavailable:
            return await self.account_pool.record_failure(
                route_id,
                error_code=error.code,
                failure_threshold=self.failure_threshold,
                cooldown=self.cooldown,
                disable_account=error.disable_account,
                job_id=job.id,
                release_assignment=True,
                owner_token=owner_token,
            )
        return await self.account_pool.release_assignment(
            job.id, route_id, owner_token=owner_token
        )

    async def _record_failure(
        self, route_id: str, error: ProviderError
    ) -> None:
        await self.account_pool.record_failure(
            route_id,
            error_code=error.code,
            failure_threshold=self.failure_threshold,
            cooldown=self.cooldown,
            disable_account=error.disable_account,
        )

    async def complete_job(self, job: GenerationJob) -> None:
        if job.provider is not None:
            await self.account_pool.complete_job(job.id, job.provider)

    async def set_account_admission(
        self, route_id: str, *, enabled: bool
    ) -> bool:
        await self._ensure_account_pool_registered()
        if route_id not in self.providers:
            return False
        return await self.account_pool.set_admission_enabled(
            route_id, enabled=enabled
        )

    async def _ensure_account_pool_registered(self) -> None:
        if self._pool_registered:
            return
        await self.account_pool.register_routes(
            [self._manifests[key] for key in sorted(self._manifests)]
        )
        self._pool_registered = True

    @staticmethod
    def _provider_job_view(job: GenerationJob) -> GenerationJob:
        """Keep caller routing metadata outside every provider plugin."""

        trace_metadata = {
            key: value
            for key in ("relay_request_id", "platform_request_id")
            if isinstance((value := job.metadata.get(key)), str)
            and value == value.strip()
            and 0 < len(value) <= 128
            and all(ord(character) >= 32 for character in value)
        }
        return job.model_copy(
            deep=True,
            update={
                "source_client_id": None,
                "client_reference_id": None,
                "metadata": trace_metadata,
                "callback_url": None,
            },
        )

    async def _capabilities_for(
        self, provider: ProviderAdapter
    ) -> tuple[ModelCapability, ...]:
        route_id = provider.route_id
        cached = self._capability_cache.get(route_id)
        if cached is not None:
            return cached
        try:
            raw_capabilities = await provider.capabilities()
        except Exception as exc:
            raise ProviderContractError(
                f"Provider route {route_id} could not declare capabilities"
            ) from exc
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise ProviderContractError(
                f"Provider route {route_id} must declare a non-empty "
                "capability list"
            )

        normalized: list[ModelCapability] = []
        seen_model_modes: set[tuple[str, object]] = set()
        for raw_capability in raw_capabilities:
            if not isinstance(raw_capability, ModelCapability):
                raise ProviderContractError(
                    f"Provider route {route_id} returned an invalid "
                    "capability object"
                )
            try:
                capability = ModelCapability.model_validate(
                    raw_capability.model_dump(mode="python")
                )
            except Exception as exc:
                raise ProviderContractError(
                    f"Provider route {route_id} returned an invalid model "
                    "capability"
                ) from exc
            for mode in capability.modes:
                key = (capability.model, mode)
                if key in seen_model_modes:
                    raise ProviderContractError(
                        f"Provider route {route_id} declares model "
                        f"{capability.model} mode {mode.value} more than once"
                    )
                seen_model_modes.add(key)
            normalized.append(
                capability.model_copy(
                    deep=True,
                    update={"available_providers": [provider.name]},
                )
            )
        cached = tuple(normalized)
        self._capability_cache[route_id] = cached
        return cached

    @staticmethod
    def _validate_job(
        job: GenerationJob,
        capability: ModelCapability | ModeCapabilityResponse,
    ) -> str | None:
        limits = capability.limits
        if len(job.inputs.prompt) > limits.max_prompt_length:
            return "Prompt exceeds the model limit"

        counts = {"image": 0, "video": 0, "audio": 0}
        allowed_types = set(capability.input_media_types)
        for asset in job.inputs.assets:
            if asset.media_type not in allowed_types:
                return f"Input type {asset.media_type} is not supported"
            counts[asset.media_type] += 1
        if counts["image"] > limits.max_images:
            return "Image input count exceeds the model limit"
        if counts["video"] > limits.max_videos:
            return "Video input count exceeds the model limit"
        if counts["audio"] > limits.max_audio:
            return "Audio input count exceeds the model limit"
        if job.output.face_enabled and not capability.supports_face:
            return "Face input is not supported by the model"
        if job.output.duration_seconds not in limits.duration_seconds:
            return "Duration is not supported by the model"
        if job.output.aspect_ratio not in limits.aspect_ratios:
            return "Aspect ratio is not supported by the model"
        if job.output.resolution not in limits.resolutions:
            return "Resolution is not supported by the model"
        if job.output.count not in limits.output_counts:
            return "Output count is not supported by the model"
        return None
