from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ..download_gateway import (
    DownloadGatewayClient,
    DownloadGatewayCommittedExpired,
    DownloadGatewayCommittedExpiredError,
    DownloadGatewayPermanentError,
    DownloadGatewayTemporaryError,
    DownloadGatewayTicket,
)
from ..models import (
    DownloadGatewayRegistrationAttempt,
    DownloadGatewayRegistrationStatus,
    DownloadRecord,
)
from ..relay_client import RelayArtifactStorageBinding
from ..request_ids import normalize_request_id
from .errors import ConflictError, NotFoundError
from .reports import DownloadRecordService


_REQUEST_PURPOSE = "registration-request"
_RESPONSE_PURPOSE = "registration-response"


class DownloadGatewayAttemptCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Download Gateway attempt key must contain 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded_key: str) -> "DownloadGatewayAttemptCipher":
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except Exception as exc:
            raise ValueError("Download Gateway attempt key is invalid") from exc
        return cls(key)

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return self._cipher.encrypt(nonce, plaintext, aad), nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes, *, aad: bytes) -> bytes:
        try:
            return self._cipher.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise DownloadGatewayPermanentError(
                "Download Gateway attempt ciphertext authentication failed"
            ) from exc


@dataclass(frozen=True, slots=True)
class DownloadGatewayAttemptClaim:
    attempt_id: str
    lease_token: str
    action: Literal["submit", "attach", "cleanup"]


