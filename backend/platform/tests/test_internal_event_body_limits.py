from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request
import pytest

from platform_api.main import (
    MAX_INTERNAL_EVENT_BODY_BYTES,
    _read_limited_request_body,
)


def _request(*, declared_length: int, chunks: list[bytes], reads: list[int]) -> Request:
    remaining = list(chunks)

    async def receive():
        reads.append(1)
        chunk = remaining.pop(0) if remaining else b""
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": bool(remaining),
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/test",
            "headers": [(b"content-length", str(declared_length).encode("ascii"))],
        },
        receive,
    )


def test_declared_oversized_event_is_rejected_without_reading_stream():
    reads: list[int] = []
    request = _request(
        declared_length=MAX_INTERNAL_EVENT_BODY_BYTES + 1,
        chunks=[b"not-read"],
        reads=reads,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(_read_limited_request_body(request))
    assert error.value.status_code == 413
    assert reads == []


def test_actual_stream_size_is_bounded_when_content_length_is_understated():
    reads: list[int] = []
    request = _request(
        declared_length=1,
        chunks=[
            b"a" * MAX_INTERNAL_EVENT_BODY_BYTES,
            b"b",
        ],
        reads=reads,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(_read_limited_request_body(request))
    assert error.value.status_code == 413
    assert len(reads) == 2
