from __future__ import annotations

import pytest

from relay_service.providers.http import (
    AioHttpJsonTransport,
    JsonTransportError,
)


class FakeResponse:
    def __init__(
        self, body: bytes, *, status: int = 200, headers: dict | None = None
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False



class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_json_transport_disables_redirects_and_parses_object() -> None:
    session = FakeSession(
        FakeResponse(b'{"id":"task-1"}', headers={"X-Request-ID": "one"})
    )
    transport = AioHttpJsonTransport(session=session)  # type: ignore[arg-type]

    response = await transport.request(
        "POST",
        "https://provider.example/tasks",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "safe"},
    )

    assert response.status == 200
    assert response.body == {"id": "task-1"}
    assert response.headers == {"x-request-id": "one"}
    assert session.calls[0][2]["allow_redirects"] is False
    await transport.close()
    assert session.closed is False


@pytest.mark.asyncio
async def test_invalid_post_json_is_marked_as_ambiguous_without_body_leak() -> None:
    secret = b"secret-provider-diagnostic"
    transport = AioHttpJsonTransport(
        session=FakeSession(FakeResponse(secret))  # type: ignore[arg-type]
    )

    with pytest.raises(JsonTransportError) as caught:
        await transport.request(
            "POST",
            "https://provider.example/tasks",
            headers={},
            json={},
        )

    assert caught.value.outcome_unknown is True
    assert secret.decode() not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_response_size_limit_fails_before_parsing() -> None:
    transport = AioHttpJsonTransport(
        max_response_bytes=8,
        session=FakeSession(  # type: ignore[arg-type]
            FakeResponse(b'{"too":"large"}', headers={"Content-Length": "15"})
        ),
    )

    with pytest.raises(JsonTransportError) as caught:
        await transport.request(
            "GET",
            "https://provider.example/tasks/one",
            headers={},
        )

    assert caught.value.outcome_unknown is False


@pytest.mark.asyncio
async def test_probe_checks_status_without_parsing_body() -> None:
    session = FakeSession(FakeResponse(b"not json", status=204))
    transport = AioHttpJsonTransport(session=session)  # type: ignore[arg-type]

    status = await transport.probe(
        "GET",
        "https://provider.example/ping",
        headers={"Authorization": "Bearer secret"},
    )

    assert status == 204
    assert session.calls[0][2]["allow_redirects"] is False
