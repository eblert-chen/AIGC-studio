from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    Query,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (
    ClientAuthenticator,
    ClientPrincipal,
    GENERATION_INVOKE_SCOPE,
    SUBMISSION_RECONCILIATION_SCOPE,
    StaticClientAuthenticator,
    authentication_dependency,
    required_scope_dependency,
)
from .artifacts import (
    ArtifactNotFoundError,
    ArtifactSignatureError,
    ArtifactStore,
    ArtifactStoreError,
    FilesystemArtifactStore,
    HuaweiObsArtifactStore,
    InMemoryArtifactStore,
)
from .config import RelaySettings
from .callback import (
    AioHttpCallbackTransport,
    CallbackDispatcher,
    CallbackPolicy,
)
from .downloader import DownloadPolicy, SafeHttpsDownloader
from .errors import (
    ErrorBody,
    ErrorEnvelope,
    RelayError,
    relay_error_handler,
)
from .models import (
    CallbackDeliveryList,
    CallbackDeliveryStatus,
    DependencyHealth,
    GenerationAccepted,
    GenerationResponse,
    GenerationRequest,
    HealthResponse,
    HealthState,
    ModelListResponse,
    ModelCapabilityResponse,
    ModelResource,
    SignedDownload,
    SubmissionReconciliationList,
    SubmissionReconciliationRequest,
    WebhookReceipt,
)
from .providers.registry import build_provider_router
from .providers.pool import ProviderAccountPool
from .providers.router import ProviderRouter
from .provider_monitoring import ProviderMonitoringRepository
from .outbox import OutboxDispatcher
from .queue import InMemoryWorkQueue, RedisWorkQueue, WorkQueue
from .repository import (
    CallbackRepository,
    InMemoryJobRepository,
    JobRepository,
    OutboxRepository,
)
from .request_ids import normalize_request_id
from .service import GenerationService
from .sql_repository import SqlAlchemyJobRepository
from .transfer import ArtifactTransferService


