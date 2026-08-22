from __future__ import annotations

import re
from uuid import uuid4


MAX_REQUEST_ID_LENGTH = 80
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")


def normalize_request_id(value: str | None) -> str:
    """Return a request id that is safe to echo in HTTP headers and logs."""

    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return str(uuid4())
