from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .relay_client import HttpxRelayClient, RelayClient
from .relay_identity import (
    LEGACY_RELAY_BACKEND_ID,
    NEW_API_RELAY_BACKEND_ID,
    NEW_API_RELAY_CONTRACT_REVISION,
    RELAY_BACKEND_ID_PATTERN,
    RELAY_CONTRACT_REVISION_PATTERN,
)

# Kept as a public compatibility name for callers that consume the v1
# provider-neutral contract.  It no longer means "legacy Relay default".
DEFAULT_RELAY_CONTRACT_REVISION = NEW_API_RELAY_CONTRACT_REVISION


class RelayBackendConfiguration(BaseModel):
    """Secret-bearing runtime configuration; never persist this model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1)
    client_id: str = Field(min_length=1, max_length=120)
    api_key: SecretStr
    contract_revision: str = Field(
        default=DEFAULT_RELAY_CONTRACT_REVISION,
        pattern=RELAY_CONTRACT_REVISION_PATTERN,
    )


@dataclass(frozen=True)
class RelayBackendAffinity:
    backend_id: str
    contract_revision: str


@dataclass(frozen=True)
class _RegisteredRelayBackend:
    affinity: RelayBackendAffinity
    client: RelayClient


class RelayBackendResolutionError(RuntimeError):
    """A persisted Relay affinity cannot be served by this deployment."""


def relay_callback_url_for_backend(
    public_callback_url: str | None,
    *,
    backend_id: str,
) -> str | None:
    """Bind a new task's callback target to its immutable backend identity."""

    if public_callback_url is None:
        return None
    if re.fullmatch(RELAY_BACKEND_ID_PATTERN, backend_id) is None:
        raise ValueError("Relay backend id is invalid")
    # The unqualified endpoint is permanently reserved for the migrated
    # legacy-default-v1 backend. This keeps already-configured Python Relay
    # exact allowlists valid while still giving every additional backend a
    # cryptographically isolated callback route.
    if backend_id == LEGACY_RELAY_BACKEND_ID:
        return public_callback_url
    return f"{public_callback_url.rstrip('/')}/{backend_id}"


class RelayBackendRegistry:
    """Resolve an immutable task affinity to a runtime-only Relay client.

    Base URLs and credentials live only in this registry's process memory. The
    database stores the stable backend id and contract revision, so changing a
    deployment default cannot redirect an already accepted task.
    """

    def __init__(
        self,
        *,
        default_backend_id: str = NEW_API_RELAY_BACKEND_ID,
        default_contract_revision: str = DEFAULT_RELAY_CONTRACT_REVISION,
        clients: Mapping[str, tuple[str, RelayClient]] | None = None,
        fallback_client_provider: Callable[[], RelayClient | None] | None = None,
    ) -> None:
        if re.fullmatch(RELAY_BACKEND_ID_PATTERN, default_backend_id) is None:
            raise ValueError("default Relay backend id is invalid")
        if (
            re.fullmatch(RELAY_CONTRACT_REVISION_PATTERN, default_contract_revision)
            is None
        ):
            raise ValueError("default Relay contract revision is invalid")
        registered: dict[str, _RegisteredRelayBackend] = {}
        for backend_id, (contract_revision, client) in (clients or {}).items():
            if re.fullmatch(RELAY_BACKEND_ID_PATTERN, backend_id) is None:
                raise ValueError(f"Relay backend id is invalid: {backend_id!r}")
            if re.fullmatch(RELAY_CONTRACT_REVISION_PATTERN, contract_revision) is None:
                raise ValueError(
                    f"Relay contract revision is invalid for backend {backend_id!r}"
                )
            registered[backend_id] = _RegisteredRelayBackend(
                affinity=RelayBackendAffinity(backend_id, contract_revision),
                client=client,
            )
        self._default_affinity = RelayBackendAffinity(
            default_backend_id,
            default_contract_revision,
        )
        self._registered = registered
        self._fallback_client_provider = fallback_client_provider

    @property
    def default_affinity(self) -> RelayBackendAffinity:
        registered = self._registered.get(self._default_affinity.backend_id)
        return registered.affinity if registered is not None else self._default_affinity

    def resolve(self, *, backend_id: str, contract_revision: str) -> RelayClient:
        registered = self._registered.get(backend_id)
        if registered is not None:
            if registered.affinity.contract_revision != contract_revision:
                raise RelayBackendResolutionError(
                    "Relay backend contract revision does not match the persisted task"
                )
            return registered.client
        if (
            backend_id == self._default_affinity.backend_id
            and contract_revision == self._default_affinity.contract_revision
            and self._fallback_client_provider is not None
        ):
            fallback = self._fallback_client_provider()
            if fallback is not None:
                return fallback
        raise RelayBackendResolutionError(
            f"Relay backend {backend_id!r} at contract {contract_revision!r} "
            "is not configured"
        )

    def default_client_or_none(self) -> RelayClient | None:
        affinity = self.default_affinity
        try:
            return self.resolve(
                backend_id=affinity.backend_id,
                contract_revision=affinity.contract_revision,
            )
        except RelayBackendResolutionError:
            return None

    def close(self) -> None:
        seen: set[int] = set()
        for registered in self._registered.values():
            identity = id(registered.client)
            if identity in seen:
                continue
            seen.add(identity)
            close = getattr(registered.client, "close", None)
            if close is not None:
                close()


