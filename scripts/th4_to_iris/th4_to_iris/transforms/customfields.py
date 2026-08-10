"""Flatten TH4 `customFields` payload into a plain {name: value} JSON dict.

TH4 serialises customFields as::

    { "cf_name": { "string": "hello", "order": 1 } }

with the type suffix indicating which sub-key to read. Preserved as-is (with
type prefix on the key) so a human can still tell the source type.
"""

from __future__ import annotations

from typing import Any

TH4_CF_TYPES = ("string", "boolean", "number", "date", "integer", "float")


def flatten(custom_fields: dict[str, Any] | None) -> dict[str, Any]:
    if not custom_fields:
        return {}
    out: dict[str, Any] = {}
    for name, payload in custom_fields.items():
        if not isinstance(payload, dict):
            out[f"th4_cf_{name}"] = payload
            continue
        for t in TH4_CF_TYPES:
            if t in payload:
                out[f"th4_cf_{name}"] = {"type": t, "value": payload[t]}
                break
        else:
            out[f"th4_cf_{name}"] = payload
    return out
