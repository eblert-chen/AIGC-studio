from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.requests import ClientDisconnect


async def read_limited_request_body(request: Request, *, max_bytes: int) -> bytes:
    """Read signature-sensitive bytes without buffering an unbounded request.

    Content-Length is only an early rejection hint. Always enforce the limit
    against the received chunks, including when that header is missing or false.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")

    lengths = request.headers.getlist("content-length")
    if lengths:
        value = lengths[0]
        if len(lengths) != 1 or not value.isascii() or not value.isdecimal():
            raise HTTPException(status_code=400, detail="Content-Length 无效")
        try:
            declared_length = int(value)
        except ValueError:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from None
        if declared_length > max_bytes:
            raise HTTPException(status_code=413, detail="请求载荷过大")

    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(chunk) > max_bytes - len(body):
                # Do not append the overflowing chunk or drain subsequent input.
                raise HTTPException(status_code=413, detail="请求载荷过大")
            body.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail="请求载荷未完整接收") from None
    return bytes(body)
