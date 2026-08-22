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

from ..schemas import ChannelCostCreateRequest
from .errors import DomainError


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class ChannelCostEventVerificationError(DomainError):
    def __init__(self, message: str = "Relay channel-cost signature is invalid") -> None:
        super().__init__(message, "channel_cost_event_unauthorized", 401)


class ChannelCostEventPayloadError(DomainError):
    def __init__(self, message: str = "Relay channel-cost payload is invalid") -> None:
        super().__init__(message, "channel_cost_event_invalid", 422)


@dataclass(frozen=True)
class ChannelCostEventEvidence:
    event_id: str
    delivery_timestamp: datetime
    payload_sha256: str


class ChannelCostEventVerifier:
    """Verify new-api's signed channel-cost delivery contract.

    Development and test deployments may explicitly allow unsigned legacy
    callers. If any signature header is present, however, the complete signed
    contract is always required and verified.
    """

    def __init__(
        self,
        signing_secret: str,
        *,
        signature_required: bool,
        max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not signing_secret:
            raise ValueError("Channel-cost signing secret must not be empty")
        if max_age_seconds < 30:
            raise ValueError(
                "Channel-cost signature replay window must be at least 30 seconds"
            )
        self._secret = signing_secret.encode("utf-8")
        self._signature_required = signature_required
        self._max_age_seconds = max_age_seconds
        self._clock = clock

    @staticmethod
    def _parse_payload(raw_body: bytes) -> ChannelCostCreateRequest:
        try:
            decoded = json.loads(
                raw_body,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            return ChannelCostCreateRequest.model_validate(decoded)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ):
            raise ChannelCostEventPayloadError() from None

    def verify(
        self,
        raw_body: bytes,
        *,
        event_id: str | None,
        timestamp: str | None,
        signature: str | None,
    ) -> tuple[ChannelCostCreateRequest, ChannelCostEventEvidence | None]:
        headers = (event_id, timestamp, signature)
        if not any(headers):
            if self._signature_required:
                raise ChannelCostEventVerificationError(
                    "Relay channel-cost signature headers are required"
                )
            return self._parse_payload(raw_body), None
        if not all(headers):
            raise ChannelCostEventVerificationError(
                "Relay channel-cost signature headers are incomplete"
            )

        assert event_id is not None
        assert timestamp is not None
        assert signature is not None
        try:
            canonical_event_id = str(UUID(event_id))
            timestamp_value = int(timestamp)
            delivery_timestamp = datetime.fromtimestamp(
                timestamp_value, tz=timezone.utc
            )
        except (OverflowError, OSError, TypeError, ValueError):
            raise ChannelCostEventVerificationError() from None
        if canonical_event_id != event_id or str(timestamp_value) != timestamp:
            raise ChannelCostEventVerificationError()
        if abs(self._clock() - timestamp_value) > self._max_age_seconds:
            raise ChannelCostEventVerificationError(
                "Relay channel-cost signature has expired"
            )
        if not signature.startswith("v1=") or len(signature) != 67:
            raise ChannelCostEventVerificationError()
        supplied_digest = signature[3:]
        if any(character not in "0123456789abcdef" for character in supplied_digest):
            raise ChannelCostEventVerificationError()

        signing_input = (
            timestamp.encode("ascii")
            + b"."
            + event_id.encode("ascii")
            + b"."
            + raw_body
        )
        expected_digest = hmac.new(
            self._secret, signing_input, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_digest, expected_digest):
            raise ChannelCostEventVerificationError()

        payload = self._parse_payload(raw_body)
        return payload, ChannelCostEventEvidence(
            event_id=event_id,
            delivery_timestamp=delivery_timestamp,
            payload_sha256=hashlib.sha256(raw_body).hexdigest(),
        )
