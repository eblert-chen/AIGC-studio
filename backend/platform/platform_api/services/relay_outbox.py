from __future__ import annotations

import copy
from datetime import timedelta
import hashlib
import json
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
from ..relay_identity import NEW_API_RELAY_BACKEND_ID, NEW_API_RELAY_CONTRACT_REVISION
from .billing import WalletService
from .personal_billing import PersonalWalletService
from .errors import ConflictError, DomainError, NotFoundError
from .relay_status import RelayStatusService
from .execution_contracts import freeze_execution_contract, require_execution_digest
from ..execution_contract import ExecutionContract


_MATERIALIZED_DIGEST_KEY = "_platform_materialized_payload_sha256"


def _materialized_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InputAssetReferenceResolver(Protocol):
    def resolve(
        self,
        *,
        company_id: str | None,
        personal_workspace_id: str | None,
        references: list[dict[str, Any]],
        aspect_ratio: str | None,
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
    _IMAGE_OUTPUT_MODES = frozenset({"text_to_image", "image_to_image"})

    @classmethod
    def _duration_seconds(
        cls, task: GenerationTask, source: dict[str, Any]
    ) -> Any:
        if "duration_seconds" in source:
            return source["duration_seconds"]
        mode = source.get("mode", "text_to_video")
        if mode not in cls._IMAGE_OUTPUT_MODES:
            return 5
        snapshot = getattr(task, "capability_snapshot", None) or {}
        effective = snapshot.get("effective_capabilities", {})
        modes = effective.get("modes", {}) if isinstance(effective, dict) else {}
        selected = modes.get(mode, {}) if isinstance(modes, dict) else {}
        limits = selected.get("limits", {}) if isinstance(selected, dict) else {}
        durations = limits.get("duration_seconds", []) if isinstance(limits, dict) else []
        if (
            isinstance(durations, list)
            and durations
            and all(type(value) is int and value > 0 for value in durations)
        ):
            return durations[0]
        raise ConflictError("Image generation duration sentinel is unavailable")

    @staticmethod
    def from_task(
        task: GenerationTask,
        model: ModelDefinition,
        *,
        request_id: str | None = None,
        resolved_assets: list[dict[str, str]] | None = None,
        director_shot: dict[str, Any] | None = None,
        director_motion: dict[str, Any] | None = None,
        callback_url: str | None = None,
        execution_contract: ExecutionContract | None = None,
    ) -> RelayGenerationRequest:
        require_execution_digest(task, execution_contract.content_sha256() if execution_contract else None)
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
                execution_contract=execution_contract,
                client_reference_id=task.id,
                model=model.slug,
                expected_capability_revision=(
                    getattr(task, "capability_snapshot", None) or {}
                ).get("relay_capability_revision"),
                mode=source.get("mode", "text_to_video"),
                inputs={
                    "prompt": source.get("prompt"),
                    "assets": resolved_assets or [],
                    **(
                        {"director_shot": director_shot}
                        if director_shot is not None
                        else {}
                    ),
                    **(
                        {"director_motion": director_motion}
                        if director_motion is not None
                        else {}
                    ),
                },
                output={
                    "duration_seconds": RelayPayloadMapper._duration_seconds(task, source),
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
        director_shot: dict[str, Any] | None = None,
        director_motion: dict[str, Any] | None = None,
        callback_url: str | None = None,
        expected_commercial_release_snapshot: dict[str, Any] | None = None,
    ) -> RelaySubmissionOutbox:
        contract = (
            freeze_execution_contract(
                expected_snapshot=expected_commercial_release_snapshot,
                request_payload=task.request_payload,
            ) if (task.pricing_snapshot or {}).get("execution_contract_sha256") else None
        )
        payload = RelayPayloadMapper.from_task(
            task,
            model,
            request_id=request_id,
            resolved_assets=resolved_assets,
            director_shot=director_shot,
            director_motion=director_motion,
            callback_url=callback_url,
            execution_contract=contract,
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
            # This evidence is server-owned and must never be a wire field or
            # an input from customer metadata (which lives under client_metadata).
            if _MATERIALIZED_DIGEST_KEY in metadata:
                raise ConflictError("Unmaterialized Relay payload already has a digest")
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
                output = payload_data.get("output")
                if not isinstance(output, dict):
                    raise ConflictError("Relay outbox output is invalid")
                payload_data.setdefault("inputs", {})["assets"] = (
                    self.asset_reference_resolver.resolve(
                        company_id=claimed.company_id,
                        personal_workspace_id=claimed.personal_workspace_id,
                        references=references,
                        aspect_ratio=output.get("aspect_ratio"),
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
            task = session.get(GenerationTask, outbox.task_id)
            checked = RelayGenerationRequest.model_validate(materialized_data)
            require_execution_digest(task, checked.execution_contract.content_sha256()
                                     if checked.execution_contract is not None else None)
            if outbox.materialized_relay_payload is None:
                outbox.materialized_relay_payload = materialized_data
            else:
                materialized_data = copy.deepcopy(outbox.materialized_relay_payload)
            stored_payload = copy.deepcopy(outbox.relay_payload)
            stored_metadata = stored_payload.get("metadata")
            if not isinstance(stored_metadata, dict) or _MATERIALIZED_DIGEST_KEY in materialized_data.get("metadata", {}):
                raise ConflictError("Relay materialization metadata is invalid")
            try:
                digest = _materialized_payload_sha256(materialized_data)
            except (TypeError, ValueError) as exc:
                raise ConflictError("Relay materialization cannot be canonically encoded") from exc
            previous_digest = stored_metadata.get(_MATERIALIZED_DIGEST_KEY)
            if previous_digest is not None and previous_digest != digest:
                raise ConflictError("Relay materialized payload differs from its original digest")
            if previous_digest is None:
                if (outbox.relay_submit_attempted_at is not None
                        or outbox.submission_outcome_uncertain_at is not None):
                    raise ConflictError("Unknown Relay submission lacks an original payload digest")
                # Commit the first digest together with materialization, before
                # the first submit marker/HTTP. Never manufacture it in recovery.
                stored_metadata[_MATERIALIZED_DIGEST_KEY] = digest
                outbox.relay_payload = stored_payload
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
                claimed.id, claimed.attempt_count, exc.message,
                pre_submit_validation_failure=True,
            )
        except (ValidationError, TypeError):
            return self._mark_permanent_failure(
                claimed.id,
                claimed.attempt_count,
                "Relay outbox payload is invalid",
                pre_submit_validation_failure=True,
            )
        # Lightweight pre-POST safety gate: a terminal task or a task that
        # already points at a different relay_job_id means this outbox row
        # is stale — do not resubmit, let the reconciliation worker handle it.
        with self.session_factory() as session:
            task = session.get(GenerationTask, claimed.task_id)
            if task is None:
                return self._mark_permanent_failure(
                    claimed.id, claimed.attempt_count, "Task gone",
                )
            if task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUCCEEDED):
                return self._mark_reconciliation_required(
                    claimed.id, claimed.attempt_count,
                    f"Task is already {task.status.value}",
                )
            if task.relay_job_id is not None and (
                claimed.relay_job_id is None or task.relay_job_id != claimed.relay_job_id
            ):
                return self._mark_reconciliation_required(
                    claimed.id, claimed.attempt_count,
                    f"Task already bound to relay_job_id={task.relay_job_id}",
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
            # Snapshot whether a prior (possibly crashed) worker had already
            # emitted a POST. Must read BEFORE _mark_submit_attempt_started
            # refreshes the timestamp to NOW, so we can tell a crash residual
            # apart from this dispatch's fresh POST.
            with self.session_factory() as _snap_session:
                _pre_dispatch_attempted_at = _snap_session.get(
                    RelaySubmissionOutbox, claimed.id
                ).relay_submit_attempted_at
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
                confirmed_not_created=not exc.submission_outcome_unknown,
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
            # If a prior worker already POSTed (crash residual) we cannot
            # prove that Relay did not create the job — pin to reconciliation
            # instead of releasing. When the Relay error is a definitive
            # rejection of *this* dispatch's fresh POST, _pre_dispatch_attempted_at
            # will be None (we just refreshed it above) and permanent_failure
            # is correct.
            if _pre_dispatch_attempted_at is not None:
                return self._mark_reconciliation_required(
                    claimed.id,
                    claimed.attempt_count,
                    f"{exc} (prior POST may have created a Relay job)",
                    error_snapshot=_submit_error_snapshot(exc),
                )
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
        expected_execution = (payload.execution_contract.content_sha256()
                              if payload.execution_contract is not None else None)
        if accepted.execution_contract_sha256 != expected_execution:
            return self._mark_reconciliation_required(
                claimed.id, claimed.attempt_count,
                "Relay accepted a different execution contract; submission outcome is unknown",
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
        confirmed_not_created: bool = False,
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
            # submission_outcome_unknown still pins to reconciliation_required
            # on exhaustion: the Relay may have created a job before the link
            # died, and we must not release the reservation if a later retry
            # would fail with a permanent error (401/404/422) unrelated to
            # that earlier creation.
            if submission_outcome_unknown:
                outbox.submission_outcome_uncertain_at = (
                    outbox.submission_outcome_uncertain_at or utcnow()
                )
            elif (
                confirmed_not_created
                and outbox.submission_outcome_uncertain_at is None
            ):
                # This timestamp marks an unresolved POST, not immutable
                # submission history. Only an explicit non-creation reply can
                # discharge it. Never erase an earlier uncertain submission.
                outbox.relay_submit_attempted_at = None
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
        pre_submit_validation_failure: bool = False,
    ) -> DispatchResult:
        with self.session_factory.begin() as session:
            task, outbox = self._lock_task_and_outbox(
                session, outbox_id=outbox_id, include_wallet=True
            )
            if not self._owns_claim(outbox, expected_attempt):
                return self._result_for_outbox(outbox)
            # The previous worker may have crashed after POST but before
            # recording its outcome. A local validation failure is not
            # provider evidence of non-creation; keep its exact request
            # and reservation for reconciliation, without another POST.
            if (
                pre_submit_validation_failure
                and outbox.relay_submit_attempted_at is not None
            ):
                outbox.submission_outcome_uncertain_at = (
                    outbox.submission_outcome_uncertain_at or utcnow()
                )
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
