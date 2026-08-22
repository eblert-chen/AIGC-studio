from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta
from uuid import uuid4

from .callback import AioHttpCallbackTransport
from .config import RelaySettings
from .provider_monitoring import (
    ProviderAlertDispatcher,
    ProviderMonitor,
    ProviderMonitorPolicy,
)
from .providers.registry import build_provider_router
from .sql_repository import SqlAlchemyJobRepository


logger = logging.getLogger("relay.provider-monitor")


async def consume_monitor(
    monitor: ProviderMonitor,
    stop: asyncio.Event,
    *,
    interval_seconds: float,
) -> None:
    while not stop.is_set():
        try:
            await monitor.run_cycle()
        except Exception:
            # Provider/driver exceptions can contain credentials or endpoints.
            logger.warning("Provider monitoring cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def consume_alerts(
    dispatcher: ProviderAlertDispatcher,
    stop: asyncio.Event,
    *,
    poll_seconds: float,
) -> None:
    while not stop.is_set():
        try:
            delivered = await dispatcher.dispatch_once()
        except Exception:
            logger.warning("Provider alert dispatch cycle failed")
            delivered = 0
        if delivered == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass


async def run() -> None:
    settings = RelaySettings.from_environment()
    if settings.runtime_mode != "production":
        raise RuntimeError(
            "Provider monitor worker requires RELAY_RUNTIME_MODE=production"
        )
    if not settings.provider_monitor_enabled:
        raise RuntimeError(
            "Provider monitor worker is disabled by configuration"
        )
    assert settings.database_url is not None
    repository = SqlAlchemyJobRepository.from_url(settings.database_url)
    router = build_provider_router(settings, account_pool=repository)
    await router.validate_configuration()
    retired_providers = frozenset(
        settings.provider_monitor_retired_providers
    )
    overlap = router.provider_names & retired_providers
    if overlap:
        raise RuntimeError(
            "Configured Relay providers cannot also be marked retired"
        )
    policy = ProviderMonitorPolicy(
        outcome_window=timedelta(
            seconds=settings.provider_monitor_window_seconds
        ),
        min_outcomes=settings.provider_monitor_min_outcomes,
        min_success_rate=settings.provider_monitor_min_success_rate,
        widespread_failure_ratio=(
            settings.provider_monitor_widespread_failure_ratio
        ),
        widespread_failure_min_routes=(
            settings.provider_monitor_widespread_min_routes
        ),
        batch_disabled_threshold=(
            settings.provider_monitor_batch_disabled_threshold
        ),
        breach_cycles=settings.provider_monitor_breach_cycles,
        recovery_cycles=settings.provider_monitor_recovery_cycles,
        cycle_lease=timedelta(
            seconds=settings.provider_monitor_lease_seconds
        ),
        cycle_interval=timedelta(
            seconds=settings.provider_monitor_interval_seconds
        ),
        sample_retention=timedelta(
            days=settings.provider_monitor_retention_days
        ),
    )
    monitor = ProviderMonitor(
        router,
        repository,
        policy=policy,
        worker_id=f"provider-monitor-{uuid4()}",
        retired_provider_names=retired_providers,
    )
    alert_dispatcher = None
    if settings.provider_alert_webhook_url is not None:
        assert settings.provider_alert_signing_secret is not None
        alert_dispatcher = ProviderAlertDispatcher(
            repository,
            webhook_url=settings.provider_alert_webhook_url,
            signing_secret=settings.provider_alert_signing_secret,
            production=settings.environment == "production",
            transport=AioHttpCallbackTransport(
                timeout_seconds=settings.provider_alert_timeout_seconds
            ),
            max_attempts=settings.provider_alert_max_attempts,
            claim_lease_seconds=(
                settings.provider_alert_claim_lease_seconds
            ),
            base_delay_seconds=settings.provider_alert_base_delay_seconds,
            max_delay_seconds=settings.provider_alert_max_delay_seconds,
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    tasks = [
        asyncio.create_task(
            consume_monitor(
                monitor,
                stop,
                interval_seconds=settings.provider_monitor_interval_seconds,
            )
        )
    ]
    if alert_dispatcher is not None:
        tasks.append(
            asyncio.create_task(
                consume_alerts(
                    alert_dispatcher,
                    stop,
                    poll_seconds=settings.provider_alert_poll_seconds,
                )
            )
        )
    try:
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await router.close()
        await repository.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
