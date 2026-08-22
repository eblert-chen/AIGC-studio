from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable


_PROVIDER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class PublicationRequest:
    """Provider-neutral input for one publication submission.

    ``connection_config`` is deliberately opaque to the worker.  A real adapter
    may use it to locate credentials in a secret store, but callers must not log
    it or copy secrets into attempt diagnostics.
    """

    job_id: str
    provider: str
    idempotency_key: str
    external_account_id: str
    title: str
    caption: str
    media_urls: tuple[str, ...]
    connection_config: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )


@dataclass(frozen=True)
class PublicationReceipt:
    """A provider's positive confirmation that the post exists."""

    external_post_id: str
    external_post_url: str | None = None
    provider_request_id: str | None = None
    response_metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class PublisherOAuthGrant:
    """Safe result of a server-side OAuth code exchange.

    A provider plug-in must put access and refresh tokens in its own encrypted
    secret store and return only an opaque reference.  This prevents the
    customer Platform database and API responses from becoming a token dump.
    """

    external_account_id: str
    display_name: str
    credential_reference: str = field(repr=False)
    credential_expires_at: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class PublisherAdapterError(RuntimeError):
    """Base class for safe, classified adapter failures.

    Adapter messages become operator-visible diagnostics.  They must not contain
    access tokens, cookies, request authorization headers, or raw provider bodies.
    """

    def __init__(self, code: str, message: str):
        normalized_code = code.strip().lower()
        if not normalized_code or len(normalized_code) > 80:
            raise ValueError("adapter error code must be 1-80 characters")
        super().__init__(message)
        self.code = normalized_code
        self.safe_message = message


class DeterministicPublicationError(PublisherAdapterError):
    """The provider proved that no post was created; retry policy may apply."""


class TemporaryPreSubmissionError(PublisherAdapterError):
    """A temporary failure proven to have happened before provider submission."""


class SubmissionOutcomeUnknownError(PublisherAdapterError):
    """The provider may have created a post; automatic retry is forbidden."""


class PublisherReauthenticationRequired(PublisherAdapterError):
    """The connection credential is no longer authorized by the provider."""


# Short aliases make adapter implementations pleasant without weakening the
# important distinction between a safe retry and an unknown submission.
PermanentPublicationError = DeterministicPublicationError
RetryablePublicationError = TemporaryPreSubmissionError
ReauthenticationRequiredError = PublisherReauthenticationRequired


@runtime_checkable
class PublisherAdapter(Protocol):
    """Plug-in contract implemented once per publishing provider."""

    provider: str
    production_ready: bool
    is_mock: bool

    def submit(self, request: PublicationRequest) -> PublicationReceipt:
        """Submit exactly once or raise one of the classified adapter errors."""


@runtime_checkable
class PublisherOAuthAdapter(PublisherAdapter, Protocol):
    """Optional account-linking contract for a publishing adapter.

    Both methods run on the server.  The browser receives an authorization URL
    and never receives a provider access token or a secret-store reference.
    """

    oauth_display_name: str

    def build_authorization_url(self, *, state: str, redirect_uri: str) -> str:
        """Return the provider authorization URL containing the supplied state."""

    def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> PublisherOAuthGrant:
        """Exchange a one-time code and persist provider credentials securely."""


