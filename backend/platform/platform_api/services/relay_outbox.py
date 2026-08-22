from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..models import (
    GenerationTask,
    ModelDefinition,
    RelayOutboxStatus,
    RelaySubmissionOutbox,
    TaskStatus,
    utcnow,
)
from ..relay_backends import (
    RelayBackendRegistry,
    RelayBackendResolutionError,
    coerce_relay_backend_registry,
)
from ..relay_client import (
    RelayAccepted,
    RelayClient,
    RelayGenerationRequest,
    RelayIdempotencyConflictError,
    RelayPermanentError,
    RelayTemporaryError,
)
from ..request_ids import normalize_request_id, stable_request_id
from .billing import WalletService
from .personal_billing import PersonalWalletService
from .errors import ConflictError, DomainError, NotFoundError
from .relay_status import RelayStatusService


class InputAssetReferenceResolver(Protocol):
    def resolve(
        self, *, company_id: str, references: list[dict[str, Any]]
    ) -> list[dict[str, str]]: ...


class _DispatchClaimLost(RuntimeError):
    """The dispatcher no longer owns this outbox attempt."""


def _submit_error_snapshot(
    error: RelayPermanentError | RelayTemporaryError,
) -> dict[str, Any] | None:
    snapshot = error.diagnostic_snapshot()
    if snapshot is None:
        return None
    return {**snapshot, "source": "submit"}


class RelayPayloadMapper:
    @staticmethod
    def from_task(
        task: GenerationTask,
        model: ModelDefinition,
        *,
        request_id: str | None = None,
        resolved_assets: list[dict[str, str]] | None = None,
        callback_url: str | None = None,
    ) -> RelayGenerationRequest:
        source = task.request_payload
        asset_references = source.get("assets", [])
        if asset_references and resolved_assets is None:
            raise ConflictError("Private input assets must be resolved by the platform")
        client_metadata = source.get("metadata", {})
        if not isinstance(client_metadata, dict):
            raise ConflictError("Task client metadata is invalid")
        scope_metadata = {
            "platform_billing_scope": (
                "company" if task.company_id is not None else "personal"
            ),
            "platform_billing_scope_id": (
                task.company_id or task.personal_workspace_id
            ),
        }
        if task.company_id is not None:
            scope_metadata["platform_company_id"] = task.company_id
        else:
            scope_metadata["platform_personal_workspace_id"] = (
                task.personal_workspace_id
            )
        try:
            return RelayGenerationRequest(
                client_reference_id=task.id,
                model=model.slug,
                expected_capability_revision=(
                    getattr(task, "capability_snapshot", None) or {}
                ).get("relay_capability_revision"),
                mode=source.get("mode", "text_to_video"),
                inputs={
                    "prompt": source.get("prompt"),
                    "assets": resolved_assets or [],
                },
                output={
                    "duration_seconds": source.get("duration_seconds", 5),
                    "aspect_ratio": source.get("aspect_ratio", "16:9"),
                    "resolution": source.get("resolution", "720p"),
                    "count": source.get("output_count", 1),
                    "face_enabled": source.get("face_enabled", False),
                },
                metadata={
                    # Customer metadata is correlation data, not a provider
                    # control surface. Namespacing prevents provider adapters
                    # from consuming undeclared generation options.
                    "client_metadata": client_metadata,
                    **scope_metadata,
                    "platform_user_id": task.user_id,
                    "platform_task_id": task.id,
                    "platform_request_id": normalize_request_id(
                        request_id or stable_request_id("platform-task", task.id)
                    ),
                    "_platform_input_assets": asset_references,
                },
                callback={"url": callback_url} if callback_url else None,
            )
        except (ValidationError, TypeError) as exc:
            raise ConflictError("生成参数无法映射到中转站请求契约") from exc


class RelayOutboxService:
    @staticmethod
    def enqueue(
        session: Session,
        *,
        task: GenerationTask,
        model: ModelDefinition,
        request_id: str | None = None,
        resolved_assets: list[dict[str, str]] | None = None,
        callback_url: str | None = None,
    ) -> RelaySubmissionOutbox:
        payload = RelayPayloadMapper.from_task(
            task,
            model,
            request_id=request_id,
            resolved_assets=resolved_assets,
            callback_url=callback_url,
        )
        outbox = RelaySubmissionOutbox(
            company_id=task.company_id,
            personal_workspace_id=task.personal_workspace_id,
            task_id=task.id,
            idempotency_key=f"platform-task-{task.id}",
            relay_backend_id=task.relay_backend_id,
            relay_contract_revision=task.relay_contract_revision,
            relay_payload=payload.model_dump(mode="json"),
        )
        session.add(outbox)
        session.flush()
        return outbox