@dataclass(frozen=True, slots=True)
class DownloadGatewayAttemptResult:
    processed: bool
    attempt_id: str | None = None
    status: str | None = None
    download_record_id: str | None = None
    ticket: DownloadGatewayTicket | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("Database clock did not return a timestamp")
    return _utc(value)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class DownloadGatewayRegistrationService:
    """Durably registers one exact Gateway request and attaches its audit row."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway_client: DownloadGatewayClient,
        cipher: DownloadGatewayAttemptCipher,
        *,
        lease_owner: str,
        lease_seconds: int = 30,
        max_attempts: int = 8,
        retry_base_seconds: int = 1,
        retry_cap_seconds: int = 60,
        gateway_ticket_ttl_seconds: int = 300,
        source_ttl_margin_seconds: int = 60,
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("Download Gateway attempt lease owner is required")
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Download Gateway attempt limits are invalid")
        if retry_base_seconds < 1 or retry_cap_seconds < retry_base_seconds:
            raise ValueError("Download Gateway retry limits are invalid")
        if gateway_ticket_ttl_seconds < 30 or source_ttl_margin_seconds < 30:
            raise ValueError("Download Gateway source validity limits are invalid")
        self.session_factory = session_factory
        self.gateway_client = gateway_client
        self.cipher = cipher
        self.lease_owner = lease_owner[:120]
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_cap_seconds = retry_cap_seconds
        self.gateway_ticket_ttl_seconds = gateway_ticket_ttl_seconds
        self.source_ttl_margin_seconds = source_ttl_margin_seconds
        self.required_source_ttl_seconds = (
            gateway_ticket_ttl_seconds + source_ttl_margin_seconds
        )

    @staticmethod
    def _aad_values(
        *,
        purpose: str,
        attempt_id: str,
        registration_request_id: str,
        download_record_id: str,
        company_id: str,
        task_id: str,
        asset_id: str,
    ) -> bytes:
        return (
            "download-gateway-registration-attempt.v1\n"
            f"{purpose}\n{attempt_id}\n{registration_request_id}\n"
            f"{download_record_id}\n{company_id}\n{task_id}\n{asset_id}"
        ).encode("utf-8")

    @classmethod
    def _aad(cls, attempt: DownloadGatewayRegistrationAttempt, purpose: str) -> bytes:
        return cls._aad_values(
            purpose=purpose,
            attempt_id=attempt.id,
            registration_request_id=attempt.registration_request_id,
            download_record_id=attempt.download_record_id,
            company_id=attempt.company_id,
            task_id=attempt.task_id,
            asset_id=attempt.asset_id,
        )

    @staticmethod
    def _validate_uuid(value: str, label: str) -> None:
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (TypeError, ValueError):
            raise ConflictError(f"{label} is invalid") from None

    @staticmethod
    def _validate_scope(
        attempt: DownloadGatewayRegistrationAttempt,
        *,
        company_id: str,
        task_id: str,
        asset_id: str,
        requested_by_user_id: str,
        platform_request_id: str,
    ) -> None:
        if (
            attempt.company_id != company_id
            or attempt.task_id != task_id
            or attempt.asset_id != asset_id
            or attempt.requested_by_user_id != requested_by_user_id
            or attempt.platform_request_id != platform_request_id
        ):
            raise ConflictError(
                "Download request id is already bound to another tenant or artifact"
            )

    def find_attempt(
        self,
        *,
        company_id: str,
        task_id: str,
        asset_id: str,
        requested_by_user_id: str,
        platform_request_id: str,
    ) -> str | None:
        with self.session_factory() as session:
            attempt = session.scalar(
                select(DownloadGatewayRegistrationAttempt).where(
                    DownloadGatewayRegistrationAttempt.company_id == company_id,
                    DownloadGatewayRegistrationAttempt.requested_by_user_id
                    == requested_by_user_id,
                    DownloadGatewayRegistrationAttempt.platform_request_id
                    == platform_request_id,
                )
            )
            if attempt is None:
                return None
            self._validate_scope(
                attempt,
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                requested_by_user_id=requested_by_user_id,
                platform_request_id=platform_request_id,
            )
            return attempt.id

    def prepare(
        self,
        *,
        company_id: str,
        task_id: str,
        asset_id: str,
        requested_by_user_id: str,
        platform_request_id: str,
        expected_size_bytes: int,
        artifact_sha256: str,
        source_url: str,
        storage_binding: RelayArtifactStorageBinding,
    ) -> str:
        if normalize_request_id(platform_request_id) != platform_request_id:
            raise ConflictError("Download request id is not normalized")
        if expected_size_bytes <= 0:
            raise ConflictError("Download artifact must be non-empty")
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
            raise ConflictError("Download artifact digest is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", storage_binding.url_sha256):
            raise ConflictError("Relay source URL digest is invalid")
        if hashlib.sha256(source_url.encode("utf-8")).hexdigest() != (
            storage_binding.url_sha256
        ):
            raise ConflictError("Relay source URL does not match its binding")
        attempt_id = str(uuid4())
        registration_request_id = str(uuid4())
        download_record_id = str(uuid4())
        transfer_reference = str(uuid4())
        for value, label in (
            (attempt_id, "Download Gateway attempt id"),
            (registration_request_id, "Download Gateway registration id"),
            (download_record_id, "Download record id"),
            (transfer_reference, "Download Gateway transfer reference"),
        ):
            self._validate_uuid(value, label)
        raw_body = self.gateway_client.build_registration_body(
            download_record_id=download_record_id,
            company_id=company_id,
            task_id=task_id,
            asset_id=asset_id,
            expected_size_bytes=expected_size_bytes,
            artifact_sha256=artifact_sha256,
            source_url=source_url,
            storage_binding=storage_binding,
            issuance_request_id=platform_request_id,
            transfer_reference=transfer_reference,
        )
        aad = self._aad_values(
            purpose=_REQUEST_PURPOSE,
            attempt_id=attempt_id,
            registration_request_id=registration_request_id,
            download_record_id=download_record_id,
            company_id=company_id,
            task_id=task_id,
            asset_id=asset_id,
        )
        ciphertext, nonce = self.cipher.encrypt(raw_body, aad=aad)
        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        try:
            with self.session_factory.begin() as session:
                now = _database_now(session)
                if _utc(storage_binding.expires_at) < now + timedelta(
                    seconds=self.required_source_ttl_seconds
                ):
                    raise ConflictError(
                        "Relay artifact URL validity is too short for a Gateway ticket"
                    )
                session.add(
                    DownloadGatewayRegistrationAttempt(
                        id=attempt_id,
                        company_id=company_id,
                        task_id=task_id,
                        asset_id=asset_id,
                        requested_by_user_id=requested_by_user_id,
                        platform_request_id=platform_request_id,
                        registration_request_id=registration_request_id,
                        download_record_id=download_record_id,
                        transfer_reference=transfer_reference,
                        expected_size_bytes=expected_size_bytes,
                        artifact_sha256=artifact_sha256,
                        storage_provider=storage_binding.provider,
                        storage_endpoint_host=storage_binding.endpoint_host,
                        storage_bucket=storage_binding.bucket,
                        storage_object_key=storage_binding.object_key,
                        source_url_sha256=storage_binding.url_sha256,
                        relay_issued_at=storage_binding.issued_at,
                        relay_expires_at=storage_binding.expires_at,
                        body_sha256=body_sha256,
                        request_ciphertext=ciphertext,
                        request_nonce=nonce,
                        status=DownloadGatewayRegistrationStatus.PENDING,
                        attempt_count=0,
                        next_attempt_at=now,
                        ticket_replay_count=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.flush()
            return attempt_id
        except IntegrityError:
            existing_id = self.find_attempt(
                company_id=company_id,
                task_id=task_id,
                asset_id=asset_id,
                requested_by_user_id=requested_by_user_id,
                platform_request_id=platform_request_id,
            )
            if existing_id is None:
                raise
            return existing_id

    @staticmethod
    def _clear_lease(attempt: DownloadGatewayRegistrationAttempt) -> None:
        attempt.lease_owner = None
        attempt.lease_token = None
        attempt.lease_expires_at = None

    @staticmethod
    def _clear_request_ciphertext(
        attempt: DownloadGatewayRegistrationAttempt,
    ) -> None:
        attempt.request_ciphertext = None
        attempt.request_nonce = None

    @staticmethod
    def _clear_response_ciphertext(
        attempt: DownloadGatewayRegistrationAttempt,
    ) -> None:
        attempt.response_ciphertext = None
        attempt.response_nonce = None

    def _dead_locked(
        self,
        attempt: DownloadGatewayRegistrationAttempt,
        *,
        now: datetime,
        error_code: str,
    ) -> DownloadGatewayAttemptResult:
        attempt.status = DownloadGatewayRegistrationStatus.DEAD
        attempt.last_error_code = error_code[:120]
        attempt.dead_at = now
        attempt.next_attempt_at = None
        self._clear_lease(attempt)
        self._clear_request_ciphertext(attempt)
        self._clear_response_ciphertext(attempt)
        return self._result(attempt)

    def _unknown_locked(
        self,
        attempt: DownloadGatewayRegistrationAttempt,
        *,
        now: datetime,
        error_code: str,
    ) -> DownloadGatewayAttemptResult:
        """Stop automatic retries without destroying reconciliation evidence."""

        attempt.status = DownloadGatewayRegistrationStatus.UNKNOWN
        attempt.last_error_code = error_code[:120]
        attempt.next_attempt_at = None
        attempt.updated_at = now
        self._clear_lease(attempt)
        return self._result(attempt)

    @staticmethod
    def _result(
        attempt: DownloadGatewayRegistrationAttempt,
        *,
        ticket: DownloadGatewayTicket | None = None,
    ) -> DownloadGatewayAttemptResult:
        status = (
            attempt.status.value
            if hasattr(attempt.status, "value")
            else str(attempt.status)
        )
        return DownloadGatewayAttemptResult(
            processed=True,
            attempt_id=attempt.id,
            status=status,
            download_record_id=(
                attempt.download_record_id
                if attempt.status == DownloadGatewayRegistrationStatus.ATTACHED
                else None
            ),
            ticket=ticket,
        )

    def _mark_reconciled_expired(
        self,
        claim: DownloadGatewayAttemptClaim,
        *,
        receipt: DownloadGatewayCommittedExpired,
        acknowledgement_sha256: str,
    ) -> DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if attempt is None:
                return DownloadGatewayAttemptResult(processed=False)
            if (
                receipt.registration_request_id
                != attempt.registration_request_id
                or receipt.registration_payload_sha256 != attempt.body_sha256
                or receipt.download_record_id != attempt.download_record_id
                or receipt.company_id != attempt.company_id
                or receipt.task_id != attempt.task_id
                or receipt.asset_id != attempt.asset_id
                or receipt.issuance_request_id != attempt.platform_request_id
                or receipt.transfer_reference != attempt.transfer_reference
                or not re.fullmatch(r"[0-9a-f]{64}", acknowledgement_sha256)
            ):
                return self._dead_locked(
                    attempt,
                    now=now,
                    error_code="gateway_committed_expired_receipt_conflict",
                )
            ttl = receipt.expires_at - receipt.issued_at
            ttl_seconds = int(ttl.total_seconds())
            if (
                ttl != timedelta(seconds=ttl_seconds)
                or ttl_seconds < 30
                or ttl_seconds > self.gateway_ticket_ttl_seconds
                or _utc(receipt.issued_at)
                < _utc(attempt.relay_issued_at) - timedelta(seconds=30)
                or _utc(receipt.expires_at)
                > _utc(attempt.relay_expires_at)
                - timedelta(seconds=self.source_ttl_margin_seconds)
            ):
                return self._dead_locked(
                    attempt,
                    now=now,
                    error_code="gateway_committed_expired_window_invalid",
                )
            attempt.status = (
                DownloadGatewayRegistrationStatus.RECONCILED_EXPIRED
            )
            attempt.gateway_ticket_id = receipt.gateway_ticket_id
            attempt.gateway_issued_at = receipt.issued_at
            attempt.gateway_expires_at = receipt.expires_at
            attempt.gateway_expires_seconds = ttl_seconds
            attempt.reconciliation_ack_sha256 = acknowledgement_sha256
            attempt.reconciled_at = now
            attempt.next_attempt_at = None
            attempt.last_error_code = "gateway_registration_committed_expired"
            attempt.updated_at = now
            self._clear_request_ciphertext(attempt)
            self._clear_response_ciphertext(attempt)
            self._clear_lease(attempt)
            return self._result(attempt)

    def _claim_locked(
        self,
        attempt: DownloadGatewayRegistrationAttempt,
        *,
        now: datetime,
        manual: bool,
    ) -> DownloadGatewayAttemptClaim | DownloadGatewayAttemptResult:
        if attempt.status in {
            DownloadGatewayRegistrationStatus.DEAD,
            DownloadGatewayRegistrationStatus.RECONCILED_EXPIRED,
        }:
            return self._result(attempt)
        if attempt.status == DownloadGatewayRegistrationStatus.ATTACHED:
            if (
                attempt.response_ciphertext is not None
                and attempt.response_destroy_after is not None
                and _utc(attempt.response_destroy_after) <= now
            ):
                action: Literal["submit", "attach", "cleanup"] = "cleanup"
            else:
                return self._result(attempt)
        elif attempt.status == DownloadGatewayRegistrationStatus.REGISTERED:
            action = "attach"
        else:
            action = "submit"
            if attempt.status == DownloadGatewayRegistrationStatus.UNKNOWN:
                if not manual:
                    return self._result(attempt)
            if (
                attempt.status == DownloadGatewayRegistrationStatus.PROCESSING
                and attempt.lease_expires_at is not None
                and _utc(attempt.lease_expires_at) > now
            ):
                raise DownloadGatewayTemporaryError(
                    "Download Gateway registration is owned by another worker"
                )
            if (
                not manual
                and attempt.status == DownloadGatewayRegistrationStatus.RETRY
                and attempt.next_attempt_at is not None
                and _utc(attempt.next_attempt_at) > now
            ):
                raise DownloadGatewayTemporaryError(
                    "Download Gateway registration retry is not due"
                )
            if (
                attempt.status != DownloadGatewayRegistrationStatus.UNKNOWN
                and attempt.attempt_count >= self.max_attempts
            ):
                if manual:
                    attempt.status = DownloadGatewayRegistrationStatus.UNKNOWN
                else:
                    return self._unknown_locked(
                        attempt,
                        now=now,
                        error_code="gateway_registration_outcome_unknown",
                    )
            if _utc(attempt.relay_expires_at) <= now:
                if attempt.attempt_count > 0:
                    if not manual:
                        return self._unknown_locked(
                            attempt,
                            now=now,
                            error_code="gateway_registration_outcome_unknown",
                        )
                else:
                    return self._dead_locked(
                        attempt,
                        now=now,
                        error_code="relay_source_url_expired_before_submission",
                    )
            if attempt.request_ciphertext is None or attempt.request_nonce is None:
                return self._dead_locked(
                    attempt,
                    now=now,
                    error_code="gateway_registration_request_missing",
                )
            if attempt.attempt_count >= 9223372036854775807:
                return self._unknown_locked(
                    attempt,
                    now=now,
                    error_code="gateway_registration_attempt_counter_exhausted",
                )
            attempt.status = DownloadGatewayRegistrationStatus.PROCESSING
            attempt.attempt_count += 1
        if (
            attempt.lease_token is not None
            and attempt.lease_expires_at is not None
            and _utc(attempt.lease_expires_at) > now
        ):
            raise DownloadGatewayTemporaryError(
                "Download Gateway attempt has an active lease"
            )
        lease_token = str(uuid4())
        attempt.lease_owner = self.lease_owner
        attempt.lease_token = lease_token
        attempt.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        attempt.updated_at = now
        return DownloadGatewayAttemptClaim(
            attempt_id=attempt.id,
            lease_token=lease_token,
            action=action,
        )

    def _claim_specific(
        self,
        attempt_id: str,
        *,
        manual: bool,
    ) -> DownloadGatewayAttemptClaim | DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = session.scalar(
                select(DownloadGatewayRegistrationAttempt)
                .where(DownloadGatewayRegistrationAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise NotFoundError("Download Gateway registration attempt does not exist")
            return self._claim_locked(attempt, now=now, manual=manual)

    def _claim_next(self) -> DownloadGatewayAttemptClaim | None:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = session.scalar(
                select(DownloadGatewayRegistrationAttempt)
                .where(
                    or_(
                        and_(
                            DownloadGatewayRegistrationAttempt.status.in_(
                                [
                                    DownloadGatewayRegistrationStatus.PENDING,
                                    DownloadGatewayRegistrationStatus.RETRY,
                                ]
                            ),
                            or_(
                                DownloadGatewayRegistrationAttempt.next_attempt_at.is_(
                                    None
                                ),
                                DownloadGatewayRegistrationAttempt.next_attempt_at <= now,
                            ),
                        ),
                        and_(
                            DownloadGatewayRegistrationAttempt.status
                            == DownloadGatewayRegistrationStatus.PROCESSING,
                            DownloadGatewayRegistrationAttempt.lease_expires_at <= now,
                        ),
                        and_(
                            DownloadGatewayRegistrationAttempt.status
                            == DownloadGatewayRegistrationStatus.REGISTERED,
                            or_(
                                DownloadGatewayRegistrationAttempt.lease_expires_at.is_(
                                    None
                                ),
                                DownloadGatewayRegistrationAttempt.lease_expires_at <= now,
                            ),
                        ),
                        and_(
                            DownloadGatewayRegistrationAttempt.status
                            == DownloadGatewayRegistrationStatus.ATTACHED,
                            DownloadGatewayRegistrationAttempt.response_ciphertext.is_not(
                                None
                            ),
                            DownloadGatewayRegistrationAttempt.response_destroy_after
                            <= now,
                        ),
                    )
                )
                .order_by(
                    DownloadGatewayRegistrationAttempt.next_attempt_at,
                    DownloadGatewayRegistrationAttempt.created_at,
                )
                .with_for_update(skip_locked=True)
            )
            if attempt is None:
                return None
            claimed = self._claim_locked(attempt, now=now, manual=False)
            if isinstance(claimed, DownloadGatewayAttemptResult):
                return None
            return claimed

    def _load_claimed(
        self,
        session: Session,
        claim: DownloadGatewayAttemptClaim,
        *,
        now: datetime,
    ) -> DownloadGatewayRegistrationAttempt | None:
        # Fencing is checked again in every transaction that persists the
        # result of leased work.  A token alone is insufficient: an expired
        # worker must not write even when no successor has reclaimed the row.
        return session.scalar(
            select(DownloadGatewayRegistrationAttempt)
            .where(
                DownloadGatewayRegistrationAttempt.id == claim.attempt_id,
                DownloadGatewayRegistrationAttempt.lease_token == claim.lease_token,
                DownloadGatewayRegistrationAttempt.lease_expires_at.is_not(None),
                DownloadGatewayRegistrationAttempt.lease_expires_at > now,
            )
            .with_for_update()
        )

    def _mark_retry(
        self,
        claim: DownloadGatewayAttemptClaim,
        *,
        error_code: str,
    ) -> DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if attempt is None:
                return DownloadGatewayAttemptResult(processed=False)
            delay = min(
                self.retry_cap_seconds,
                self.retry_base_seconds * (2 ** min(attempt.attempt_count - 1, 20)),
            )
            if attempt.attempt_count >= self.max_attempts:
                return self._unknown_locked(
                    attempt,
                    now=now,
                    error_code=error_code,
                )
            attempt.status = DownloadGatewayRegistrationStatus.RETRY
            attempt.next_attempt_at = now + timedelta(seconds=delay)
            attempt.last_error_code = error_code[:120]
            attempt.updated_at = now
            self._clear_lease(attempt)
            return self._result(attempt)

    def _mark_dead(
        self,
        claim: DownloadGatewayAttemptClaim,
        *,
        error_code: str,
    ) -> DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if attempt is None:
                return DownloadGatewayAttemptResult(processed=False)
            return self._dead_locked(attempt, now=now, error_code=error_code)

    def _submit(
        self,
        claim: DownloadGatewayAttemptClaim,
    ) -> tuple[DownloadGatewayAttemptResult, DownloadGatewayTicket | None]:
        invalid_result: DownloadGatewayAttemptResult | None = None
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if (
                attempt is None
                or attempt.request_ciphertext is None
                or attempt.request_nonce is None
            ):
                return DownloadGatewayAttemptResult(processed=False), None
            try:
                raw_body = self.cipher.decrypt(
                    bytes(attempt.request_ciphertext),
                    bytes(attempt.request_nonce),
                    aad=self._aad(attempt, _REQUEST_PURPOSE),
                )
            except DownloadGatewayPermanentError:
                invalid_result = self._dead_locked(
                    attempt,
                    now=now,
                    error_code="gateway_registration_ciphertext_invalid",
                )
            else:
                if hashlib.sha256(raw_body).hexdigest() != attempt.body_sha256:
                    invalid_result = self._dead_locked(
                        attempt,
                        now=now,
                        error_code="gateway_registration_body_digest_mismatch",
                    )
                else:
                    try:
                        payload = json.loads(raw_body)
                        if not isinstance(payload, dict):
                            raise TypeError
                        source_url = payload["source_url"]
                        if not isinstance(source_url, str):
                            raise TypeError
                        binding = RelayArtifactStorageBinding(
                            provider=attempt.storage_provider,
                            endpoint_host=attempt.storage_endpoint_host,
                            bucket=attempt.storage_bucket,
                            object_key=attempt.storage_object_key,
                            issued_at=_utc(attempt.relay_issued_at),
                            expires_at=_utc(attempt.relay_expires_at),
                            url_sha256=attempt.source_url_sha256,
                        )
                    except Exception:
                        invalid_result = self._dead_locked(
                            attempt,
                            now=now,
                            error_code="gateway_registration_body_invalid",
                        )
                    else:
                        rebuilt = self.gateway_client.build_registration_body(
                            download_record_id=attempt.download_record_id,
                            company_id=attempt.company_id,
                            task_id=attempt.task_id,
                            asset_id=attempt.asset_id,
                            expected_size_bytes=attempt.expected_size_bytes,
                            artifact_sha256=attempt.artifact_sha256,
                            source_url=source_url,
                            storage_binding=binding,
                            issuance_request_id=attempt.platform_request_id,
                            transfer_reference=attempt.transfer_reference,
                        )
                        if rebuilt != raw_body:
                            invalid_result = self._dead_locked(
                                attempt,
                                now=now,
                                error_code="gateway_registration_body_not_canonical",
                            )
                        else:
                            registration_request_id = (
                                attempt.registration_request_id
                            )
                            expected = {
                                "download_record_id": attempt.download_record_id,
                                "company_id": attempt.company_id,
                                "task_id": attempt.task_id,
                                "asset_id": attempt.asset_id,
                                "expected_size_bytes": attempt.expected_size_bytes,
                                "artifact_sha256": attempt.artifact_sha256,
                                "issuance_request_id": attempt.platform_request_id,
                                "transfer_reference": attempt.transfer_reference,
                            }
        if invalid_result is not None:
            return invalid_result, None
        try:
            ticket = self.gateway_client.register(
                registration_request_id=registration_request_id,
                source_url=source_url,
                storage_binding=binding,
                **expected,
            )
        except DownloadGatewayCommittedExpiredError as exc:
            result = self._mark_reconciled_expired(
                claim,
                receipt=exc.receipt,
                acknowledgement_sha256=exc.acknowledgement_sha256,
            )
            return result, None
        except DownloadGatewayTemporaryError:
            result = self._mark_retry(
                claim,
                error_code="gateway_registration_outcome_unknown",
            )
            return result, None
        except DownloadGatewayPermanentError:
            result = self._mark_dead(
                claim,
                error_code="gateway_registration_rejected",
            )
            return result, None

        raw_response = _canonical_json_bytes(ticket.model_dump(mode="json"))
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if attempt is None:
                return DownloadGatewayAttemptResult(processed=False), None
            response_ciphertext, response_nonce = self.cipher.encrypt(
                raw_response,
                aad=self._aad(attempt, _RESPONSE_PURPOSE),
            )
            attempt.status = DownloadGatewayRegistrationStatus.REGISTERED
            attempt.response_sha256 = hashlib.sha256(raw_response).hexdigest()
            attempt.response_ciphertext = response_ciphertext
            attempt.response_nonce = response_nonce
            attempt.gateway_ticket_id = ticket.gateway_ticket_id
            attempt.gateway_ticket_url_sha256 = hashlib.sha256(
                str(ticket.ticket_url).encode("utf-8")
            ).hexdigest()
            attempt.gateway_issued_at = ticket.issued_at
            attempt.gateway_expires_at = ticket.expires_at
            attempt.gateway_expires_seconds = ticket.expires_seconds
            attempt.registered_at = now
            attempt.next_attempt_at = None
            attempt.last_error_code = None
            attempt.updated_at = now
            self._clear_request_ciphertext(attempt)
            result = self._result(attempt, ticket=ticket)
        return result, ticket

    @staticmethod
    def _record_matches_attempt(
        record: DownloadRecord,
        attempt: DownloadGatewayRegistrationAttempt,
    ) -> bool:
        return (
            record.id == attempt.download_record_id
            and record.company_id == attempt.company_id
            and record.task_id == attempt.task_id
            and record.asset_id == attempt.asset_id
            and record.requested_by_user_id == attempt.requested_by_user_id
            and record.request_id == attempt.platform_request_id
            and record.gateway_registration_request_id
            == attempt.registration_request_id
            and record.gateway_ticket_id == attempt.gateway_ticket_id
            and record.gateway_ticket_url_sha256
            == attempt.gateway_ticket_url_sha256
            and record.gateway_issued_at is not None
            and attempt.gateway_issued_at is not None
            and _utc(record.gateway_issued_at)
            == _utc(attempt.gateway_issued_at)
            and record.gateway_expires_at is not None
            and attempt.gateway_expires_at is not None
            and _utc(record.gateway_expires_at)
            == _utc(attempt.gateway_expires_at)
            and record.gateway_transfer_reference == attempt.transfer_reference
            and record.expires_seconds == attempt.gateway_expires_seconds
            and _utc(record.expires_at) == _utc(attempt.gateway_expires_at)
            and record.storage_binding_version == 1
            and record.storage_provider == attempt.storage_provider
            and record.storage_endpoint_host == attempt.storage_endpoint_host
            and record.storage_bucket == attempt.storage_bucket
            and record.storage_object_key == attempt.storage_object_key
            and record.storage_version_id is None
            and record.source_url_sha256 == attempt.source_url_sha256
            and record.relay_issued_at is not None
            and _utc(record.relay_issued_at) == _utc(attempt.relay_issued_at)
            and record.relay_expires_at is not None
            and _utc(record.relay_expires_at) == _utc(attempt.relay_expires_at)
        )

    def _mark_attached_locked(
        self,
        attempt: DownloadGatewayRegistrationAttempt,
        *,
        now: datetime,
    ) -> DownloadGatewayAttemptResult:
        attempt.status = DownloadGatewayRegistrationStatus.ATTACHED
        attempt.attached_at = now
        attempt.response_destroy_after = attempt.gateway_expires_at
        attempt.updated_at = now
        self._clear_lease(attempt)
        return self._result(attempt)

    def _resolve_attach_integrity_conflict(
        self,
        claim: DownloadGatewayAttemptClaim,
    ) -> DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if attempt is None:
                return DownloadGatewayAttemptResult(processed=False)
            if attempt.status != DownloadGatewayRegistrationStatus.REGISTERED:
                return self._result(attempt)
            if attempt.gateway_expires_at is None:
                return self._dead_locked(
                    attempt,
                    now=now,
                    error_code="gateway_ticket_metadata_missing",
                )
            if _utc(attempt.gateway_expires_at) <= now:
                return self._dead_locked(
                    attempt,
                    now=now,
                    error_code="registered_ticket_expired_before_attach",
                )
            conflicts = session.scalars(
                select(DownloadRecord)
                .where(
                    or_(
                        DownloadRecord.id == attempt.download_record_id,
                        DownloadRecord.gateway_registration_request_id
                        == attempt.registration_request_id,
                        DownloadRecord.gateway_ticket_id
                        == attempt.gateway_ticket_id,
                        DownloadRecord.gateway_transfer_reference
                        == attempt.transfer_reference,
                    )
                )
                .with_for_update()
            ).all()
            if conflicts and all(
                self._record_matches_attempt(record, attempt)
                for record in conflicts
            ):
                return self._mark_attached_locked(attempt, now=now)
            return self._dead_locked(
                attempt,
                now=now,
                error_code="gateway_download_record_conflict",
            )

    def _attach(
        self,
        claim: DownloadGatewayAttemptClaim,
    ) -> DownloadGatewayAttemptResult:
        try:
            with self.session_factory.begin() as session:
                now = _database_now(session)
                attempt = self._load_claimed(session, claim, now=now)
                if attempt is None:
                    return DownloadGatewayAttemptResult(processed=False)
                if attempt.status != DownloadGatewayRegistrationStatus.REGISTERED:
                    return self._result(attempt)
                if attempt.gateway_expires_at is None:
                    return self._dead_locked(
                        attempt,
                        now=now,
                        error_code="gateway_ticket_metadata_missing",
                    )
                if _utc(attempt.gateway_expires_at) <= now:
                    return self._dead_locked(
                        attempt,
                        now=now,
                        error_code="registered_ticket_expired_before_attach",
                    )
                record = session.get(DownloadRecord, attempt.download_record_id)
                if record is None:
                    DownloadRecordService.append_registered_gateway_attempt(
                        session,
                        attempt=attempt,
                    )
                elif not self._record_matches_attempt(record, attempt):
                    return self._dead_locked(
                        attempt,
                        now=now,
                        error_code="gateway_download_record_conflict",
                    )
                return self._mark_attached_locked(attempt, now=now)
        except IntegrityError:
            try:
                return self._resolve_attach_integrity_conflict(claim)
            except SQLAlchemyError as exc:
                raise DownloadGatewayTemporaryError(
                    "Platform could not resolve a Gateway attachment conflict"
                ) from exc
        except SQLAlchemyError as exc:
            raise DownloadGatewayTemporaryError(
                "Platform could not attach the registered Gateway ticket"
            ) from exc

    def _cleanup(
        self,
        claim: DownloadGatewayAttemptClaim,
    ) -> DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = self._load_claimed(session, claim, now=now)
            if attempt is None:
                return DownloadGatewayAttemptResult(processed=False)
            if attempt.status != DownloadGatewayRegistrationStatus.ATTACHED:
                return self._result(attempt)
            if (
                attempt.response_destroy_after is None
                or _utc(attempt.response_destroy_after) > now
            ):
                self._clear_lease(attempt)
                return self._result(attempt)
            self._clear_response_ciphertext(attempt)
            self._clear_lease(attempt)
            attempt.updated_at = now
            return self._result(attempt)

    def _replay_ticket(self, attempt_id: str) -> DownloadGatewayAttemptResult:
        with self.session_factory.begin() as session:
            now = _database_now(session)
            attempt = session.scalar(
                select(DownloadGatewayRegistrationAttempt)
                .where(DownloadGatewayRegistrationAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise NotFoundError("Download Gateway registration attempt does not exist")
            if attempt.status != DownloadGatewayRegistrationStatus.ATTACHED:
                return self._result(attempt)
            if (
                attempt.response_ciphertext is None
                or attempt.response_nonce is None
                or attempt.response_sha256 is None
                or attempt.gateway_ticket_url_sha256 is None
                or attempt.gateway_issued_at is None
                or attempt.gateway_expires_at is None
                or attempt.gateway_expires_seconds is None
            ):
                raise DownloadGatewayPermanentError(
                    "Download Gateway ticket replay is no longer available"
                )
            raw_response = self.cipher.decrypt(
                bytes(attempt.response_ciphertext),
                bytes(attempt.response_nonce),
                aad=self._aad(attempt, _RESPONSE_PURPOSE),
            )
            if hashlib.sha256(raw_response).hexdigest() != attempt.response_sha256:
                raise DownloadGatewayPermanentError(
                    "Download Gateway ticket response digest is invalid"
                )
            try:
                ticket = DownloadGatewayTicket.model_validate(json.loads(raw_response))
            except Exception as exc:
                raise DownloadGatewayPermanentError(
                    "Download Gateway ticket response is invalid"
                ) from exc
            if (
                ticket.download_record_id != attempt.download_record_id
                or ticket.company_id != attempt.company_id
                or ticket.task_id != attempt.task_id
                or ticket.asset_id != attempt.asset_id
                or ticket.issuance_request_id != attempt.platform_request_id
                or ticket.transfer_reference != attempt.transfer_reference
                or ticket.gateway_ticket_id != attempt.gateway_ticket_id
                or hashlib.sha256(
                    str(ticket.ticket_url).encode("utf-8")
                ).hexdigest()
                != attempt.gateway_ticket_url_sha256
                or _utc(ticket.issued_at) != _utc(attempt.gateway_issued_at)
                or _utc(ticket.expires_at) != _utc(attempt.gateway_expires_at)
                or ticket.expires_seconds != attempt.gateway_expires_seconds
                or _utc(ticket.expires_at) <= now
            ):
                raise DownloadGatewayPermanentError(
                    "Download Gateway ticket response conflicts with its attempt"
                )
            self.gateway_client._validate_ticket_url(str(ticket.ticket_url))
            if attempt.ticket_replay_count >= 9223372036854775807:
                raise DownloadGatewayPermanentError(
                    "Download Gateway ticket replay counter is exhausted"
                )
            attempt.ticket_replay_count += 1
            attempt.ticket_replayed_at = now
            # Replaying the same tenant-bound, one-time Gateway capability
            # does not expand authority. Keep it recoverable until its own
            # expiry so repeated lost HTTP acknowledgements remain idempotent.
            attempt.response_destroy_after = attempt.gateway_expires_at
            attempt.updated_at = now
            return self._result(attempt, ticket=ticket)

    def process_attempt(
        self,
        attempt_id: str,
        *,
        return_ticket: bool,
        manual: bool = False,
    ) -> DownloadGatewayAttemptResult:
        claim_or_result = self._claim_specific(attempt_id, manual=manual)
        if isinstance(claim_or_result, DownloadGatewayAttemptResult):
            if (
                return_ticket
                and claim_or_result.status
                == DownloadGatewayRegistrationStatus.ATTACHED.value
            ):
                return self._replay_ticket(attempt_id)
            return claim_or_result
        claim = claim_or_result
        if claim.action == "cleanup":
            return self._cleanup(claim)
        if claim.action == "submit":
            result, _ = self._submit(claim)
            if result.status != DownloadGatewayRegistrationStatus.REGISTERED.value:
                if result.status == DownloadGatewayRegistrationStatus.RETRY.value:
                    raise DownloadGatewayTemporaryError(
                        "Download Gateway registration outcome is unknown"
                    )
                if result.status == DownloadGatewayRegistrationStatus.DEAD.value:
                    raise DownloadGatewayPermanentError(
                        "Download Gateway registration is dead-lettered"
                    )
                return result
        attached = self._attach(claim)
        if (
            attached.status == DownloadGatewayRegistrationStatus.ATTACHED.value
            and return_ticket
        ):
            return self._replay_ticket(attempt_id)
        return attached

    def process_existing(
        self,
        *,
        company_id: str,
        task_id: str,
        asset_id: str,
        requested_by_user_id: str,
        platform_request_id: str,
    ) -> DownloadGatewayAttemptResult | None:
        attempt_id = self.find_attempt(
            company_id=company_id,
            task_id=task_id,
            asset_id=asset_id,
            requested_by_user_id=requested_by_user_id,
            platform_request_id=platform_request_id,
        )
        if attempt_id is None:
            return None
        return self.process_attempt(attempt_id, return_ticket=True, manual=False)

    def run_once(self) -> DownloadGatewayAttemptResult:
        claim = self._claim_next()
        if claim is None:
            return DownloadGatewayAttemptResult(processed=False)
        if claim.action == "cleanup":
            return self._cleanup(claim)
        if claim.action == "submit":
            result, _ = self._submit(claim)
            if result.status != DownloadGatewayRegistrationStatus.REGISTERED.value:
                return result
        return self._attach(claim)

    def reconcile(self, attempt_id: str) -> DownloadGatewayAttemptResult:
        return self.process_attempt(
            attempt_id,
            return_ticket=False,
            manual=True,
        )