class PublisherAdapterRegistry:
    """Validated provider registry with a production fail-closed boundary."""

    def __init__(
        self,
        *,
        environment: str,
        adapters: Iterable[PublisherAdapter] = (),
    ):
        self.environment = environment.strip().lower()
        self._adapters: dict[str, PublisherAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    @property
    def oauth_providers(self) -> tuple[PublisherOAuthAdapter, ...]:
        return tuple(
            adapter
            for _, adapter in sorted(self._adapters.items())
            if isinstance(adapter, PublisherOAuthAdapter)
        )

    @property
    def production(self) -> bool:
        return self.environment in {"production", "staging"}

    def register(self, adapter: PublisherAdapter) -> None:
        provider = getattr(adapter, "provider", "").strip().lower()
        if not _PROVIDER_KEY.fullmatch(provider):
            raise ValueError("publisher provider key is invalid")
        if provider in self._adapters:
            raise ValueError(f"publisher adapter already registered: {provider}")
        if self.production and (
            bool(getattr(adapter, "is_mock", False))
            or not bool(getattr(adapter, "production_ready", False))
        ):
            raise ValueError(
                f"publisher adapter is not production-ready: {provider}"
            )
        self._adapters[provider] = adapter

    def require(self, provider: str) -> PublisherAdapter:
        normalized = provider.strip().lower()
        adapter = self._adapters.get(normalized)
        if adapter is None:
            raise KeyError(f"publisher adapter is not configured: {normalized}")
        # Re-check at use time.  This catches mutable or incorrectly implemented
        # plug-ins that changed their safety declaration after registration.
        if self.production and (
            bool(getattr(adapter, "is_mock", False))
            or not bool(getattr(adapter, "production_ready", False))
        ):
            raise RuntimeError(
                f"publisher adapter is not production-ready: {normalized}"
            )
        return adapter

    def require_oauth(self, provider: str) -> PublisherOAuthAdapter:
        adapter = self.require(provider)
        if not isinstance(adapter, PublisherOAuthAdapter):
            raise KeyError(
                f"publisher adapter does not support OAuth linking: {provider.strip().lower()}"
            )
        display_name = getattr(adapter, "oauth_display_name", "").strip()
        if not display_name or len(display_name) > 80:
            raise RuntimeError("publisher OAuth display name is invalid")
        return adapter


class MockPublisherAdapter:
    """Deterministic local adapter.  Never register this in production."""

    provider = "mock"
    production_ready = False
    is_mock = True

    def submit(self, request: PublicationRequest) -> PublicationReceipt:
        return PublicationReceipt(
            external_post_id=f"mock-{request.job_id}",
            external_post_url=(
                f"https://publisher.invalid/mock/{request.job_id}"
            ),
            provider_request_id=f"mock-request-{request.job_id}",
            response_metadata=MappingProxyType({"mock": True}),
        )


def load_publisher_adapter(
    spec: str,
    *,
    credential_manifest: Mapping[str, str] | None = None,
    require_credential_manifest: bool = False,
) -> PublisherAdapter:
    """Load a trusted ``python.module:factory`` adapter configuration."""

    module_name, separator, factory_name = spec.partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError("publisher adapter spec must be python.module:factory")
    module = importlib.import_module(module_name)
    factory: Callable[..., PublisherAdapter] = getattr(module, factory_name)
    if require_credential_manifest:
        if credential_manifest is None:
            raise ValueError(
                "production publisher adapter requires an explicit credential manifest"
            )
        signature = inspect.signature(factory)
        manifest_parameter = signature.parameters.get("credential_manifest")
        if (
            manifest_parameter is None
            or manifest_parameter.kind is not inspect.Parameter.KEYWORD_ONLY
            or manifest_parameter.default is not inspect.Parameter.empty
        ):
            raise ValueError(
                "production publisher adapter factory must require keyword-only "
                "credential_manifest"
            )
        try:
            adapter = factory(credential_manifest=credential_manifest)
        except Exception:
            raise RuntimeError("publisher adapter factory failed") from None
    else:
        adapter = factory()
    if not isinstance(adapter, PublisherAdapter):
        raise TypeError("publisher adapter factory returned an invalid adapter")
    return adapter


def build_publisher_registry(
    *,
    environment: str,
    adapters: Iterable[PublisherAdapter] = (),
    include_mock: bool = False,
) -> PublisherAdapterRegistry:
    configured = list(adapters)
    if include_mock:
        configured.append(MockPublisherAdapter())
    return PublisherAdapterRegistry(
        environment=environment,
        adapters=configured,
    )
