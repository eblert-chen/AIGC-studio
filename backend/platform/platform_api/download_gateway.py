from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Callable, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from .relay_client import RelayArtifactStorageBinding


_REGISTRATION_PATH = "/internal/v1/download-tickets"
_SIGNING_DOMAIN = b"download-edge-registration.v1\n"


class DownloadGatewayError(Exception):
    pass


class DownloadGatewayTemporaryError(DownloadGatewayError):
    pass


class DownloadGatewayOutcomeUnknownError(DownloadGatewayTemporaryError):
    """The Gateway may have committed, but Platform could not trust the ACK."""


class DownloadGatewayCommittedExpiredError(DownloadGatewayError):
    """A fully bound Gateway receipt proves the committed ticket has expired."""

    def __init__(
        self,
        receipt: "DownloadGatewayCommittedExpired",
        *,
        acknowledgement_sha256: str,
    ) -> None:
        super().__init__("Download Gateway registration is committed and expired")
        self.receipt = receipt
        self.acknowledgement_sha256 = acknowledgement_sha256


class DownloadGatewayPermanentError(DownloadGatewayError):
    pass


class DownloadGatewayTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    download_record_id: str = Field(min_length=1, max_length=36)
    company_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    asset_id: str = Field(min_length=1, max_length=160)
    issuance_request_id: str = Field(min_length=1, max_length=160)
    transfer_reference: str = Field(min_length=36, max_length=36)
    gateway_ticket_id: str = Field(min_length=36, max_length=36)
    one_time: Literal[True]
    ticket_url: HttpUrl
    issued_at: datetime
    expires_at: datetime
    expires_seconds: int = Field(strict=True, ge=30, le=3600)

    @field_validator("transfer_reference", "gateway_ticket_id")
    @classmethod
    def identifiers_are_canonical_uuids(cls, value: str) -> str:
        try:
            canonical = str(UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Gateway identifiers must be UUIDs") from exc
        if canonical != value:
            raise ValueError("Gateway identifiers must use canonical UUID form")
        return value

    @model_validator(mode="after")
    def validity_window_matches_ttl(self) -> "DownloadGatewayTicket":
        for value in (self.issued_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Gateway timestamps require a UTC offset")
        if self.expires_at - self.issued_at != timedelta(
            seconds=self.expires_seconds
        ):
            raise ValueError("Gateway ticket TTL does not match its timestamps")
        return self


class DownloadGatewayCommittedExpired(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["v1"]
    schema_version: Literal[1]
    outcome: Literal["committed_expired"]
    registration_request_id: str = Field(min_length=36, max_length=36)
    registration_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_ticket_id: str = Field(min_length=36, max_length=36)
    download_record_id: str = Field(min_length=1, max_length=36)
    company_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    asset_id: str = Field(min_length=1, max_length=160)
    issuance_request_id: str = Field(min_length=1, max_length=160)
    transfer_reference: str = Field(min_length=36, max_length=36)
    issued_at: datetime
    expires_at: datetime

    @field_validator(
        "registration_request_id",
        "gateway_ticket_id",
        "transfer_reference",
    )
    @classmethod
    def identifiers_are_canonical_uuids(cls, value: str) -> str:
        try:
            canonical = str(UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Gateway identifiers must be UUIDs") from exc
        if canonical != value:
            raise ValueError("Gateway identifiers must use canonical UUID form")
        return value

    @model_validator(mode="after")
    def validity_window_is_complete(self) -> "DownloadGatewayCommittedExpired":
        for value in (self.issued_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Gateway timestamps require a UTC offset")
        if self.expires_at <= self.issued_at:
            raise ValueError("Gateway committed-expired window is invalid")
        return self


class DownloadGatewayClient:
    def __init__(
        self,
        *,
        registration_url: str,
        public_base_url: str,
        service_token: str,
        signing_secret: str,
        timeout_seconds: float = 10.0,
        max_ticket_ttl_seconds: int = 300,
        source_ttl_margin_seconds: int = 60,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registration_url = registration_url
        self._public_base_url = public_base_url.rstrip("/")
        self._service_token = service_token
        self._signing_secret = signing_secret.encode("utf-8")
        self._clock = clock
        if max_ticket_ttl_seconds < 30 or source_ttl_margin_seconds < 30:
            raise ValueError("Download Gateway validity limits are invalid")
        self._max_ticket_ttl_seconds = max_ticket_ttl_seconds
        self._source_ttl_margin_seconds = source_ttl_margin_seconds
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    def _canonical_uuid(value: str, label: str) -> str:
        try:
            canonical = str(UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise DownloadGatewayPermanentError(
                f"{label} must be a canonical UUID"
            ) from exc
        if canonical != value:
            raise DownloadGatewayPermanentError(
                f"{label} must be a canonical UUID"
            )
        return canonical

    def _headers(
        self,
        raw_body: bytes,
        *,
        registration_request_id: str,
        timestamp: str,
    ) -> dict[str, str]:
        signing_input = (
            _SIGNING_DOMAIN
            + b"POST\n"
            + _REGISTRATION_PATH.encode("ascii")
            + b"\n"
            + timestamp.encode("ascii")
            + b"\n"
            + registration_request_id.encode("ascii")
            + b"\n"
            + raw_body
        )
        digest = hmac.new(
            self._signing_secret,
            signing_input,
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Download-Gateway-Token": self._service_token,
            "X-Download-Gateway-Timestamp": timestamp,
            "X-Download-Gateway-Request-ID": registration_request_id,
            "X-Download-Gateway-Signature": f"sha256={digest}",
        }

    def _validate_ticket_url(self, ticket_url: str) -> None:
        if "?" in ticket_url or "#" in ticket_url:
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL contains a non-canonical delimiter"
            )
        expected = urlsplit(self._public_base_url)
        actual = urlsplit(ticket_url)
        try:
            expected_port = expected.port
            actual_port = actual.port
        except ValueError as exc:
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL is invalid"
            ) from exc
        if (
            actual.scheme != expected.scheme
            or (actual.hostname or "").casefold()
            != (expected.hostname or "").casefold()
            or actual_port != expected_port
            or actual.username is not None
            or actual.password is not None
            or actual.query
            or actual.fragment
        ):
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL is outside the configured origin"
            )
        prefix = "/downloads/"
        if not actual.path.startswith(prefix):
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL path is invalid"
            )
        token = actual.path[len(prefix) :]
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL token is invalid"
            )
        try:
            decoded = base64.urlsafe_b64decode(token + "=")
        except (binascii.Error, ValueError) as exc:
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL token is invalid"
            ) from exc
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if len(decoded) != 32 or canonical != token:
            raise DownloadGatewayPermanentError(
                "Download Gateway ticket URL token is not canonical"
            )

    @staticmethod
    def _strict_response_json(response: httpx.Response) -> object:
        if len(response.content) > 16 * 1024:
            raise ValueError("Download Gateway response body is too large")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Download Gateway response has duplicate keys")
                result[key] = value
            return result

        return json.loads(
            response.content.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )

    @staticmethod
    def build_registration_body(
        *,
        download_record_id: str,
        company_id: str,
        task_id: str,
        asset_id: str,
        expected_size_bytes: int,
        artifact_sha256: str,
        source_url: str,
        storage_binding: RelayArtifactStorageBinding,
        issuance_request_id: str,
        transfer_reference: str,
    ) -> bytes:
        payload = {
            "api_version": "v1",
            "schema_version": 1,
            "download_record_id": download_record_id,
            "company_id": company_id,
            "task_id": task_id,
            "asset_id": asset_id,
            "expected_size_bytes": expected_size_bytes,
            "artifact_sha256": artifact_sha256,
            "source_url": source_url,
            "source_expires_at": storage_binding.expires_at.astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "obs_binding": {
                "bucket": storage_binding.bucket,
                "object_key": storage_binding.object_key,
                "version_id": None,
            },
            "issuance_request_id": issuance_request_id,
            "transfer_reference": transfer_reference,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def register(
        self,
        *,
        registration_request_id: str,
        download_record_id: str,
        company_id: str,
        task_id: str,
        asset_id: str,
        expected_size_bytes: int,
        artifact_sha256: str,
        source_url: str,
        storage_binding: RelayArtifactStorageBinding,
        issuance_request_id: str,
        transfer_reference: str,
    ) -> DownloadGatewayTicket:
        registration_request_id = self._canonical_uuid(
            registration_request_id,
            "Gateway registration request id",
        )
        transfer_reference = self._canonical_uuid(
            transfer_reference,
            "Gateway transfer reference",
        )
        if expected_size_bytes <= 0:
            raise DownloadGatewayPermanentError(
                "Download Gateway requires a non-empty artifact"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
            raise DownloadGatewayPermanentError(
                "Download Gateway artifact digest is invalid"
            )
        if hashlib.sha256(source_url.encode("utf-8")).hexdigest() != (
            storage_binding.url_sha256
        ):
            raise DownloadGatewayPermanentError(
                "Download Gateway source URL does not match its binding"
            )
        raw_body = self.build_registration_body(
            download_record_id=download_record_id,
            company_id=company_id,
            task_id=task_id,
            asset_id=asset_id,
            expected_size_bytes=expected_size_bytes,
            artifact_sha256=artifact_sha256,
            source_url=source_url,
            storage_binding=storage_binding,
            issuance_request_id=issuance_request_id,
            transfer_reference=transfer_reference,
        )
        timestamp = str(int(self._clock()))
        try:
            response = self._client.post(
                self._registration_url,
                content=raw_body,
                headers=self._headers(
                    raw_body,
                    registration_request_id=registration_request_id,
                    timestamp=timestamp,
                ),
            )
        except httpx.RequestError as exc:
            raise DownloadGatewayOutcomeUnknownError(
                "Download Gateway registration request failed"
            ) from exc
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise DownloadGatewayOutcomeUnknownError(
                "Download Gateway registration is temporarily unavailable"
            )
        if response.status_code == 410:
            try:
                if response.headers.get("Location") is not None:
                    raise ValueError(
                        "Committed-expired receipt must not include Location"
                    )
                receipt = DownloadGatewayCommittedExpired.model_validate(
                    self._strict_response_json(response)
                )
                observed_at = datetime.fromtimestamp(
                    self._clock(), tz=timezone.utc
                )
                issued_at = receipt.issued_at.astimezone(timezone.utc)
                expires_at = receipt.expires_at.astimezone(timezone.utc)
                source_issued_at = storage_binding.issued_at.astimezone(
                    timezone.utc
                )
                source_expires_at = storage_binding.expires_at.astimezone(
                    timezone.utc
                )
                ttl = expires_at - issued_at
                ttl_seconds = int(ttl.total_seconds())
                if (
                    receipt.registration_request_id != registration_request_id
                    or receipt.registration_payload_sha256
                    != hashlib.sha256(raw_body).hexdigest()
                    or receipt.download_record_id != download_record_id
                    or receipt.company_id != company_id
                    or receipt.task_id != task_id
                    or receipt.asset_id != asset_id
                    or receipt.issuance_request_id != issuance_request_id
                    or receipt.transfer_reference != transfer_reference
                    or ttl != timedelta(seconds=ttl_seconds)
                    or ttl_seconds < 30
                    or ttl_seconds > self._max_ticket_ttl_seconds
                    or issued_at < source_issued_at - timedelta(seconds=30)
                    or expires_at
                    > source_expires_at
                    - timedelta(seconds=self._source_ttl_margin_seconds)
                    or expires_at > observed_at
                ):
                    raise ValueError(
                        "Committed-expired receipt does not match registration"
                    )
            except Exception as exc:
                raise DownloadGatewayOutcomeUnknownError(
                    "Download Gateway returned an unusable committed-expired acknowledgement"
                ) from exc
            raise DownloadGatewayCommittedExpiredError(
                receipt,
                acknowledgement_sha256=hashlib.sha256(response.content).hexdigest(),
            )
        if response.status_code != 201:
            raise DownloadGatewayPermanentError(
                "Download Gateway rejected the ticket registration"
            )
        # A 201 means the registration may already be committed.  Any failure
        # to parse or validate that acknowledgement is therefore an unknown
        # outcome, never a proven rejection.  The durable caller must replay
        # the same registration id and exact body against Gateway idempotency.
        try:
            response_payload = self._strict_response_json(response)
            raw_ticket_url = (
                response_payload.get("ticket_url")
                if isinstance(response_payload, dict)
                else None
            )
            if (
                not isinstance(raw_ticket_url, str)
                or response.headers.get("Location") != raw_ticket_url
            ):
                raise ValueError(
                    "Download Gateway Location does not match its ticket response"
                )
            ticket = DownloadGatewayTicket.model_validate(response_payload)
            if (
                ticket.download_record_id != download_record_id
                or ticket.company_id != company_id
                or ticket.task_id != task_id
                or ticket.asset_id != asset_id
                or ticket.issuance_request_id != issuance_request_id
                or ticket.transfer_reference != transfer_reference
            ):
                raise ValueError(
                    "Download Gateway ticket response does not match its registration"
                )
            self._validate_ticket_url(raw_ticket_url)
            observed_at = datetime.fromtimestamp(self._clock(), tz=timezone.utc)
            issued_at = ticket.issued_at.astimezone(timezone.utc)
            expires_at = ticket.expires_at.astimezone(timezone.utc)
            source_expires_at = storage_binding.expires_at.astimezone(timezone.utc)
            if (
                issued_at > observed_at + timedelta(seconds=30)
                or issued_at < observed_at - timedelta(minutes=5)
                or expires_at <= observed_at
                or ticket.expires_seconds > self._max_ticket_ttl_seconds
                or expires_at
                > source_expires_at
                - timedelta(seconds=self._source_ttl_margin_seconds)
            ):
                raise ValueError(
                    "Download Gateway ticket is outside its safe validity window"
                )
        except Exception as exc:
            raise DownloadGatewayOutcomeUnknownError(
                "Download Gateway committed but returned an unusable acknowledgement"
            ) from exc
        return ticket

    def close(self) -> None:
        self._client.close()