class DispatchResult:
    def __init__(
        self,
        *,
        processed: bool,
        outbox_id: str | None = None,
        status: str | None = None,
        relay_job_id: str | None = None,
    ):
        self.processed = processed
        self.outbox_id = outbox_id
        self.status = status
        self.relay_job_id = relay_job_id


class RelayOutboxDispatcher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        relay_client: RelayClient | RelayBackendRegistry,
        *,
        stale_after_seconds: int = 300,
        max_attempts: int = 12,
        asset_reference_resolver: InputAssetReferenceResolver | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.session_factory = session_factory
        self.relay_backends = coerce_relay_backend_registry(relay_client)
        self.stale_after_seconds = stale_after_seconds
        self.max_attempts = max_attempts
        self.asset_reference_resolver = asset_reference_resolver

    def _materialize_payload(
        self, claimed: RelaySubmissionOutbox
    ) -> RelayGenerationRequest:
        materialized_data = copy.deepcopy(claimed.materialized_relay_payload)
        if materialized_data is None:
            payload_data = copy.deepcopy(claimed.relay_payload)
            metadata = payload_data.get("metadata")
            if not isinstance(metadata, dict):
                raise ConflictError("Relay outbox metadata is invalid")
            references = metadata.pop("_platform_input_assets", [])
            if references:
                if not isinstance(references, list):
                    raise ConflictError("Relay input asset references are invalid")
                if self.asset_reference_resolver is None:
                    raise DomainError(
                        "Input asset resolver is not configured",
                        "input_asset_resolver_unavailable",
                        503,
                    )
                payload_data.setdefault("inputs", {})["assets"] = (
                    self.asset_reference_resolver.resolve(
                        company_id=claimed.company_id,
                        references=references,
                    )
                )
            materialized = RelayGenerationRequest.model_validate(payload_data)
            materialized_data = materialized.model_dump(mode="json")
        # Signed input URLs are volatile. Persist the first exact request before
        # any Relay POST so every retry has an identical idempotency hash.
        with self.session_factory.begin() as session:
            outbox = session.scalar(
                select(RelaySubmissionOutbox)
                .where(RelaySubmissionOutbox.id == claimed.id)
                .with_for_update()
            )
            if outbox is None:
                raise NotFoundError("派发记录不存在")
            if not self._owns_claim(outbox, claimed.attempt_count):
                raise _DispatchClaimLost()
            task_affinity = session.execute(
                select(
                    GenerationTask.relay_backend_id,
                    GenerationTask.relay_contract_revision,
                ).where(GenerationTask.id == outbox.task_id)
            ).one_or_none()
            if task_affinity is None:
                raise NotFoundError("Relay outbox task does not exist")
            if task_affinity != (
                outbox.relay_backend_id,
                outbox.relay_contract_revision,
            ):
                raise ConflictError("Relay task and outbox affinities do not match")
            if outbox.materialized_relay_payload is None:
                outbox.materialized_relay_payload = materialized_data
            else:
                materialized_data = copy.deepcopy(outbox.materialized_relay_payload)
        return RelayGenerationRequest.model_validate(materialized_data)

    def _mark_submit_attempt_started(
        self, outbox_id: str, expected_attempt: int
    ) -> bool:
        with self.session_factory.begin() as session:
            outbox = session.scalar(
                select(RelaySubmissionOutbox)
                .where(RelaySubmissionOutbox.id == outbox_id)
                .with_for_update()
            )
            if outbox is None:
                raise NotFoundError("派发记录不存在")
            if not self._owns_claim(outbox, expected_attempt):
                return False
            if outbox.relay_submit_attempted_at is None:
                outbox.relay_submit_attempted_at = utcnow()
            return True

    @staticmethod
    def _owns_claim(outbox: RelaySubmissionOutbox, expected_attempt: int) -> bool:
        return (
            outbox.status == RelayOutboxStatus.PROCESSING
            and outbox.attempt_count == expected_attempt
        )

    @staticmethod
    def _lock_task_and_outbox(
        session: Session,
        *,
        outbox_id: str,
        include_wallet: bool,
    ) -> tuple[GenerationTask, RelaySubmissionOutbox]:
        identity = session.execute(
            select(
                RelaySubmissionOutbox.company_id,
                RelaySubmissionOutbox.personal_workspace_id,
                RelaySubmissionOutbox.task_id,
            ).where(RelaySubmissionOutbox.id == outbox_id)
        ).one_or_none()
        if identity is None:
            raise NotFoundError("派发记录不存在")

        if include_wallet:
            task = RelayStatusService.lock_wallet_and_task_for_update(
                session, company_id=identity.company_id, task_id=identity.task_id
            ) if identity.company_id is not None else (
                RelayStatusService.lock_wallet_and_task_for_scope(
                    session,
                    company_id=None,
                    personal_workspace_id=identity.personal_workspace_id,
                    task_id=identity.task_id,
                )
            )
        else:
            task = RelayStatusService.lock_task_for_scope(
                session,
                company_id=identity.company_id,
                personal_workspace_id=identity.personal_workspace_id,
                task_id=identity.task_id,
            )
        outbox = session.scalar(
            select(RelaySubmissionOutbox)
            .where(RelaySubmissionOutbox.id == outbox_id)
            .with_for_update()
        )
        if outbox is None:
            raise NotFoundError("派发记录不存在")
        if (
            outbox.task_id != task.id
            or outbox.company_id != task.company_id
            or outbox.personal_workspace_id != task.personal_workspace_id
        ):
            raise ConflictError("派发记录与任务归属不一致")
        return task, outbox

    @staticmethod
    def _result_for_outbox(outbox: RelaySubmissionOutbox) -> DispatchResult:
        return DispatchResult(
            processed=True,
            outbox_id=outbox.id,
            status=outbox.status.value,
            relay_job_id=outbox.relay_job_id,
        )

    def _current_result(self, outbox_id: str) -> DispatchResult:
        with self.session_factory() as session:
            outbox = session.get(RelaySubmissionOutbox, outbox_id)
            if outbox is None:
                raise NotFoundError("Relay outbox record does not exist")
            return self._result_for_outbox(outbox)

    def _claim(self) -> RelaySubmissionOutbox | None:
        now = utcnow()
        stale_before = now - timedelta(seconds=self.stale_after_seconds)
        with self.session_factory.begin() as session:
            outbox = session.scalar(
                select(RelaySubmissionOutbox)
                .where(
                    or_(
                        (
                            RelaySubmissionOutbox.status.in_(
                                [
                                    RelayOutboxStatus.PENDING,
                                    RelayOutboxStatus.RETRY,
                                ]
                            )
                            & (RelaySubmissionOutbox.next_attempt_at <= now)
                        ),
                        (RelaySubmissionOutbox.status == RelayOutboxStatus.PROCESSING)
                        & (RelaySubmissionOutbox.updated_at <= stale_before),
                    )
                )
                .order_by(RelaySubmissionOutbox.created_at)
                .with_for_update(skip_locked=True)
            )
            if outbox is None:
                return None
            outbox.status = RelayOutboxStatus.PROCESSING
            outbox.attempt_count += 1
            session.flush()
            session.expunge(outbox)
            return outbox

    def dispatch_once(self) -> DispatchResult:
        claimed = self._claim()
        if claimed is None:
            return DispatchResult(processed=False)
        if claimed.attempt_count > self.max_attempts:
            return self._mark_attempt_limit(
                claimed.id,
                claimed.attempt_count,
                f"Relay dispatch attempt limit ({self.max_attempts}) exhausted",
            )
        try:
            payload = self._materialize_payload(claimed)
        except _DispatchClaimLost:
            return self._current_result(claimed.id)
        except DomainError as exc:
            if exc.status_code >= 500:
                return self._mark_retry(claimed.id, claimed.attempt_count, exc.message)
            return self._mark_permanent_failure(
                claimed.id, claimed.attempt_count, exc.message
            )
        except (ValidationError, TypeError):
            return self._mark_permanent_failure(
                claimed.id,
                claimed.attempt_count,
                "Relay outbox payload is invalid",
            )
        try:
            client = self.relay_backends.resolve(
                backend_id=claimed.relay_backend_id,
                contract_revision=claimed.relay_contract_revision,
            )
        except RelayBackendResolutionError as exc:
            return self._mark_retry(
                claimed.id,
                claimed.attempt_count,
                str(exc),
                submission_outcome_unknown=False,
            )
        try:
            if not self._mark_submit_attempt_started(claimed.id, claimed.attempt_count):
                return self._current_result(claimed.id)
            accepted = client.submit(
                payload,
                idempotency_key=claimed.idempotency_key,
                request_id=payload.metadata.get("platform_request_id"),
            )
        except RelayTemporaryError as exc:
            return self._mark_retry(
                claimed.id,
                claimed.attempt_count,
                str(exc),
                submission_outcome_unknown=exc.submission_outcome_unknown,
                error_snapshot=_submit_error_snapshot(exc),
            )
        except RelayIdempotencyConflictError as exc:
            return self._mark_reconciliation_required(
                claimed.id,
                claimed.attempt_count,
                str(exc),
                error_snapshot=_submit_error_snapshot(exc),
            )
        except RelayPermanentError as exc:
            return self._mark_permanent_failure(
                claimed.id,
                claimed.attempt_count,
                str(exc),
                error_snapshot=_submit_error_snapshot(exc),
            )
        except Exception as exc:
            return self._mark_retry(
                claimed.id,
                claimed.attempt_count,
                f"Relay client raised {type(exc).__name__}",
                submission_outcome_unknown=True,
            )
        if accepted.expected_capability_revision != payload.expected_capability_revision:
            return self._mark_reconciliation_required(
                claimed.id,
                claimed.attempt_count,
                "Relay accepted a different capability revision",
            )
        return self._mark_sent(claimed.id, claimed.attempt_count, accepted)

    def _mark_sent(
        self,
        outbox_id: str,
        expected_attempt: int,
        accepted: RelayAccepted,
    ) -> DispatchResult:
        with self.session_factory.begin() as session:
            task, outbox = self._lock_task_and_outbox(
                session, outbox_id=outbox_id, include_wallet=False
            )
            if not self._owns_claim(outbox, expected_attempt):
                return self._result_for_outbox(outbox)
            if (
                task.relay_backend_id != outbox.relay_backend_id
                or task.relay_contract_revision != outbox.relay_contract_revision
            ):
                raise ConflictError("Relay task and outbox affinities do not match")
            if task.relay_job_id is not None and task.relay_job_id != accepted.job_id:
                raise ConflictError("重复派发返回了不同的中转站任务 ID")
            outbox.status = RelayOutboxStatus.SENT
            outbox.relay_job_id = accepted.job_id
            outbox.last_error = None
            task.relay_job_id = accepted.job_id
            if accepted.status in {
                "submitting",
                "processing",
                "reconciliation_required",
                "transferring",
            }:
                task.status = TaskStatus.PROCESSING
            return DispatchResult(
                processed=True,
                outbox_id=outbox.id,
                status=outbox.status.value,
                relay_job_id=accepted.job_id,
            )

    def _mark_retry(
        self,
        outbox_id: str,
        expected_attempt: int,
        error: str,
        *,
        submission_outcome_unknown: bool = False,
        error_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        with self.session_factory.begin() as session:
            task = None
            if expected_attempt >= self.max_attempts:
                task, outbox = self._lock_task_and_outbox(
                    session, outbox_id=outbox_id, include_wallet=True
                )
            else:
                outbox = session.scalar(
                    select(RelaySubmissionOutbox)
                    .where(RelaySubmissionOutbox.id == outbox_id)
                    .with_for_update()
                )
                if outbox is None:
                    raise NotFoundError("派发记录不存在")
            if not self._owns_claim(outbox, expected_attempt):
                return self._result_for_outbox(outbox)
            if submission_outcome_unknown:
                outbox.submission_outcome_uncertain_at = (
                    outbox.submission_outcome_uncertain_at or utcnow()
                )
            if outbox.attempt_count >= self.max_attempts:
                exhausted_error = (
                    f"Relay dispatch attempt limit ({self.max_attempts}) "
                    f"exhausted: {error}"
                )
                assert task is not None
                if outbox.submission_outcome_uncertain_at is not None:
                    return self._apply_reconciliation_required(
                        session,
                        task,
                        outbox,
                        exhausted_error,
                        error_snapshot=error_snapshot,
                    )
                return self._apply_permanent_failure(
                    session,
                    task,
                    outbox,
                    exhausted_error,
                    error_snapshot=error_snapshot,
                )
            delay_seconds = min(300, 2 ** min(outbox.attempt_count, 8))
            outbox.status = RelayOutboxStatus.RETRY
            outbox.next_attempt_at = utcnow() + timedelta(seconds=delay_seconds)
            outbox.last_error = error[:2000]
            return DispatchResult(
                processed=True, outbox_id=outbox.id, status=outbox.status.value
            )

    def _mark_permanent_failure(
        self,
        outbox_id: str,
        expected_attempt: int,
        error: str,
        *,
        error_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        with self.session_factory.begin() as session:
            task, outbox = self._lock_task_and_outbox(
                session, outbox_id=outbox_id, include_wallet=True
            )
            if not self._owns_claim(outbox, expected_attempt):
                return self._result_for_outbox(outbox)
            if outbox.submission_outcome_uncertain_at is not None:
                return self._apply_reconciliation_required(
                    session,
                    task,
                    outbox,
                    error,
                    error_snapshot=error_snapshot,
                )
            return self._apply_permanent_failure(
                session,
                task,
                outbox,
                error,
                error_snapshot=error_snapshot,
            )

    def _mark_attempt_limit(
        self, outbox_id: str, expected_attempt: int, error: str
    ) -> DispatchResult:
        with self.session_factory.begin() as session:
            task, outbox = self._lock_task_and_outbox(
                session, outbox_id=outbox_id, include_wallet=True
            )
            if not self._owns_claim(outbox, expected_attempt):
                return self._result_for_outbox(outbox)
            if (
                outbox.submission_outcome_uncertain_at is not None
                or outbox.relay_submit_attempted_at is not None
            ):
                return self._apply_reconciliation_required(session, task, outbox, error)
            return self._apply_permanent_failure(session, task, outbox, error)

    def _mark_reconciliation_required(
        self,
        outbox_id: str,
        expected_attempt: int,
        error: str,
        *,
        error_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        with self.session_factory.begin() as session:
            task, outbox = self._lock_task_and_outbox(
                session, outbox_id=outbox_id, include_wallet=False
            )
            if not self._owns_claim(outbox, expected_attempt):
                return self._result_for_outbox(outbox)
            outbox.submission_outcome_uncertain_at = (
                outbox.submission_outcome_uncertain_at or utcnow()
            )
            return self._apply_reconciliation_required(
                session,
                task,
                outbox,
                error,
                error_snapshot=error_snapshot,
            )

    @staticmethod
    def _apply_reconciliation_required(
        session: Session,
        task: GenerationTask,
        outbox: RelaySubmissionOutbox,
        error: str,
        *,
        error_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        if task.status not in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            task.status = TaskStatus.PROCESSING
        if error_snapshot is not None:
            task.relay_error_snapshot = error_snapshot
        outbox.status = RelayOutboxStatus.RECONCILIATION_REQUIRED
        outbox.last_error = error[:2000]
        return DispatchResult(
            processed=True,
            outbox_id=outbox.id,
            status=outbox.status.value,
            relay_job_id=outbox.relay_job_id,
        )

    @staticmethod
    def _apply_permanent_failure(
        session: Session,
        task: GenerationTask,
        outbox: RelaySubmissionOutbox,
        error: str,
        *,
        error_snapshot: dict[str, Any] | None = None,
    ) -> DispatchResult:
        task.relay_error_snapshot = error_snapshot
        if outbox.company_id is not None:
            WalletService.release_failure(
                session,
                company_id=outbox.company_id,
                task_id=outbox.task_id,
                idempotency_key=f"relay-submit-failed:{outbox.id}",
                failure_reason=error,
            )
        else:
            if outbox.personal_workspace_id is None:
                raise ConflictError("Relay outbox billing scope is invalid")
            PersonalWalletService.release_failure(
                session,
                workspace_id=outbox.personal_workspace_id,
                task_id=outbox.task_id,
                idempotency_key=f"relay-submit-failed:{outbox.id}",
                failure_reason=error,
            )
        outbox.status = RelayOutboxStatus.PERMANENTLY_FAILED
        outbox.last_error = error[:2000]
        return DispatchResult(
            processed=True, outbox_id=outbox.id, status=outbox.status.value
        )
