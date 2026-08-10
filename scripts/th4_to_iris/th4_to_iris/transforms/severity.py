"""TH4 severity int 1-4 → IRIS `severities.severity_name`."""

from __future__ import annotations

TH4_SEVERITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "critical"}


def th4_to_iris(th4_severity: int | None) -> str:
    if th4_severity is None:
        return "medium"
    return TH4_SEVERITY_NAMES.get(int(th4_severity), "medium")
