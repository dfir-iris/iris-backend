"""TH4 tag flattening.

TH4 exposes tags as flat strings on the wire (e.g. ``"tlp:amber"``,
``"misp-galaxy:actor=\\"APT29\\""``). Colours are org-scoped taxonomy metadata,
not part of the string — dropped.
"""

from __future__ import annotations


def clean(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags:
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def namespace_of(tag: str) -> str | None:
    """Return the namespace before the first ``:``, or None."""
    if ":" in tag:
        return tag.split(":", 1)[0]
    return None
