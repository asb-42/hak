"""Canonical JSON per RFC 8785 (JCS) — the subset HAK needs (spec v0.5.1 D31).

The idempotency hash and C5a replay identity use one rule (D31):
hash input = the normalized POST object after schema validation, serialized
per JCS. Schema validation normalizes omitted == null for optional fields,
so both forms hash identically.

Subset scope: HAK bodies are UTF-8 strings, ints, floats (rare), booleans,
null, arrays, and objects with string keys. JCS essentials implemented:
  - Property sorting by UTF-16 code unit sequence (the JCS rule)
  - No insignificant whitespace
  - JSON.stringify-style string escaping
  - Numbers per ECMAScript (ints stay ints; floats via repr shortest form)

RFC 8785 Appendix B-style vectors are used in the self-test at the bottom
and in the conformance suite (C14).
"""

# HAK — inter-agent messaging bus. Copyright (C) 2026 asb (operator seat).
# SPDX-License-Identifier: AGPL-3.0-only
# This file is part of HAK. See LICENSE for the full notice.

from __future__ import annotations

import json
import math
from typing import Any

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _jcs_key(key: str) -> list[int]:
    """Sort key: UTF-16 code units (RFC 8785 §3.2.3)."""
    be = key.encode("utf-16-be")
    return [int.from_bytes(be[i : i + 2], "big") for i in range(0, len(be), 2)]


def _format_number(x: float) -> str:
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN/Infinity not valid JSON")
    if x == int(x) and abs(x) < 1e21:
        return str(int(x))
    return repr(x)


def canonicalize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_number(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: _jcs_key(kv[0]))
        return "{" + ",".join(
            _escape_string(k) + ":" + canonicalize(v) for k, v in items
        ) + "}"
    raise TypeError(f"cannot canonicalize {type(value)!r}")


def loads_and_canonical(raw: str | bytes) -> str:
    """Parse then canonicalize — the server-side pipeline (parse -> normalize
    -> JCS -> hash input). Client encoder differences die at parse time."""
    return canonicalize(json.loads(raw))


if __name__ == "__main__":
    # RFC 8785 Appendix-style self-check
    vectors = [
        ('{"a":"x","b":null}', '{"a":"x","b":null}'),
        ('{"b":null,"a":"x"}', '{"a":"x","b":null}'),  # key order irrelevant
        ('{"\\u20ac":"Euro Sign"}', None),  # non-ASCII key allowed; checked below
        ("[1.0, 2, 3e2]", "[1,2,300]"),  # number normalization
        ('{"n":1e30}', '{"n":1e+30}'),
        ('{"x":"\\u0041"}', '{"x":"A"}'),  # escape normalization
        ("[]", "[]"),
        ("{}", "{}"),
    ]
    for raw, expected in vectors:
        got = loads_and_canonical(raw)
        if expected is not None:
            assert got == expected, f"{raw} -> {got} != {expected}"
    # JCS sort-order vector from the RFC (§3.2.3): € (U+20AC) < \u00fc (ü) is
    # FALSE in UTF-16 order? No: 0x20AC > 0x00FC, so "ü" sorts first.
    d = loads_and_canonical('{"\\u20ac":1,"\\u00fc":2}')
    assert d.startswith('{"ü"') or d.startswith('{"\\u00fc'), d
    assert loads_and_canonical('{"z":1,"a":2}') == '{"a":2,"z":1}'
    print("canonical.py self-test: all vectors pass")
