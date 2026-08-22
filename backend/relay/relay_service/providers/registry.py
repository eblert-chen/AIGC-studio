from __future__ import annotations

import importlib
from collections.abc import Iterable
from typing import Any

from ..config import RelaySettings
from .base import ProviderAdapter, ProviderChannelType
from .mock import MockProviderAdapter
from .pool import ProviderAccountPool
from .router import ProviderRouter


def load_provider_factory(spec: str) -> list[ProviderAdapter]:
    """Load one adapter plugin without importing it in the Relay upper layer."""

    module_name, separator, attribute_name = spec.partition(":")
    if (
        not separator
        or not module_name.strip()
        or not attribute_name.strip()
        or ":" in attribute_name
    ):
        raise RuntimeError(
            "Provider factory must use the format 'python.module:factory'"
        )
    try:
        module = importlib.import_module(module_name)
        factory: Any = getattr(module, attribute_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"Configured Provider factory could not be imported: {spec}"
        ) from exc
    if not callable(factory):
        raise RuntimeError(f"Configured Provider factory is not callable: {spec}")
    try:
        created = factory()
    except Exception as exc:
        raise RuntimeError(
            f"Configured Provider factory failed during initialization: {spec}"
        ) from exc

    if isinstance(created, ProviderAdapter):
        adapters = [created]
    elif isinstance(created, Iterable) and not isinstance(
        created, (str, bytes, dict)
    ):
        adapters = list(created)
    else:
        raise RuntimeError(
            f"Provider factory must return an adapter or iterable: {spec}"
        )
    if not adapters or any(
        not isinstance(adapter, ProviderAdapter) for adapter in adapters
    ):
        raise RuntimeError(
            f"Provider factory returned no valid Provider adapters: {spec}"
        )
    return adapters


def load_provider_adapters(settings: RelaySettings) -> list[ProviderAdapter]:
    adapters: list[ProviderAdapter] = []
    for spec in settings.provider_factories:
        adapters.extend(load_provider_factory(spec))

    if settings.environment != "production" and (
        settings.runtime_mode == "memory" or settings.enable_mock_provider
    ):
        adapters.append(MockProviderAdapter())

    try:
        manifests = [adapter.manifest for adapter in adapters]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Provider route metadata is invalid") from exc
    route_ids = [manifest.route_id for manifest in manifests]
    duplicate_routes = sorted(
        route_id
        for route_id in set(route_ids)
        if route_ids.count(route_id) > 1
    )
    if duplicate_routes:
        raise RuntimeError(
            "Provider route identities must be unique: "
            + ", ".join(duplicate_routes)
        )

    if settings.runtime_mode == "production" and not adapters:
        raise RuntimeError(
            "Persistent Relay runtime has no Provider adapter. Configure "
            "RELAY_PROVIDER_FACTORIES=python.module:factory, or explicitly "
            "enable the Mock Provider in a development environment."
        )

    if settings.environment == "production":
        unverified = [
            manifest.route_id
            for manifest in manifests
            if not manifest.production_ready
        ]
        if unverified:
            raise RuntimeError(
                "Production Relay rejected Provider adapters that are not "
                "explicitly production-ready: " + ", ".join(unverified)
            )
        mocks = [
            manifest.route_id
            for manifest in manifests
            if manifest.channel_type == ProviderChannelType.MOCK
        ]
        if mocks:
            raise RuntimeError(
                "Production Relay rejected Mock Provider routes: "
                + ", ".join(mocks)
            )

    return adapters


def build_provider_router(
    settings: RelaySettings,
    *,
    account_pool: ProviderAccountPool | None = None,
) -> ProviderRouter:
    return ProviderRouter(
        load_provider_adapters(settings),
        failure_threshold=settings.provider_failure_threshold,
        cooldown_seconds=settings.provider_cooldown_seconds,
        healthcheck_timeout_seconds=(
            settings.provider_healthcheck_timeout_seconds
        ),
        account_pool=account_pool,
    )
