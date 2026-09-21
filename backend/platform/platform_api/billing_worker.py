from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import logging
from pathlib import Path
import signal
from threading import Event
from typing import Callable, Sequence
from urllib.parse import urlsplit

import httpx

from .billing_worker_health import write_health


logger = logging.getLogger("platform.billing_worker")

# This worker has no database or PSP credential. It may only invoke these
# server-owned, idempotent queue entry points; no caller-provided path is sent.
BILLING_JOB_PATHS = {
    "inbox": "/internal/billing/payment-webhook-inbox/run-once",
    "payments": "/internal/billing/payment-provider-commands/run-once",
    "auto-recharge": "/internal/billing/auto-recharge/run-once",
    "enterprise": "/internal/billing/enterprise/run-once",
    "reservation-recovery": "/internal/billing/reservation-recovery/run-once",
}
DEFAULT_JOBS = (
    "inbox",
    "payments",
    "auto-recharge",
    "enterprise",
    "reservation-recovery",
)


class BillingWorkerConfigurationError(ValueError):
    pass


class BillingWorkerBoundaryError(RuntimeError):
    """Authentication or contract failures stop the worker instead of retrying."""


class BillingWorkerTemporaryError(RuntimeError):
    pass


def validate_platform_url(value: str, *, allow_loopback_http: bool = False) -> str:
    if not isinstance(value, str) or not value or any(
        character.isspace() or ord(character) < 33 or character == "\\"
        for character in value
    ):
        raise BillingWorkerConfigurationError("Platform URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise BillingWorkerConfigurationError("Platform URL is invalid") from None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise BillingWorkerConfigurationError(
            "Platform URL must be an origin without credentials, path or query"
        )
    loopback = parsed.hostname.lower() == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and allow_loopback_http and loopback
    ):
        raise BillingWorkerConfigurationError(
            "Platform URL requires HTTPS; loopback HTTP is an explicit local-only option"
        )
    return value.rstrip("/")


def read_service_token(path: Path) -> str:
    """Read a rotateable file secret without exposing it to logs or arguments."""

    if not path.is_absolute() or path.is_symlink():
        raise BillingWorkerConfigurationError(
            "Service token must be an absolute regular-file path"
        )
    try:
        if not path.is_file() or not 32 <= path.stat().st_size <= 4096:
            raise BillingWorkerConfigurationError("Service token file is invalid")
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise BillingWorkerConfigurationError("Service token file is unreadable") from None
    if not 32 <= len(token) <= 4096 or any(character.isspace() for character in token):
        raise BillingWorkerConfigurationError("Service token file is invalid")
    if any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise BillingWorkerConfigurationError("Service token file is invalid")
    return token


@dataclass(frozen=True)
class BillingCycleResult:
    processed_jobs: tuple[str, ...]
    temporary_failures: tuple[str, ...]


