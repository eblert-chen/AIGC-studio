"""Ephemeral Platform wrapper used only by the cross-service cost gate.

The host runner launches exactly one uvicorn process with a random nonce.  The
middleware echoes the nonce and the OS process id on the health probe, which
lets the runner prove that it tested the source snapshot and process it started
instead of an unrelated Platform instance already listening on the machine.
"""

from __future__ import annotations

import os

from platform_api.main import app


_PROCESS_NONCE = os.environ.get("PLATFORM_COST_ACCEPTANCE_PROCESS_NONCE", "")
if len(_PROCESS_NONCE) < 32:
    raise RuntimeError("Platform cost-acceptance process nonce is required")


@app.middleware("http")
async def bind_cost_acceptance_process(request, call_next):
    response = await call_next(request)
    response.headers["X-Cost-Acceptance-Process-Nonce"] = _PROCESS_NONCE
    response.headers["X-Cost-Acceptance-Process-Pid"] = str(os.getpid())
    return response

