from __future__ import annotations

import asyncio

import pytest

from relay_service.providers.base import ProviderContractError
from relay_service.providers.verify import inspect_provider_factories


def test_conformance_report_contains_no_provider_credentials() -> None:
    report = asyncio.run(
        inspect_provider_factories(
            ["relay_service.providers.mock:create_mock_provider"]
        )
    )

    assert report["valid"] is True
    assert report["routes"][0]["channel_type"] == "mock"
    assert report["routes"][0]["contract_version"] == 1
    serialized = str(report).lower()
    assert "secret" not in serialized
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_production_conformance_rejects_mock_route() -> None:
    with pytest.raises(ProviderContractError, match="rejected routes"):
        asyncio.run(
            inspect_provider_factories(
                ["relay_service.providers.mock:create_mock_provider"],
                require_production=True,
            )
        )
