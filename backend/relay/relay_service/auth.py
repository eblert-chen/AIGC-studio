from __future__ import annotations

import json
import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from .errors import RelayError


DEVELOPMENT_CLIENT_ID = "development-client"
DEVELOPMENT_API_KEY = "development-api-key-change-before-deploy"
DEVELOPMENT_TENANT_ID = "00000000-0000-4000-8000-000000000001"
MINIMUM_PRODUCTION_API_KEY_BYTES = 32
GENERATION_INVOKE_SCOPE = "generation:invoke"
SUBMISSION_RECONCILIATION_SCOPE = "operations:submission-reconciliation"
KNOWN_CLIENT_SCOPES = frozenset(
    {GENERATION_INVOKE_SCOPE, SUBMISSION_RECONCILIATION_SCOPE}
)
DEFAULT_CLIENT_SCOPES = frozenset({GENERATION_INVOKE_SCOPE})

_CLIENT_ID_HEADER = APIKeyHeader(
    name="X-Client-ID",
    scheme_name="RelayClientId",
    description="Stable service-client identifier",
    auto_error=False,
)
_API_KEY_HEADER = APIKeyHeader(
    name="X-API-Key",
    scheme_name="RelayApiKey",
    description="Secret service-client credential",
    auto_error=False,
)

_KNOWN_DEVELOPMENT_API_KEYS = frozenset(
    {
        DEVELOPMENT_API_KEY,
    }
)
_PRODUCTION_PLACEHOLDER_KEY_MARKERS = (
    "changeme",
    "replaceme",
    "replacewith",
    "placeholder",
    "yourapikey",
)


@dataclass(frozen=True)
class ClientPrincipal:
    client_id: str
    tenant_id: UUID
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ClientCredential:
    tenant_id: UUID
    api_key: str
    scopes: frozenset[str] = DEFAULT_CLIENT_SCOPES


class ClientAuthenticator(ABC):
    """Boundary for relay-to-relay service authentication."""

    @abstractmethod
    async def authenticate(self, client_id: str, api_key: str) -> ClientPrincipal:
        """Return a trusted tenant identity or raise RelayError."""


class StaticClientAuthenticator(ClientAuthenticator):
    """Static service-client credentials loaded at process startup."""

    def __init__(self, credentials: dict[str, ClientCredential]) -> None:
        self._credentials = credentials.copy()

    async def authenticate(self, client_id: str, api_key: str) -> ClientPrincipal:
        credential = self._credentials.get(client_id)
        # Always perform a constant-time comparison, including for an unknown client.
        expected = credential.api_key if credential else "\0" * max(len(api_key), 1)
        key_matches = secrets.compare_digest(
            api_key.encode("utf-8"), expected.encode("utf-8")
        )
        if credential is None or not key_matches:
            raise RelayError(
                "INVALID_CLIENT_CREDENTIALS",
                "Client credentials are invalid",
                status_code=401,
            )
        return ClientPrincipal(
            client_id=client_id,
            tenant_id=credential.tenant_id,
            scopes=credential.scopes,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Literal["development", "production"] = "development",
    ) -> "StaticClientAuthenticator":
        """Load development credentials or a production credential map.

        ``RELAY_CLIENT_CREDENTIALS_JSON`` uses this object shape::

            {
              "client-id": {
                "tenant_id": "00000000-0000-4000-8000-000000000001",
                "api_key": "...",
                "scopes": ["operations:submission-reconciliation"]
              }
            }

        Production never falls back to the single-client development variables.
        """

        if environment not in {"development", "production"}:
            raise RuntimeError(
                "environment must be 'development' or 'production'"
            )

        serialized_credentials = os.getenv("RELAY_CLIENT_CREDENTIALS_JSON")
        if serialized_credentials is not None:
            return cls(
                _parse_credentials_json(
                    serialized_credentials,
                    production=environment == "production",
                )
            )

        if environment == "production":
            raise RuntimeError(
                "RELAY_CLIENT_CREDENTIALS_JSON is required in production"
            )

        client_id = os.getenv("RELAY_CLIENT_ID", DEVELOPMENT_CLIENT_ID)
        api_key = os.getenv("RELAY_API_KEY", DEVELOPMENT_API_KEY)
        tenant_value = os.getenv("RELAY_TENANT_ID", DEVELOPMENT_TENANT_ID)
        try:
            tenant_id = UUID(tenant_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "RELAY_TENANT_ID must be a valid UUID"
            ) from exc
        return cls(
            {
                client_id: ClientCredential(
                    tenant_id=tenant_id,
                    api_key=api_key,
                )
            }
        )


