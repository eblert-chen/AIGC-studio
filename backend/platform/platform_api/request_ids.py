from __future__ import annotations

import hashlib
import re
from uuid import uuid4


MAX_REQUEST_ID_LENGTH = 80
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")


def normalize_request_id(value: str | None) -> str:
    """Return a header-safe request id, replacing untrusted values entirely."""

    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return str(uuid4())


def stable_request_id(scope: str, reference: object) -> str:
    """Build a deterministic, header-safe id for asynchronous operations."""

    candidate = f"{scope}-{reference}"
    if _SAFE_REQUEST_ID.fullmatch(candidate):
        return candidate
    digest = hashlib.sha256(str(reference).encode("utf-8")).hexdigest()
    safe_scope = re.sub(r"[^A-Za-z0-9._:-]", "-", scope)[:15] or "request"
    return f"{safe_scope}-{digest}"
