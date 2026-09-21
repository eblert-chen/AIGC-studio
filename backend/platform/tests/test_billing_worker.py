from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from platform_api.billing_worker import (
    BILLING_JOB_PATHS,
    BillingCycleResult,
    BillingWorker,
    BillingWorkerBoundaryError,
    BillingWorkerConfigurationError,
    BillingWorkerTemporaryError,
    read_service_token,
    run_loop,
    validate_platform_url,
)


TOKEN = "test-billing-worker-token-" + "x" * 32


@pytest.mark.parametrize(
    "value",
    [
        "http://platform.example.test",
        "https://user:password@platform.example.test",
        "https://platform.example.test/private",
        "https://platform.example.test?token=secret",
        "https://platform.example.test#fragment",
        "file:///private/secret",
        "https://platform.example.test:99999",
        "https://platform.example.test\n",
        "https://platform.example.test\\other.example.test",
    ],
)
def test_billing_worker_rejects_unsafe_origins(value: str) -> None:
    with pytest.raises(BillingWorkerConfigurationError):
        validate_platform_url(value, allow_loopback_http=True)


def test_billing_worker_http_is_explicit_and_loopback_only() -> None:
    with pytest.raises(BillingWorkerConfigurationError):
        validate_platform_url("http://127.0.0.1:8000")
    assert validate_platform_url(
        "http://127.0.0.1:8000/", allow_loopback_http=True
    ) == "http://127.0.0.1:8000"
    assert validate_platform_url("https://platform.example.test/") == (
        "https://platform.example.test"
    )


def test_billing_worker_token_is_file_only_and_rejects_multiline(tmp_path: Path) -> None:
    token_file = tmp_path / "billing-token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    assert read_service_token(token_file) == TOKEN
    token_file.write_text(TOKEN + "\nsecond-value", encoding="utf-8")
    with pytest.raises(BillingWorkerConfigurationError):
        read_service_token(token_file)
    with pytest.raises(BillingWorkerConfigurationError):
        read_service_token(Path("relative-token"))


def test_billing_worker_only_calls_allowlisted_paths_and_reloads_rotated_token() -> None:
    requests: list[httpx.Request] = []
    tokens = iter([TOKEN, TOKEN + "-rotated"])

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"processed": len(requests) == 1})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        worker = BillingWorker(
            platform_url="https://platform.example.test",
            token_loader=lambda: next(tokens),
            jobs=("inbox", "payments"),
            client=client,
        )
        result = worker.run_once()
    assert result == BillingCycleResult(("inbox",), ())
    assert [request.url.path for request in requests] == [
        BILLING_JOB_PATHS["inbox"],
        BILLING_JOB_PATHS["payments"],
    ]
    assert all(request.method == "POST" for request in requests)
    assert requests[0].headers["X-Internal-Service-Token"] == TOKEN
    assert requests[1].headers["X-Internal-Service-Token"] == TOKEN + "-rotated"
    assert all(TOKEN not in str(request.url) for request in requests)
    assert all(request.content == b"{}" for request in requests)


def test_payment_outage_does_not_starve_inbox_or_enterprise_queue() -> None:
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == BILLING_JOB_PATHS["payments"]:
            return httpx.Response(503, json={"detail": "unconfigured"})
        return httpx.Response(200, json={"processed": True})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        worker = BillingWorker(
            platform_url="https://platform.example.test",
            token_loader=lambda: TOKEN,
            jobs=("payments", "inbox", "enterprise"),
            client=client,
        )
        result = worker.run_once()
    assert len(paths) == 3
    assert result.processed_jobs == ("inbox", "enterprise")
    assert result.temporary_failures == ("payments",)


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 404, 422])
def test_billing_worker_does_not_redirect_credentials_or_retry_auth_failures(
    status: int,
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://other.example.test/steal"},
        )

    with httpx.Client(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as client:
        worker = BillingWorker(
            platform_url="https://platform.example.test",
            token_loader=lambda: TOKEN,
            jobs=("payments",),
            client=client,
        )
        with pytest.raises(BillingWorkerBoundaryError):
            worker.run_once()
    assert len(requests) == 1


@pytest.mark.parametrize("payload", [{}, {"processed": 1}, {"processed": "false"}, []])
def test_billing_worker_rejects_untruthful_response_shape(payload) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        worker = BillingWorker(
            platform_url="https://platform.example.test",
            token_loader=lambda: TOKEN,
            jobs=("payments",),
            client=client,
        )
        with pytest.raises(BillingWorkerBoundaryError):
            worker.run_once()


class _StopAfterWaits:
    def __init__(self, count: int):
        self.count = count
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return len(self.waits) >= self.count

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)


def test_billing_worker_loop_backs_off_and_recovers_without_sleeping(caplog) -> None:
    outcomes = iter(
        [
            BillingCycleResult((), ("payments",)),
            BillingCycleResult((), ("payments",)),
            BillingCycleResult(("payments",), ()),
        ]
    )

    class Worker:
        @staticmethod
        def run_once():
            return next(outcomes)

    stop_event = _StopAfterWaits(3)
    run_loop(Worker(), stop_event=stop_event, interval_seconds=2)
    assert stop_event.waits == [4, 8, 2]
    assert TOKEN not in caplog.text


def test_billing_worker_once_returns_failure_for_incomplete_iteration() -> None:
    class Worker:
        @staticmethod
        def run_once():
            return BillingCycleResult((), ("payments",))

    with pytest.raises(BillingWorkerTemporaryError):
        run_loop(Worker(), stop_event=_StopAfterWaits(1), once=True)
