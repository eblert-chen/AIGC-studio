"""The director-only xutian-json-sort-v1 browser JSON representation.

This is intentionally separate from frozen Relay catalog/release revisions.
Keys sort by UTF-16 code units; numbers use the browser's finite binary64
representation, with safe integers only and negative zero normalized to zero.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any


MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _string(value: str) -> str:
    # UTF-8 cannot encode isolated UTF-16 surrogates. Reject instead of allowing
    # different runtimes to silently replace them with U+FFFD.
    value.encode("utf-8", errors="strict")
    return json.dumps(value, ensure_ascii=False)


def _number(value: int | float) -> str:
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("director JSON integer exceeds the safe range")
        return str(value)
    if not math.isfinite(value):
        raise ValueError("director JSON number must be finite")
    if value == 0:
        return "0"
    if value.is_integer():
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("director JSON integer exceeds the safe range")
        return str(int(value))
    # CPython and ECMAScript use the shortest round-trippable binary64 decimal.
    # Their notation thresholds differ: JS keeps 1e-6 in fixed notation and
    # does not zero-pad a scientific exponent.
    rendered = repr(value)
    if abs(value) >= 1e-6:
        return format(Decimal(rendered), "f")
    mantissa, exponent = rendered.split("e")
    return f"{mantissa}e{int(exponent)}"


def _render(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_render(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("director JSON object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be", errors="strict"))
        members = (_string(key) + ":" + _render(value[key]) for key in keys)
        return "{" + ",".join(members) + "}"
    raise ValueError("unsupported director JSON value")


def director_canonical_json(value: Any) -> bytes:
    return _render(value).encode("utf-8")
