#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""PII scrubber for tool_result content before it enters the LLM request.

Off by default — redaction breaks IOC pivoting (an assistant can't
enrich `1.2.3.4` if we hand it `<redacted-ip>`). Strict-DLP on-prem
installs can enable per-class toggles from Server Settings.

Regex primitives are intentionally the same shape as the observability
redactor (see `app.iris_engine.observability.redaction`) so a
security-review update to one gets applied to both surfaces.
"""
from __future__ import annotations

import re
from typing import Any

# IPv4 dotted-quad; not exhaustive on IPv6 because IOCs in IRIS notes
# are overwhelmingly v4 today and matching v6 without false positives
# on hex strings is a rabbit hole.
_IPV4 = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
    r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
)
_EMAIL = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
)
# MD5 / SHA1 / SHA256 hex — covers 32 / 40 / 64 hex chars, must be
# purely hex to avoid gobbling ordinary case ids that happen to be
# hex-looking (`0000000000000001` etc.).
_HASH = re.compile(r'\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b')


def redact_text(
    text: str,
    *,
    redact_ips: bool,
    redact_emails: bool,
    redact_hashes: bool,
) -> str:
    """Scrub a single string. Returns the input unchanged if all flags
    are False so the caller doesn't need to guard."""
    if not text:
        return text
    if redact_ips:
        text = _IPV4.sub('<redacted-ip>', text)
    if redact_emails:
        text = _EMAIL.sub('<redacted-email>', text)
    if redact_hashes:
        text = _HASH.sub('<redacted-hash>', text)
    return text


def redact_content_blocks(
    blocks: list[dict[str, Any]],
    *,
    redact_ips: bool,
    redact_emails: bool,
    redact_hashes: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Walk Anthropic-shaped content blocks, redacting any embedded
    strings in `text`, `content` (nested list or bare string), and any
    values inside `input` / `arguments` dicts.

    Returns `(new_blocks, redaction_applied)` — the boolean is what we
    persist in `case_chat_egress_audit.redacted` so admins can see per
    request whether the scrubber ran.
    """
    if not (redact_ips or redact_emails or redact_hashes):
        return blocks, False
    changed = [False]

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            new = redact_text(
                value,
                redact_ips=redact_ips,
                redact_emails=redact_emails,
                redact_hashes=redact_hashes,
            )
            if new != value:
                changed[0] = True
            return new
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        return value

    return scrub(blocks), changed[0]
