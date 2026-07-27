#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Sentry payload redactor.

IRIS handles DFIR case content — IOCs, malware, evidence, secrets.
None of that may cross the network in a crash-report payload. The two
hooks below are wired into `sentry_sdk.init(before_send=...,
before_breadcrumb=...)` so every event runs through them before the
SDK's transport takes it.

Two knobs, both configurable at init time:

    strict_frame_vars=True
        Drop ALL local variables from stack frames. Recommended for
        deployments that ship error reports to an off-box collector.

    strict_frame_vars=False
        Keep locals but redact by name (password/token/ioc/... — see
        `_SENSITIVE_KEY_RE` and `_CASE_CONTENT_KEY_RE`).

The regex-based path is inherently best-effort; the strict path is a
hard boundary. When in doubt, prefer strict.

All functions are pure and take dict payloads as-is from the SDK so
they're trivially unit-testable — no Flask app, no sentry_sdk imports
needed.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode


REDACTED = '[Filtered]'

# Header names whose values must never leave the box. Matched
# case-insensitively as full names (not substrings) except for the
# `x-*-token` / `x-*-key` families which do need substring matching.
_SENSITIVE_HEADER_RE = re.compile(
    r'^(authorization|cookie|proxy-authorization|x-.*-token|x-.*-key|x-api-key)$',
    re.I,
)

# Body / query-string / stack-frame local KEYS whose VALUES get
# scrubbed. Substring match on the key name — `oauth_token`, `db_password`,
# `api_key`, `sentry_dsn` all hit.
_SENSITIVE_KEY_RE = re.compile(
    r'secret|token|password|passwd|key|credential|api[_-]?key|dsn|cookie|session',
    re.I,
)

# Additional key names that hit DFIR case content. Stack-frame locals
# named `ioc_value`, `evidence_bytes`, `malware_hash` etc. get
# scrubbed on top of the general sensitive-key rule.
_CASE_CONTENT_KEY_RE = re.compile(
    r'ioc|evidence|malware|indicator|payload|hash|artifact',
    re.I,
)

# Source-file paths whose entire local-variable dict gets blanket
# redacted regardless of key name. These modules touch plaintext
# secrets end-to-end and any stack-frame local from them is high-risk.
_SENSITIVE_FRAME_FILES = (
    'iris_engine/mail/secrets.py',
    'iris_engine/access_control/',
    'iris_engine/mail/config.py',
)


def _should_redact_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key) or _CASE_CONTENT_KEY_RE.search(key))


def _redact_mapping(mapping: dict) -> dict:
    """Copy `mapping` with values replaced by REDACTED where the key hits."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            out[key] = value
            continue
        if _should_redact_key(key):
            out[key] = REDACTED
            continue
        if isinstance(value, dict):
            out[key] = _redact_mapping(value)
            continue
        if isinstance(value, list):
            out[key] = [
                _redact_mapping(item) if isinstance(item, dict) else item
                for item in value
            ]
            continue
        out[key] = value
    return out


def _redact_headers(headers: Any) -> Any:
    """Replace values of sensitive-named headers with REDACTED.

    Sentry passes headers as either a dict or a list-of-pairs
    depending on integration; support both.
    """
    if isinstance(headers, dict):
        return {
            name: (REDACTED if isinstance(name, str) and _SENSITIVE_HEADER_RE.match(name) else value)
            for name, value in headers.items()
        }
    if isinstance(headers, list):
        return [
            [name, REDACTED if isinstance(name, str) and _SENSITIVE_HEADER_RE.match(name) else value]
            for name, value in headers
        ]
    return headers


def _redact_query_string(qs: str) -> str:
    """Parse `qs`, redact values whose keys hit the sensitive regex, re-serialize."""
    if not isinstance(qs, str) or not qs:
        return qs
    pairs = parse_qsl(qs, keep_blank_values=True)
    redacted = [
        (key, REDACTED if _should_redact_key(key) else value)
        for key, value in pairs
    ]
    return urlencode(redacted)


def _frame_file_is_sensitive(abs_or_rel_path: Any) -> bool:
    if not isinstance(abs_or_rel_path, str):
        return False
    return any(marker in abs_or_rel_path for marker in _SENSITIVE_FRAME_FILES)


def _redact_frame_vars(frame: dict, strict: bool) -> None:
    """In-place: mutate `frame['vars']` per the redaction rules."""
    frame_vars = frame.get('vars')
    if not isinstance(frame_vars, dict):
        return
    if strict:
        # Zero-trust: nothing local ever leaves the box.
        frame['vars'] = {name: REDACTED for name in frame_vars}
        return
    if _frame_file_is_sensitive(frame.get('abs_path') or frame.get('filename')):
        frame['vars'] = {name: REDACTED for name in frame_vars}
        return
    frame['vars'] = _redact_mapping(frame_vars)


def _walk_exception_frames(event: dict, strict: bool) -> None:
    exception = event.get('exception')
    if not isinstance(exception, dict):
        return
    values = exception.get('values')
    if not isinstance(values, list):
        return
    for value in values:
        stacktrace = value.get('stacktrace') if isinstance(value, dict) else None
        if not isinstance(stacktrace, dict):
            continue
        frames = stacktrace.get('frames')
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if isinstance(frame, dict):
                _redact_frame_vars(frame, strict)


def make_before_send(*, strict_frame_vars: bool = False):
    """Return a `before_send(event, hint)` callback for `sentry_sdk.init`.

    Closure so the strict-mode toggle can be flipped per install
    without touching global state.
    """
    def before_send(event: dict, _hint: Any) -> dict:
        request = event.get('request')
        if isinstance(request, dict):
            if 'headers' in request:
                request['headers'] = _redact_headers(request['headers'])
            # Sentry always drops cookies when `send_default_pii=False`
            # but we blank the key here for defense in depth in case a
            # future integration re-adds them.
            request.pop('cookies', None)
            if 'query_string' in request:
                request['query_string'] = _redact_query_string(request['query_string'])
            data = request.get('data')
            if isinstance(data, dict):
                request['data'] = _redact_mapping(data)
            elif isinstance(data, str) and data:
                # Free-form string bodies (e.g. raw JSON blobs) are
                # unsafe to regex-scan. Replace wholesale.
                request['data'] = '[redacted-body]'
        _walk_exception_frames(event, strict_frame_vars)
        return event
    return before_send


# Breadcrumb categories worth dropping entirely — SQL statements can
# echo case-content values as bound parameters, and HTTP breadcrumbs
# from Sentry's own logging integration risk echoing IOC URLs.
_DROP_BREADCRUMB_CATEGORIES = frozenset({'query', 'sql'})


def before_breadcrumb(crumb: dict, _hint: Any):
    """Return the crumb (possibly redacted) or None to drop it."""
    category = crumb.get('category')
    if category in _DROP_BREADCRUMB_CATEGORIES:
        return None
    data = crumb.get('data')
    if isinstance(data, dict):
        crumb['data'] = _redact_mapping(data)
    return crumb