def _parse_credentials_json(
    serialized_credentials: str,
    *,
    production: bool,
) -> dict[str, ClientCredential]:
    try:
        payload = json.loads(
            serialized_credentials,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "RELAY_CLIENT_CREDENTIALS_JSON must be valid JSON without "
            "duplicate keys"
        ) from exc

    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(
            "RELAY_CLIENT_CREDENTIALS_JSON must be a non-empty object"
        )

    credentials: dict[str, ClientCredential] = {}
    key_owners: dict[str, str] = {}
    tenant_owners: dict[UUID, list[tuple[str, frozenset[str]]]] = {}
    for client_id, raw_credential in payload.items():
        if (
            not isinstance(client_id, str)
            or not client_id
            or client_id != client_id.strip()
            or len(client_id) > 128
        ):
            raise RuntimeError(
                "Each RELAY_CLIENT_CREDENTIALS_JSON client id must be "
                "1-128 non-whitespace-padded characters"
            )
        if not isinstance(raw_credential, dict) or not {
            "tenant_id",
            "api_key",
        }.issubset(raw_credential) or not set(raw_credential).issubset(
            {"tenant_id", "api_key", "scopes"}
        ):
            raise RuntimeError(
                f"Credential for client '{client_id}' must contain "
                "'tenant_id' and 'api_key', with optional 'scopes'"
            )

        tenant_value = raw_credential["tenant_id"]
        api_key = raw_credential["api_key"]
        if not isinstance(tenant_value, str):
            raise RuntimeError(
                f"tenant_id for client '{client_id}' must be a UUID string"
            )
        try:
            tenant_id = UUID(tenant_value)
        except ValueError as exc:
            raise RuntimeError(
                f"tenant_id for client '{client_id}' must be a valid UUID"
            ) from exc
        raw_scopes = raw_credential.get("scopes", list(DEFAULT_CLIENT_SCOPES))
        if (
            not isinstance(raw_scopes, list)
            or any(not isinstance(scope, str) for scope in raw_scopes)
            or len(raw_scopes) != len(set(raw_scopes))
        ):
            raise RuntimeError(
                f"scopes for client '{client_id}' must be a unique string list"
            )
        scopes = frozenset(raw_scopes)
        unknown_scopes = scopes - KNOWN_CLIENT_SCOPES
        if unknown_scopes:
            raise RuntimeError(
                f"scopes for client '{client_id}' contain unknown values"
            )
        if (
            SUBMISSION_RECONCILIATION_SCOPE in scopes
            and scopes != frozenset({SUBMISSION_RECONCILIATION_SCOPE})
        ):
            raise RuntimeError(
                f"client '{client_id}' reconciliation credential must be "
                "operations-only"
            )

        existing_tenant_owners = tenant_owners.setdefault(tenant_id, [])
        if production:
            for existing_client, existing_scopes in existing_tenant_owners:
                shares_ops_tenant = (
                    (SUBMISSION_RECONCILIATION_SCOPE in scopes)
                    != (SUBMISSION_RECONCILIATION_SCOPE in existing_scopes)
                )
                if not shares_ops_tenant:
                    raise RuntimeError(
                        f"tenant_id for client '{client_id}' is already "
                        f"assigned to client '{existing_client}'; production "
                        "service clients must use distinct tenant identities "
                        "except for one scoped reconciliation credential"
                    )
        existing_tenant_owners.append((client_id, scopes))
        if not isinstance(api_key, str) or not api_key:
            raise RuntimeError(
                f"api_key for client '{client_id}' must be a non-empty string"
            )
        existing_owner = key_owners.get(api_key)
        if existing_owner is not None:
            raise RuntimeError(
                f"api_key for client '{client_id}' is already assigned "
                f"to client '{existing_owner}'; clients must use unique "
                "api_key values"
            )
        key_owners[api_key] = client_id

        if production:
            if (
                len(api_key.encode("utf-8"))
                < MINIMUM_PRODUCTION_API_KEY_BYTES
            ):
                raise RuntimeError(
                    f"api_key for client '{client_id}' must contain at least "
                    f"{MINIMUM_PRODUCTION_API_KEY_BYTES} bytes in production"
                )
            if api_key in _KNOWN_DEVELOPMENT_API_KEYS:
                raise RuntimeError(
                    f"api_key for client '{client_id}' uses a known "
                    "development default"
                )
            if _looks_like_production_placeholder(api_key):
                raise RuntimeError(
                    f"api_key for client '{client_id}' uses an obvious "
                    "placeholder and must be replaced in production"
                )

        credentials[client_id] = ClientCredential(
            tenant_id=tenant_id,
            api_key=api_key,
            scopes=scopes,
        )

    return credentials


def _looks_like_production_placeholder(api_key: str) -> bool:
    normalized = "".join(
        character for character in api_key.casefold() if character.isalnum()
    )
    return any(
        marker in normalized
        for marker in _PRODUCTION_PLACEHOLDER_KEY_MARKERS
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def authentication_dependency(
    authenticator: ClientAuthenticator,
):
    async def authenticate_request(
        client_id: str | None = Security(_CLIENT_ID_HEADER),
        api_key: str | None = Security(_API_KEY_HEADER),
    ) -> ClientPrincipal:
        if not client_id or not api_key:
            raise RelayError(
                "CLIENT_AUTHENTICATION_REQUIRED",
                "X-Client-ID and X-API-Key headers are required",
                status_code=401,
            )
        return await authenticator.authenticate(client_id, api_key)

    return authenticate_request


def required_scope_dependency(authenticate_client, required_scope: str):
    """Build an authorization dependency without weakening tenant isolation."""

    async def require_scope(
        principal: ClientPrincipal = Depends(authenticate_client),
    ) -> ClientPrincipal:
        if required_scope not in principal.scopes:
            raise RelayError(
                "INSUFFICIENT_CLIENT_SCOPE",
                "Client credential is not authorized for this operation",
                status_code=403,
                details={"required_scope": required_scope},
            )
        return principal

    return require_scope