class BillingWorker:
    def __init__(
        self,
        *,
        platform_url: str,
        token_loader: Callable[[], str],
        jobs: Sequence[str] = DEFAULT_JOBS,
        allow_loopback_http: bool = False,
        timeout_seconds: float = 45,
        client: httpx.Client | None = None,
    ) -> None:
        self.platform_url = validate_platform_url(
            platform_url, allow_loopback_http=allow_loopback_http
        )
        if not jobs or len(set(jobs)) != len(jobs) or any(
            job not in BILLING_JOB_PATHS for job in jobs
        ):
            raise BillingWorkerConfigurationError("Billing worker jobs are invalid")
        if not 1 <= timeout_seconds <= 60:
            raise BillingWorkerConfigurationError("Billing worker timeout is invalid")
        self.jobs = tuple(jobs)
        self.token_loader = token_loader
        self._owned_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=10),
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def run_job_once(self, job: str) -> bool:
        if job not in self.jobs:
            raise BillingWorkerConfigurationError("Billing worker job is not enabled")
        token = self.token_loader()
        if not isinstance(token, str) or not 32 <= len(token) <= 4096 or any(
            ord(character) < 33 or ord(character) > 126 for character in token
        ):
            raise BillingWorkerConfigurationError("Service token is invalid")
        try:
            response = self.client.post(
                self.platform_url + BILLING_JOB_PATHS[job],
                headers={"X-Internal-Service-Token": token},
                json={},
                follow_redirects=False,
            )
        except httpx.TransportError:
            # Never interpolate a transport error: it may contain endpoint or
            # authorization details. A timed-out run is safe to retry only
            # because the Platform owns the durable claim and lease fencing.
            raise BillingWorkerTemporaryError("Platform request unavailable") from None
        if response.status_code == 429 or response.status_code >= 500:
            raise BillingWorkerTemporaryError("Platform billing queue unavailable")
        if response.status_code != 200:
            raise BillingWorkerBoundaryError(
                f"Platform billing queue rejected the worker ({response.status_code})"
            )
        if len(response.content) > 64 * 1024:
            raise BillingWorkerBoundaryError("Platform billing response is too large")
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise BillingWorkerBoundaryError("Platform billing response is invalid") from None
        if not isinstance(payload, dict) or type(payload.get("processed")) is not bool:
            raise BillingWorkerBoundaryError("Platform billing response is invalid")
        return payload["processed"]

    def run_once(self) -> BillingCycleResult:
        processed: list[str] = []
        failures: list[str] = []
        for job in self.jobs:
            try:
                if self.run_job_once(job):
                    processed.append(job)
            except BillingWorkerTemporaryError:
                # One PSP outage must not prevent a verified inbox event or
                # enterprise collection scan from being processed.
                failures.append(job)
        return BillingCycleResult(tuple(processed), tuple(failures))


def run_loop(
    worker: BillingWorker,
    *,
    stop_event: Event,
    interval_seconds: float = 2,
    retry_cap_seconds: float = 60,
    once: bool = False,
    health_file: Path | None = None,
) -> None:
    if not 1 <= interval_seconds <= retry_cap_seconds <= 60:
        raise BillingWorkerConfigurationError("Billing worker retry interval is invalid")
    failures = 0
    while not stop_event.is_set():
        try:
            result = worker.run_once()
        except Exception:
            if health_file is not None:
                write_health(health_file, healthy=False, failed_jobs=("boundary",))
            raise
        if health_file is not None:
            write_health(health_file, healthy=not result.temporary_failures,
                         failed_jobs=result.temporary_failures)
        if result.temporary_failures:
            failures += 1
            logger.warning(
                "billing queues temporarily unavailable: %s",
                ",".join(result.temporary_failures),
            )
            if once:
                raise BillingWorkerTemporaryError("Billing worker iteration is incomplete")
        else:
            failures = 0
            if once:
                return
        delay = min(retry_cap_seconds, interval_seconds * (2 ** min(failures, 10)))
        stop_event.wait(delay)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Dispatch durable Platform billing queues without PSP credentials"
    )
    parser.add_argument("--platform-url", required=True)
    parser.add_argument("--service-token-file", required=True, type=Path)
    parser.add_argument("--jobs", nargs="+", choices=tuple(BILLING_JOB_PATHS), default=DEFAULT_JOBS)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=2)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health-file", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    # Validate the file before opening a client, then reread on each request to
    # support controlled token rotation without putting the secret in argv.
    read_service_token(args.service_token_file)
    worker = BillingWorker(
        platform_url=args.platform_url,
        token_loader=lambda: read_service_token(args.service_token_file),
        jobs=args.jobs,
        allow_loopback_http=args.allow_loopback_http,
    )
    stop_event = Event()

    def stop(*_) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_loop(
            worker,
            stop_event=stop_event,
            interval_seconds=args.interval_seconds,
            once=args.once,
            health_file=args.health_file,
        )
    finally:
        worker.close()


if __name__ == "__main__":
    main()
