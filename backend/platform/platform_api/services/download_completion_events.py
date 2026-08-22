from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from typing import Callable
from uuid import UUID

from pydantic import ValidationError

from ..models import DownloadCompletionSource
from ..schemas import (
    EdgeGatewayDownloadCompletionRequest,
    ObsAccessLogDownloadCompletionRequest,
)
from .errors import DomainError

DownloadCompletionEventPayload = (
    EdgeGatewayDownloadCompletionRequest | ObsAccessLogDownloadCompletionRequest
)


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class DownloadCompletionEventVerificationError(DomainError):
    def __init__(
        self,
        message: str = "Download-completion event signature is invalid",
    ) -> None:
        super().__init__(
            message,
            "download_completion_event_unauthorized",
            401,
        )


class DownloadCompletionEventPayloadError(DomainError):
    def __init__(
        self,
        message: str = "Download-completion event payload is invalid",
    ) -> None:
        super().__init__(
            message,
            "download_completion_event_invalid",
            422,
        )


@dataclass(frozen=True)
class DownloadCompletionEventEvidence:
    event_id: str
    event_timestamp: datetime
    payload_sha256: str


class DownloadCompletionEventVerifier:
    """Verify source-bound artifact download-completion events.

    The endpoint supplies its fixed source; callers cannot select a secret in
    the request body. The complete Pydantic contract is evaluated only after
    the exact body has passed source-bound HMAC verification.
    """

    _DOMAIN = b"download-completion.v1\n"

    def __init__(
        self,
        *,
        edge_gateway_signing_secret: str,
        obs_access_log_signing_secret: str,
        max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not edge_gateway_signing_secret:
            raise ValueError("Edge-gateway signing secret must not be empty")
        if not obs_access_log_signing_secret:
            raise ValueError("OBS access-log signing secret must not be empty")
        edge_gateway_secret_bytes = edge_gateway_signing_secret.encode("utf-8")
        obs_access_log_secret_bytes = obs_access_log_signing_secret.encode("utf-8")
        if hmac.compare_digest(
            edge_gateway_secret_bytes,
            obs_access_log_secret_bytes,
        ):
            raise ValueError(
                "Download-completion sources must use independent signing secrets"
            )
        if max_age_seconds < 30:
            raise ValueError(
                "Download-completion signature replay window must be at least 30 seconds"
            )
        self._secrets = {
            DownloadCompletionSource.EDGE_GATEWAY: edge_gateway_secret_bytes,
            DownloadCompletionSource.OBS_ACCESS_LOG: obs_access_log_secret_bytes,
        }
        self._max_age_seconds = max_age_seconds
        self._clock = clock

    @staticmethod
    def _decode_strict_object(raw_body: bytes) -> dict[str, object]:
        try:
            decoded = json.loads(
                raw_body,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            raise DownloadCompletionEventPayloadError() from None
        if not isinstance(decoded, dict):
            raise DownloadCompletionEventPayloadError()
        return decoded

    @staticmethod
    def _parse_headers(
        *,
        event_id: str | None,
        timestamp: str | None,
        signature: str | None,
    ) -> tuple[str, str, datetime, str]:
        if not event_id or not timestamp or not signature:
            raise DownloadCompletionEventVerificationError(
                "Download-completion signature headers are required"
            )
        try:
            canonical_event_id = str(UUID(event_id))
            timestamp_value = int(timestamp)
            event_timestamp = datetime.fromtimestamp(
                timestamp_value,
                tz=timezone.utc,
            )
        except (OverflowError, OSError, TypeError, ValueError):
            raise DownloadCompletionEventVerificationError() from None
        if canonical_event_id != event_id or str(timestamp_value) != timestamp:
            raise DownloadCompletionEventVerificationError()
        if not signature.startswith("v1=") or len(signature) != 67:
            raise DownloadCompletionEventVerificationError()
        supplied_digest = signature[3:]
        if any(character not in "0123456789abcdef" for character in supplied_digest):
            raise DownloadCompletionEventVerificationError()
        return event_id, timestamp, event_timestamp, supplied_digest

    @staticmethod
    def _parse_payload(
        decoded: dict[str, object],
        source: DownloadCompletionSource,
    ) -> DownloadCompletionEventPayload:
        schema = (
            EdgeGatewayDownloadCompletionRequest
            if source == DownloadCompletionSource.EDGE_GATEWAY
            else ObsAccessLogDownloadCompletionRequest
        )
        try:
            return schema.model_validate(decoded)
        except ValidationError:
            raise DownloadCompletionEventPayloadError() from None

    def verify(
        self,
        raw_body: bytes,
        *,
        source: DownloadCompletionSource,
        event_id: str | None,
        timestamp: str | None,
        signature: str | None,
    ) -> tuple[
        DownloadCompletionEventPayload,
        DownloadCompletionEventEvidence,
    ]:
        if source not in self._secrets:
            raise DownloadCompletionEventVerificationError(
                "Download-completion event source is not authorized"
            )
        (
            canonical_event_id,
            canonical_timestamp,
            event_timestamp,
            supplied_digest,
        ) = self._parse_headers(
            event_id=event_id,
            timestamp=timestamp,
            signature=signature,
        )
        timestamp_value = int(canonical_timestamp)
        if abs(self._clock() - timestamp_value) > self._max_age_seconds:
            raise DownloadCompletionEventVerificationError(
                "Download-completion event signature has expired"
            )

        signing_input = (
            self._DOMAIN
            + source.value.encode("ascii")
            + b"\n"
            + canonical_timestamp.encode("ascii")
            + b"\n"
            + canonical_event_id.encode("ascii")
            + b"\n"
            + raw_body
        )
        expected_digest = hmac.new(
            self._secrets[source],
            signing_input,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise DownloadCompletionEventVerificationError()

        decoded = self._decode_strict_object(raw_body)
        payload = self._parse_payload(decoded, source)
        return payload, DownloadCompletionEventEvidence(
            event_id=canonical_event_id,
            event_timestamp=event_timestamp,
            payload_sha256=hashlib.sha256(raw_body).hexdigest(),
        )
