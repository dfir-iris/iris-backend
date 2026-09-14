#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Hard ceiling on the serialised size of an MCP tool result.

A tool result is not paid once. `case_chat` persists every
`tool_result` block and replays the conversation to the provider on
each subsequent turn, so an oversized payload is charged again on every
following request until it falls out of the history trim window. One
unbounded list call can therefore cost more than the rest of the
conversation put together.

The per-tool projections in `tools/_common.py` keep the common case
small, but they are declared tool by tool and a caller can always ask
for `view=full`. This module is the backstop: it runs on the way out of
`dispatch_tool_call`, so a tool nobody projected — including one added
later — still cannot flood the model's context.

Truncation is always *reported*. The caller gets a `_truncated` block
describing what was dropped and how to get the rest; silently short
results would read as complete and the model would reason from a
partial picture without knowing it.
"""
from __future__ import annotations

import json
from typing import Any


# ~10k tokens at 4 chars/token. Sized so that a full turn of
# `chatbot_max_tool_calls_per_turn` (8 by default) cannot on its own
# exhaust a 128k context window.
MAX_RESULT_BYTES = 40_000

# Any single string longer than this is clipped before rows are
# dropped — one pathological field (a raw detection payload, a long
# note body) should not cost the caller the rest of its results.
MAX_TEXT_FIELD_BYTES = 4_000

# Keys the list-shaped tools use for their rows, most specific first.
# Anything else falls back to "the longest list in the payload".
_ROW_KEYS = ('data', 'notes', 'war_rooms', 'items', 'results')

# Headroom held back from `max_bytes` for the `_truncated` block itself.
# The block is attached after the shrink passes have run, so without a
# reservation every truncated result would land just over the ceiling it
# was shrunk to meet. The reported field-name lists are capped (see
# `_capped`) to keep the block comfortably inside this.
_REPORT_RESERVE_BYTES = 1_024

# Most field names reported in one `_truncated` block.
_MAX_REPORTED_FIELDS = 10

# Floor for the aggressive final clip. Below this a text field carries
# no usable signal and the caller is better served by refetching.
_MIN_TEXT_FIELD_BYTES = 200

_HINT = (
    'The result was too large to return in full. Narrow the filters, '
    'lower `per_page`, or fetch individual records with the matching '
    '`*_get` tool. Do not assume the omitted records are absent.'
)


def apply_result_budget(
    result: Any, max_bytes: int = MAX_RESULT_BYTES,
) -> tuple[Any, dict | None]:
    """Shrink `result` until it serialises within `max_bytes`.

    Returns `(result, report)`. `report` is None when the payload
    already fitted and nothing was touched; otherwise it is the
    `_truncated` block that was attached to the result, returned
    separately so the dispatcher can log it.

    Four passes, cheapest loss of information first: clip long strings,
    drop rows off the end of the dominant list, elide whole oversized
    values, then clip text hard. Each pass is skipped once the payload
    fits.

    The one thing never given up is the first row of a list: an empty
    result reads as "nothing matched", which is a different and far more
    misleading answer than "here is the first of many".
    """
    if _encoded_length(result) <= max_bytes:
        return result, None

    # Shrink to the ceiling minus the space the report will occupy, so
    # the final payload — report included — lands inside `max_bytes`.
    effective = max(max_bytes // 2, max_bytes - _REPORT_RESERVE_BYTES)

    report: dict[str, Any] = {'reason': (
        f'result exceeded the {max_bytes}-byte MCP response budget'
    )}

    result, clipped = _clip_long_strings(result, MAX_TEXT_FIELD_BYTES)
    if clipped:
        report['fields_clipped'] = _capped(clipped)

    rows_key = None
    if _encoded_length(result) > effective:
        result, rows_key = _drop_rows(result, effective, report)

    if _encoded_length(result) > effective:
        result = _elide_largest_values(result, effective, report, rows_key)

    if _encoded_length(result) > effective:
        # A single row that still doesn't fit. Clipping text to a hard
        # floor is the last lever that keeps the row itself intact.
        result, hard_clipped = _clip_long_strings(
            result, max(_MIN_TEXT_FIELD_BYTES, effective // 8),
        )
        if hard_clipped:
            report['fields_clipped'] = _capped(
                sorted(set(report.get('fields_clipped', [])) | set(hard_clipped))
            )

    report['hint'] = _HINT
    if isinstance(result, dict):
        result['_truncated'] = report
        return result, report
    # A non-dict result (a bare list or scalar) has nowhere to carry the
    # report, so wrap it rather than drop the warning on the floor.
    return {'data': result, '_truncated': report}, report


def _capped(names: list[str]) -> list[str]:
    """Bound a reported field-name list so the report stays small."""
    if len(names) <= _MAX_REPORTED_FIELDS:
        return names
    remaining = len(names) - _MAX_REPORTED_FIELDS
    return names[:_MAX_REPORTED_FIELDS] + [f'… and {remaining} more']


def _encode(value: Any) -> str:
    return json.dumps(value, default=str)


def _encoded_length(value: Any) -> int:
    try:
        return len(_encode(value))
    except (TypeError, ValueError):
        return len(str(value))


def _clip_long_strings(value: Any, limit: int) -> tuple[Any, list[str]]:
    """Recursively clip every string longer than `limit`.

    Returns the rebuilt value and the sorted names of the keys that were
    clipped. The input is never mutated — tool handlers hand us dicts
    that may still reference live ORM state.
    """
    clipped: set[str] = set()

    def _walk(node: Any, key: str | None) -> Any:
        if isinstance(node, str):
            if len(node) > limit:
                if key:
                    clipped.add(key)
                return f'{node[:limit]}… [clipped, {len(node)} chars total]'
            return node
        if isinstance(node, dict):
            return {k: _walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v, key) for v in node]
        return node

    return _walk(value, None), sorted(clipped)


def _find_row_list(result: Any) -> tuple[str | None, list | None]:
    """Locate the dominant list-of-records in a tool result."""
    if not isinstance(result, dict):
        return None, None
    for key in _ROW_KEYS:
        value = result.get(key)
        if isinstance(value, list) and value:
            return key, value
    best_key, best_len = None, 0
    for key, value in result.items():
        if isinstance(value, list) and len(value) > best_len:
            best_key, best_len = key, len(value)
    return best_key, (result[best_key] if best_key is not None else None)


def _drop_rows(
    result: Any, max_bytes: int, report: dict,
) -> tuple[Any, str | None]:
    """Keep the longest prefix of the dominant row list that fits.

    Binary search on the row count: the rows are heterogeneous in size,
    so counting bytes per row and dividing would under- or over-shoot.
    At most ~7 re-encodes for a 100-row page.

    Returns the shrunk result and the key that was shrunk, so the later
    elision pass knows not to throw away the rows this pass just fought
    to keep.
    """
    key, rows = _find_row_list(result)
    if not rows:
        return result, None

    total = len(rows)
    low, high, best = 0, total, 0
    while low <= high:
        mid = (low + high) // 2
        if _encoded_length({**result, key: rows[:mid]}) <= max_bytes:
            best, low = mid, mid + 1
        else:
            high = mid - 1

    best = max(best, 1)
    if best >= total:
        return result, key

    report['rows_shown'] = best
    report['rows_total'] = total
    report['rows_key'] = key
    return {**result, key: rows[:best]}, key


def _elide_largest_values(
    result: Any, max_bytes: int, report: dict, rows_key: str | None,
) -> Any:
    """Replace the biggest top-level values with a stub until it fits.

    The last resort for a record carrying a structured blob — a raw
    alert payload, a modification history — that string clipping cannot
    shrink because the bulk is in the structure rather than in any one
    string.

    `rows_key` is left alone: `_drop_rows` has already trimmed it to the
    rows that fit, and eliding it would replace real results with a
    stub.
    """
    if not isinstance(result, dict):
        return result

    out = dict(result)
    elided: list[str] = []
    by_size = sorted(
        ((_encoded_length(v), k) for k, v in out.items()),
        reverse=True,
    )
    for size, key in by_size:
        if _encoded_length(out) <= max_bytes:
            break
        if key == rows_key or key.startswith('_'):
            continue
        if isinstance(out[key], (int, float, bool)) or out[key] is None:
            continue
        out[key] = f'[elided, {size} bytes — refetch this record on its own]'
        elided.append(key)

    if elided:
        report['fields_elided'] = _capped(elided)
    return out
