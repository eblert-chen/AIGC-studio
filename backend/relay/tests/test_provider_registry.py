from __future__ import annotations

import sys
from types import ModuleType

import pytest

from relay_service.config import RelaySettings
from relay_service.providers.base import ProviderChannelType
from relay_service.providers.mock import MockProviderAdapter
from relay_service.providers.registry import load_provider_adapters


def persistent_settings(**updates) -> RelaySettings:
    values = {
        "environment": "development",
        "runtime_mode": "production",
        "database_url": "postgresql+asyncpg://db/relay",
        "redis_url": "redis://queue",
        "artifact_store": "memory",
    }
    values.update(updates)
    return RelaySettings(**values)


def test_memory_development_registers_mock_implicitly() -> None:
    adapters = load_provider_adapters(RelaySettings())
    assert [adapter.name for adapter in adapters] == ["mock-video"]


def test_persistent_runtime_without_provider_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="RELAY_PROVIDER_FACTORIES"):
        load_provider_adapters(persistent_settings())


def test_persistent_development_can_explicitly_enable_mock() -> None:
    adapters = load_provider_adapters(
        persistent_settings(enable_mock_provider=True)
    )
    assert [adapter.name for adapter in adapters] == ["mock-video"]


def test_production_rejects_mock_factory() -> None:
    settings = persistent_settings(
        environment="production",
        artifact_store="huawei_obs",
        provider_factories=(
            "relay_service.providers.mock:create_mock_provider",
        ),
    )
    with pytest.raises(RuntimeError, match="not explicitly production-ready"):
        load_provider_adapters(settings)


def test_production_loads_explicit_real_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RealProviderForTest(MockProviderAdapter):
        name = "real-provider-for-test"
        channel_type = ProviderChannelType.THIRD_PARTY_API
        production_ready = True

    module = ModuleType("relay_test_provider_plugin")
    module.build = lambda: RealProviderForTest()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    settings = persistent_settings(
        environment="production",
        artifact_store="huawei_obs",
        provider_factories=("relay_test_provider_plugin:build",),
    )

    adapters = load_provider_adapters(settings)

    assert [adapter.name for adapter in adapters] == [
        "real-provider-for-test"
    ]
    assert adapters[0].production_ready is True


def test_production_rejects_mock_even_when_marked_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisguisedMock(MockProviderAdapter):
        name = "disguised-mock"
        production_ready = True

    module = ModuleType("relay_disguised_mock_plugin")
    module.build = lambda: DisguisedMock()
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with pytest.raises(RuntimeError, match="rejected Mock Provider routes"):
        load_provider_adapters(
            persistent_settings(
                environment="production",
                artifact_store="huawei_obs",
                provider_factories=(f"{module.__name__}:build",),
            )
        )


@pytest.mark.parametrize(
    "channel_type",
    [
        ProviderChannelType.REVERSE,
        ProviderChannelType.THIRD_PARTY_API,
        ProviderChannelType.OFFICIAL,
    ],
)
def test_factory_accepts_each_real_channel_class(
    monkeypatch: pytest.MonkeyPatch,
    channel_type: ProviderChannelType,
) -> None:
    class ClassifiedProvider(MockProviderAdapter):
        name = f"classified-{channel_type.value}"
        production_ready = True

    ClassifiedProvider.channel_type = channel_type
    module = ModuleType(f"relay_{channel_type.value}_plugin")
    module.build = lambda: ClassifiedProvider()
    monkeypatch.setitem(sys.modules, module.__name__, module)

    adapters = load_provider_adapters(
        persistent_settings(
            provider_factories=(f"{module.__name__}:build",)
        )
    )

    assert adapters[0].manifest.channel_type == channel_type


def test_factory_can_return_multiple_accounts_for_one_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("relay_test_provider_pool")
    module.build = lambda: [
        MockProviderAdapter(account_id="account-a"),
        MockProviderAdapter(account_id="account-b"),
    ]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    adapters = load_provider_adapters(
        persistent_settings(
            provider_factories=("relay_test_provider_pool:build",)
        )
    )

    assert [adapter.route_id for adapter in adapters] == [
        "mock-video@account-a",
        "mock-video@account-b",
    ]


def test_factory_rejects_duplicate_provider_account_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("relay_test_duplicate_provider_pool")
    module.build = lambda: [
        MockProviderAdapter(account_id="duplicate"),
        MockProviderAdapter(account_id="duplicate"),
    ]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with pytest.raises(RuntimeError, match="route identities must be unique"):
        load_provider_adapters(
            persistent_settings(
                provider_factories=(
                    "relay_test_duplicate_provider_pool:build",
                )
            )
        )
