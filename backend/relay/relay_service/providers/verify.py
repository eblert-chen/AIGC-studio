from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from .base import ProviderChannelType, ProviderContractError
from .registry import load_provider_factory
from .router import ProviderRouter


async def inspect_provider_factories(
    factory_specs: Sequence[str],
    *,
    require_production: bool = False,
) -> dict[str, Any]:
    """Validate adapter plugins and return a secret-free conformance report."""

    adapters = []
    for spec in factory_specs:
        adapters.extend(load_provider_factory(spec))
    router = ProviderRouter(adapters)
    try:
        profiles = await router.route_profiles()
        if require_production:
            rejected = [
                profile.manifest.route_id
                for profile in profiles
                if not profile.manifest.production_ready
                or profile.manifest.channel_type == ProviderChannelType.MOCK
            ]
            if rejected:
                raise ProviderContractError(
                    "Production conformance rejected routes: "
                    + ", ".join(rejected)
                )
        return {
            "object": "provider_adapter_conformance",
            "valid": True,
            "routes": [
                {
                    "contract_version": profile.manifest.contract_version,
                    "route_id": profile.manifest.route_id,
                    "provider_name": profile.manifest.provider_name,
                    "account_id": profile.manifest.account_id,
                    "channel_type": profile.manifest.channel_type.value,
                    "priority": profile.manifest.priority,
                    "max_concurrency": profile.manifest.max_concurrency,
                    "requests_per_minute": (
                        profile.manifest.requests_per_minute
                    ),
                    "production_ready": profile.manifest.production_ready,
                    "capabilities": [
                        {
                            "model": capability.model,
                            "modes": [mode.value for mode in capability.modes],
                            "input_media_types": capability.input_media_types,
                            "supports_face": capability.supports_face,
                            "limits": capability.limits.model_dump(mode="json"),
                        }
                        for capability in profile.capabilities
                    ],
                }
                for profile in profiles
            ],
        }
    finally:
        await router.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Relay provider adapter factories"
    )
    parser.add_argument(
        "factories",
        nargs="+",
        help="One or more python.module:factory adapter plugins",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="also require production_ready routes and reject Mock",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = asyncio.run(
            inspect_provider_factories(
                arguments.factories,
                require_production=arguments.production,
            )
        )
    except (ProviderContractError, RuntimeError, ValueError) as exc:
        print(f"adapter contract failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