def create_app(
    *,
    repository: JobRepository | None = None,
    queue: WorkQueue | None = None,
    transfer_queue: WorkQueue | None = None,
    router: ProviderRouter | None = None,
    authenticator: ClientAuthenticator | None = None,
    artifact_store: ArtifactStore | None = None,
    artifact_downloader=None,
    callback_transport=None,
    settings: RelaySettings | None = None,
    process_in_background: bool | None = None,
) -> FastAPI:
    settings = settings or RelaySettings.from_environment()
    settings.validate()
    if authenticator is None:
        authenticator = StaticClientAuthenticator.from_environment(
            environment=settings.environment
        )
    if repository is None:
        repository = (
            SqlAlchemyJobRepository.from_url(settings.database_url)
            if settings.runtime_mode == "production"
            else InMemoryJobRepository()
        )
    if router is None:
        router = build_provider_router(
            settings,
            account_pool=(
                repository
                if isinstance(repository, ProviderAccountPool)
                else None
            ),
        )
    if queue is None:
        queue = (
            RedisWorkQueue(settings.redis_url)
            if settings.runtime_mode == "production"
            else InMemoryWorkQueue()
        )
    if transfer_queue is None:
        transfer_queue = (
            RedisWorkQueue(
                settings.redis_url,
                stream="relay:artifact:transfer",
                group="relay-transfer-workers",
            )
            if settings.runtime_mode == "production"
            else InMemoryWorkQueue()
        )
    if artifact_store is None:
        if settings.artifact_store == "huawei_obs":
            artifact_store = HuaweiObsArtifactStore.from_environment()
        elif settings.artifact_store == "filesystem":
            assert settings.artifact_filesystem_root is not None
            assert settings.artifact_public_base_url is not None
            assert settings.artifact_signing_secret is not None
            artifact_store = FilesystemArtifactStore(
                settings.artifact_filesystem_root,
                settings.artifact_public_base_url,
                settings.artifact_signing_secret,
            )
        else:
            artifact_store = InMemoryArtifactStore()
    artifact_downloader = artifact_downloader or SafeHttpsDownloader(
        DownloadPolicy(
            max_bytes=settings.artifact_max_bytes,
            timeout_seconds=settings.artifact_timeout_seconds,
        )
    )
    authenticate_client = authentication_dependency(authenticator)
    authenticate_generation_client = required_scope_dependency(
        authenticate_client,
        GENERATION_INVOKE_SCOPE,
    )
    authenticate_submission_ops = required_scope_dependency(
        authenticate_client,
        SUBMISSION_RECONCILIATION_SCOPE,
    )
    callback_policy = CallbackPolicy(
        settings.callback_routes,
        production=settings.environment == "production",
    )
    service = GenerationService(
        repository,
        queue,
        router,
        max_worker_attempts=settings.worker_max_attempts,
        submission_claim_lease_seconds=(settings.submission_claim_lease_seconds),
        transfer_queue=transfer_queue,
        callback_policy=callback_policy,
        provider_poll_concurrency=settings.provider_poll_concurrency,
        provider_poll_claim_lease_seconds=(settings.provider_poll_claim_lease_seconds),
        provider_poll_error_base_seconds=(settings.provider_poll_error_base_seconds),
        provider_poll_error_max_seconds=(settings.provider_poll_error_max_seconds),
        provider_admission_retry_seconds=(
            settings.provider_admission_retry_seconds
        ),
    )
    transfer_service = ArtifactTransferService(
        repository,
        transfer_queue,
        artifact_downloader,
        artifact_store,
        max_attempts=settings.transfer_max_attempts,
        claim_lease_seconds=(settings.artifact_transfer_claim_lease_seconds),
    )
    dispatcher = (
        OutboxDispatcher(
            repository,
            {
                "generation.submit": queue,
                "artifact.transfer": transfer_queue,
            },
        )
        if isinstance(repository, OutboxRepository)
        else None
    )
    callback_dispatcher = (
        CallbackDispatcher(
            repository,
            callback_policy,
            transport=callback_transport
            or AioHttpCallbackTransport(
                timeout_seconds=settings.callback_timeout_seconds
            ),
            max_attempts=settings.callback_max_attempts,
            base_delay_seconds=settings.callback_base_delay_seconds,
            max_delay_seconds=settings.callback_max_delay_seconds,
        )
        if isinstance(repository, CallbackRepository)
        else None
    )
    if process_in_background is None:
        process_in_background = settings.runtime_mode == "memory"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await router.validate_configuration()
        yield
        close_operations = []
        close_queue = getattr(queue, "close", None)
        if close_queue:
            close_operations.append(close_queue())
        close_transfer_queue = getattr(transfer_queue, "close", None)
        if close_transfer_queue and transfer_queue is not queue:
            close_operations.append(close_transfer_queue())
        close_store = getattr(artifact_store, "close", None)
        if close_store:
            close_operations.append(close_store())
        dispose_repository = getattr(repository, "dispose", None)
        if dispose_repository:
            close_operations.append(dispose_repository())
        close_operations.append(router.close())
        await asyncio.gather(*close_operations, return_exceptions=True)

    api = FastAPI(
        title="AI Video Generation Relay",
        version="1.0.0",
        description=(
            "Provider-neutral asynchronous generation contract. "
            "The bundled adapter is a mock only."
        ),
        lifespan=lifespan,
    )
    api.state.generation_service = service
    api.state.outbox_dispatcher = dispatcher
    api.state.transfer_service = transfer_service
    api.state.callback_dispatcher = callback_dispatcher
    api.state.artifact_store = artifact_store
    api.state.runtime_mode = settings.runtime_mode
    api.add_exception_handler(RelayError, relay_error_handler)

    @api.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            404: "ROUTE_NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }.get(exc.status_code, "HTTP_REQUEST_REJECTED")
        message = {
            404: "The requested API route does not exist",
            405: "The HTTP method is not allowed for this route",
        }.get(exc.status_code, "The HTTP request was rejected")
        body = ErrorEnvelope(
            error=ErrorBody(
                code=code,
                message=message,
                request_id=getattr(request.state, "request_id", "unknown"),
            )
        )
        headers = getattr(exc, "headers", None) or {}
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(mode="json"),
            headers=headers,
        )

    @api.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        body = ErrorEnvelope(
            error=ErrorBody(
                code="INTERNAL_ERROR",
                message="The relay could not complete the request",
                retryable=True,
                request_id=getattr(request.state, "request_id", "unknown"),
            )
        )
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    @api.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Deliberately exclude `input` and exception context: validation may involve
        # credentials or user content that must not be echoed into responses/logs.
        issues = [
            {
                "location": [str(part) for part in issue["loc"]],
                "message": issue["msg"],
                "type": issue["type"],
            }
            for issue in exc.errors()
        ]
        body = ErrorEnvelope(
            error=ErrorBody(
                code="REQUEST_VALIDATION_FAILED",
                message="Request validation failed",
                request_id=getattr(request.state, "request_id", "unknown"),
                details={"issues": issues},
            )
        )
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @api.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = normalize_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @api.post(
        "/v1/generations",
        response_model=GenerationAccepted,
        status_code=202,
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    async def submit_generation(
        payload: GenerationRequest,
        background_tasks: BackgroundTasks,
        request: Request,
        principal: ClientPrincipal = Depends(authenticate_generation_client),
        idempotency_key: str = Header(
            min_length=8,
            max_length=128,
            description="Stable key for one logical submission",
        ),
    ) -> GenerationAccepted:
        accepted = await service.submit(
            payload,
            idempotency_key,
            principal.tenant_id,
            source_client_id=principal.client_id,
            request_id=request.state.request_id,
        )
        if process_in_background and not accepted.idempotent_replay:
            if dispatcher:
                background_tasks.add_task(dispatcher.dispatch_once)
            background_tasks.add_task(service.process_next)
            if callback_dispatcher:
                background_tasks.add_task(callback_dispatcher.dispatch_once)
        return accepted

    @api.get(
        "/v1/generations/{job_id}",
        response_model=GenerationResponse,
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    async def get_generation(
        job_id: UUID,
        principal: ClientPrincipal = Depends(authenticate_generation_client),
    ) -> GenerationResponse:
        job = await service.get(job_id, principal.tenant_id)
        return GenerationResponse.model_validate(job)

    @api.get(
        "/v1/operations/submission-reconciliations",
        response_model=SubmissionReconciliationList,
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
        },
    )
    async def list_submission_reconciliations(
        limit: int = Query(default=100, ge=1, le=500),
        principal: ClientPrincipal = Depends(authenticate_submission_ops),
    ) -> SubmissionReconciliationList:
        jobs = await service.list_submission_reconciliations(
            principal.tenant_id, limit=limit
        )
        return SubmissionReconciliationList(
            items=[GenerationResponse.model_validate(job) for job in jobs]
        )

    @api.post(
        "/v1/operations/submission-reconciliations/{job_id}",
        response_model=GenerationResponse,
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    async def resolve_submission_reconciliation(
        job_id: UUID,
        payload: SubmissionReconciliationRequest,
        background_tasks: BackgroundTasks,
        principal: ClientPrincipal = Depends(authenticate_submission_ops),
    ) -> GenerationResponse:
        job = await service.resolve_submission_reconciliation(
            job_id,
            principal.tenant_id,
            payload,
        )
        if process_in_background and callback_dispatcher:
            background_tasks.add_task(callback_dispatcher.dispatch_once)
        return GenerationResponse.model_validate(job)

    @api.get(
        "/v1/models/capabilities",
        response_model=list[ModelCapabilityResponse],
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
        },
        deprecated=True,
        description=(
            "Deprecated flat view. It emits one row per model/mode because "
            "different modes can have different limits. Use /v1/models."
        ),
    )
    async def capabilities(
        response: Response,
        _principal: ClientPrincipal = Depends(authenticate_generation_client),
    ) -> list[ModelCapabilityResponse]:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Wed, 30 Sep 2026 00:00:00 GMT"
        response.headers["Link"] = '</v1/models>; rel="successor-version"'
        response.headers["Warning"] = (
            '299 - "Deprecated capability endpoint; use /v1/models"'
        )
        return [
            ModelCapabilityResponse.model_validate(capability)
            for capability in await router.capabilities()
        ]

    @api.get(
        "/v1/models",
        response_model=ModelListResponse,
        responses={
            304: {"description": "Catalog has not changed"},
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
        },
    )
    async def model_catalog(
        request: Request,
        response: Response,
        _principal: ClientPrincipal = Depends(authenticate_generation_client),
    ):
        catalog = await router.model_catalog()
        etag = f'"{catalog.catalog_revision}"'
        cache_headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=60, must-revalidate",
            "Vary": "X-Client-ID, X-API-Key",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=cache_headers)
        for name, value in cache_headers.items():
            response.headers[name] = value
        return catalog

    @api.get(
        "/v1/models/{model_id}",
        response_model=ModelResource,
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
        },
    )
    async def model_capability(
        model_id: str,
        request: Request,
        response: Response,
        _principal: ClientPrincipal = Depends(authenticate_generation_client),
    ) -> ModelResource | Response:
        catalog = await router.model_catalog()
        model = next((item for item in catalog.data if item.id == model_id), None)
        if model is None:
            raise RelayError(
                "MODEL_NOT_FOUND",
                "Generation model does not exist",
                status_code=404,
            )
        etag = f'"{model.capability_revision}"'
        cache_headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=60, must-revalidate",
            "Vary": "X-Client-ID, X-API-Key",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=cache_headers)
        for name, value in cache_headers.items():
            response.headers[name] = value
        return model

    @api.post(
        "/v1/providers/{provider_name}/webhooks",
        response_model=WebhookReceipt,
        responses={
            401: {"model": ErrorEnvelope},
            403: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    async def provider_webhook(
        provider_name: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> WebhookReceipt:
        body = await request.body()
        receipt = await service.receive_webhook(
            provider_name,
            body,
            {key.lower(): value for key, value in request.headers.items()},
        )
        if process_in_background and receipt.status == "transferring":
            if dispatcher:
                background_tasks.add_task(dispatcher.dispatch_once)
            background_tasks.add_task(transfer_service.process_next)
        if process_in_background and callback_dispatcher:
            background_tasks.add_task(callback_dispatcher.dispatch_once)
        return receipt

    @api.get(
        "/v1/operations/callback-deliveries",
        response_model=CallbackDeliveryList,
        responses={401: {"model": ErrorEnvelope}},
    )
    async def callback_deliveries(
        status: CallbackDeliveryStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        principal: ClientPrincipal = Depends(authenticate_generation_client),
    ) -> CallbackDeliveryList:
        if not isinstance(repository, CallbackRepository):
            return CallbackDeliveryList(items=[])
        return CallbackDeliveryList(
            items=await repository.list_callback_deliveries(
                principal.tenant_id,
                status=status,
                limit=limit,
            )
        )

    @api.get(
        "/v1/generations/{job_id}/artifacts/{asset_id}/download",
        response_model=SignedDownload,
        responses={
            401: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
        },
    )
    async def signed_artifact_download(
        job_id: UUID,
        asset_id: UUID,
        principal: ClientPrincipal = Depends(authenticate_generation_client),
    ) -> SignedDownload:
        job = await service.get(job_id, principal.tenant_id)
        artifact = next(
            (output for output in job.outputs if output.asset_id == asset_id),
            None,
        )
        if artifact is None or job.status != "succeeded":
            raise RelayError(
                "ARTIFACT_NOT_FOUND",
                "Artifact does not exist",
                status_code=404,
            )
        expires = 300
        url = await artifact_store.signed_download_url(
            artifact.object_key, expires_seconds=expires
        )
        return SignedDownload(url=url, expires_seconds=expires)

    @api.get(
        FilesystemArtifactStore.download_path,
        include_in_schema=False,
        responses={
            403: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
        },
    )
    async def filesystem_artifact_download(
        key: str,
        expires: int,
        signature: str,
    ) -> Response:
        # This endpoint intentionally has no relay client authentication. Access is
        # delegated solely by the short-lived, object-bound HMAC signature.
        if not isinstance(artifact_store, FilesystemArtifactStore):
            raise RelayError(
                "ARTIFACT_NOT_FOUND",
                "Artifact does not exist",
                status_code=404,
            )
        try:
            opened = await artifact_store.open_signed(key, expires, signature)
        except ArtifactSignatureError:
            raise RelayError(
                "ARTIFACT_SIGNATURE_INVALID",
                "Artifact download signature is invalid or expired",
                status_code=403,
            ) from None
        except ArtifactNotFoundError:
            raise RelayError(
                "ARTIFACT_NOT_FOUND",
                "Artifact does not exist",
                status_code=404,
            ) from None
        except ArtifactStoreError:
            # Malformed keys and corrupt storage are deliberately not exposed.
            raise RelayError(
                "ARTIFACT_NOT_FOUND",
                "Artifact does not exist",
                status_code=404,
            ) from None

        def stream_content():
            try:
                while chunk := opened.content.read(64 * 1024):
                    yield chunk
            finally:
                opened.close()

        return StreamingResponse(
            stream_content(),
            media_type=opened.content_type,
            headers={
                "Content-Length": str(opened.size_bytes),
                "Cache-Control": "private, no-store",
                "ETag": f'"sha256-{opened.sha256}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(state=HealthState.HEALTHY)

    @api.get("/health/ready", response_model=HealthResponse)
    async def ready(response: Response) -> HealthResponse:
        provider_health = await router.health()
        repository_healthy = await repository.healthcheck()
        queue_healthy = await queue.healthcheck()
        transfer_queue_healthy = await transfer_queue.healthcheck()
        artifact_store_healthy = await artifact_store.healthcheck()
        try:
            queue_depth = await queue.depth() if queue_healthy else None
        except Exception:
            queue_healthy = False
            queue_depth = None
        dependencies = [
            DependencyHealth(
                name=f"provider:{name}",
                state=HealthState.HEALTHY if healthy else HealthState.UNAVAILABLE,
            )
            for name, healthy in provider_health.items()
        ]
        dependencies.append(
            DependencyHealth(
                name="repository",
                state=(
                    HealthState.HEALTHY
                    if repository_healthy
                    else HealthState.UNAVAILABLE
                ),
                details={
                    "kind": repository.kind,
                    "persistent": repository.persistent,
                    "outbox": repository.has_outbox,
                },
            )
        )
        monitor_degraded = False
        if settings.runtime_mode == "production":
            monitor_state = HealthState.DEGRADED
            monitor_details: dict[str, object] = {
                "enabled": settings.provider_monitor_enabled,
                "alert_sink_configured": (
                    settings.provider_alert_webhook_url is not None
                ),
            }
            if not settings.provider_monitor_enabled:
                monitor_details["reason"] = "monitor_disabled"
            elif not isinstance(repository, ProviderMonitoringRepository):
                monitor_details["reason"] = "status_unavailable"
            else:
                try:
                    monitor_status = await repository.provider_monitoring_status()
                    current = monitor_status.observed_at
                    stale_after = timedelta(
                        seconds=max(
                            settings.provider_monitor_lease_seconds,
                            settings.provider_monitor_interval_seconds * 3,
                        )
                    )
                    last_cycle = monitor_status.last_successful_cycle_at
                    cycle_age = (
                        (current - last_cycle).total_seconds()
                        if last_cycle is not None
                        else None
                    )
                    pending_age = (
                        (
                            current - monitor_status.oldest_pending_at
                        ).total_seconds()
                        if monitor_status.oldest_pending_at is not None
                        else None
                    )
                    cycle_stale = (
                        cycle_age is None
                        or cycle_age > stale_after.total_seconds()
                    )
                    pending_stale = (
                        pending_age is not None
                        and pending_age
                        > (
                            settings.provider_alert_max_delay_seconds
                            + settings.provider_monitor_interval_seconds
                        )
                    )
                    unhealthy_monitoring = (
                        cycle_stale
                        or pending_stale
                        or monitor_status.dead_letter_count > 0
                        or monitor_status.active_alert_count > 0
                        or settings.provider_alert_webhook_url is None
                    )
                    monitor_state = (
                        HealthState.DEGRADED
                        if unhealthy_monitoring
                        else HealthState.HEALTHY
                    )
                    monitor_details.update(
                        {
                            "last_successful_cycle_at": (
                                last_cycle.isoformat() if last_cycle else None
                            ),
                            "last_successful_cycle_age_seconds": cycle_age,
                            "stale_after_seconds": stale_after.total_seconds(),
                            "active_alerts": monitor_status.active_alert_count,
                            "pending_deliveries": (
                                monitor_status.pending_delivery_count
                            ),
                            "oldest_pending_age_seconds": pending_age,
                            "dead_letter_deliveries": (
                                monitor_status.dead_letter_count
                            ),
                            "oldest_dead_letter_at": (
                                monitor_status.oldest_dead_letter_at.isoformat()
                                if monitor_status.oldest_dead_letter_at
                                else None
                            ),
                        }
                    )
                except Exception:
                    monitor_state = HealthState.UNAVAILABLE
                    monitor_details["reason"] = "status_query_failed"
            monitor_degraded = monitor_state != HealthState.HEALTHY
            dependencies.append(
                DependencyHealth(
                    name="provider_monitor",
                    state=monitor_state,
                    details=monitor_details,
                )
            )
        dependencies.append(
            DependencyHealth(
                name="transfer_queue",
                state=(
                    HealthState.HEALTHY
                    if transfer_queue_healthy
                    else HealthState.UNAVAILABLE
                ),
                details={
                    "kind": transfer_queue.kind,
                    "persistent": transfer_queue.persistent,
                },
            )
        )
        dependencies.append(
            DependencyHealth(
                name="artifact_store",
                state=(
                    HealthState.UNAVAILABLE
                    if not artifact_store_healthy
                    else (
                        HealthState.HEALTHY
                        if artifact_store.persistent
                        else HealthState.DEGRADED
                    )
                ),
                details={
                    "kind": artifact_store.kind,
                    "persistent": artifact_store.persistent,
                },
            )
        )
        dependencies.append(
            DependencyHealth(
                name="runtime",
                state=(
                    HealthState.HEALTHY
                    if settings.environment == "production"
                    else HealthState.DEGRADED
                ),
                details={
                    "environment": settings.environment,
                    "mode": settings.runtime_mode,
                    "production_controls_enforced": (
                        settings.environment == "production"
                    ),
                },
            )
        )
        dependencies.append(
            DependencyHealth(
                name="queue",
                state=HealthState.HEALTHY if queue_healthy else HealthState.UNAVAILABLE,
                details={
                    "depth": queue_depth,
                    "kind": queue.kind,
                    "persistent": queue.persistent,
                },
            )
        )
        persistence_ready = (
            repository_healthy
            and queue_healthy
            and transfer_queue_healthy
            and artifact_store_healthy
            and (
                settings.runtime_mode != "production"
                or (
                    repository.persistent
                    and queue.persistent
                    and transfer_queue.persistent
                    and (
                        settings.environment != "production"
                        or artifact_store.persistent
                    )
                )
            )
        )
        # Upstream generation channels are an admission dependency, not an API
        # process dependency. Keeping the Relay behind the load balancer lets
        # accepted jobs continue receiving webhooks, polling, artifact access,
        # and reconciliation while an upstream outage is being failed over.
        if not persistence_ready:
            state = HealthState.UNAVAILABLE
        elif not any(provider_health.values()):
            state = HealthState.DEGRADED
        elif monitor_degraded:
            state = HealthState.DEGRADED
        elif (
            settings.runtime_mode == "production"
            and settings.environment == "development"
        ):
            state = HealthState.DEGRADED
        else:
            state = HealthState.HEALTHY
        if state == HealthState.UNAVAILABLE:
            response.status_code = 503
        return HealthResponse(state=state, dependencies=dependencies)

    def relay_openapi() -> dict:
        if api.openapi_schema is not None:
            return api.openapi_schema
        schema = get_openapi(
            title=api.title,
            version=api.version,
            description=api.description,
            routes=api.routes,
        )
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                security = operation.get("security")
                if not isinstance(security, list):
                    continue
                scheme_names = {
                    name
                    for requirement in security
                    if isinstance(requirement, dict)
                    for name in requirement
                }
                if {"RelayClientId", "RelayApiKey"}.issubset(scheme_names):
                    operation["security"] = [
                        {"RelayClientId": [], "RelayApiKey": []}
                    ]
        # Literal defaults ensure every server-built resource emits these
        # values. Mark them required in the wire schema as well so generated
        # clients cannot treat contract identity as optional.
        for component in (
            schema.get("components", {}).get("schemas", {}).values()
        ):
            if not isinstance(component, dict):
                continue
            properties = component.get("properties")
            if not isinstance(properties, dict):
                continue
            required = component.setdefault("required", [])
            for field_name in ("api_version", "schema_version"):
                if field_name in properties and field_name not in required:
                    required.append(field_name)
        api.openapi_schema = schema
        return schema

    api.openapi = relay_openapi
    return api


app = create_app()