def single_client_registry(
    client: RelayClient | None,
    *,
    backend_id: str = NEW_API_RELAY_BACKEND_ID,
    contract_revision: str = DEFAULT_RELAY_CONTRACT_REVISION,
) -> RelayBackendRegistry:
    clients = {backend_id: (contract_revision, client)} if client is not None else {}
    return RelayBackendRegistry(
        default_backend_id=backend_id,
        default_contract_revision=contract_revision,
        clients=clients,
    )


def coerce_relay_backend_registry(
    value: RelayBackendRegistry | RelayClient | None,
) -> RelayBackendRegistry:
    if isinstance(value, RelayBackendRegistry):
        return value
    return single_client_registry(value)


def build_relay_backend_registry(
    *,
    default_backend_id: str,
    default_contract_revision: str,
    configurations: Mapping[str, RelayBackendConfiguration],
    legacy_base_url: str | None,
    legacy_client_id: str | None,
    legacy_api_key: str | None,
    allow_local_http: bool,
    legacy_compatibility_enabled: bool = False,
    fallback_client_provider: Callable[[], RelayClient | None] | None = None,
) -> RelayBackendRegistry:
    legacy_identity = (legacy_base_url, legacy_client_id, legacy_api_key)
    if any(legacy_identity) and not all(legacy_identity):
        raise ValueError("Legacy Relay credentials must be configured together")
    if any(legacy_identity) and not legacy_compatibility_enabled:
        raise ValueError("Legacy Relay compatibility is not enabled")
    if fallback_client_provider is not None and not legacy_compatibility_enabled:
        raise ValueError("Implicit Relay client fallback is not enabled")
    if (
        LEGACY_RELAY_BACKEND_ID in configurations
        and not legacy_compatibility_enabled
    ):
        raise ValueError("Legacy Relay compatibility is not enabled")
    clients: dict[str, tuple[str, RelayClient]] = {}
    for backend_id, configuration in configurations.items():
        clients[backend_id] = (
            configuration.contract_revision,
            HttpxRelayClient(
                base_url=configuration.base_url,
                client_id=configuration.client_id,
                api_key=configuration.api_key.get_secret_value(),
                allow_local_http=allow_local_http,
            ),
        )
    if legacy_compatibility_enabled and all(legacy_identity) and (
        LEGACY_RELAY_BACKEND_ID not in clients
        or (not configurations and default_backend_id not in clients)
    ):
        legacy_client = HttpxRelayClient(
            base_url=legacy_base_url or "",
            client_id=legacy_client_id or "",
            api_key=legacy_api_key or "",
            allow_local_http=allow_local_http,
        )
        clients.setdefault(
            LEGACY_RELAY_BACKEND_ID,
            (DEFAULT_RELAY_CONTRACT_REVISION, legacy_client),
        )
        # A singleton deployment may choose a semantic backend id before it
        # adopts RELAY_BACKENDS. Preserve the fixed legacy alias for migrated
        # rows while letting new rows use the configured default id.
        if not configurations:
            clients.setdefault(
                default_backend_id,
                (default_contract_revision, legacy_client),
            )
    return RelayBackendRegistry(
        default_backend_id=default_backend_id,
        default_contract_revision=default_contract_revision,
        clients=clients,
        fallback_client_provider=fallback_client_provider,
    )
