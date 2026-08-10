"""TLP: TH4 int 0-3 → IRIS `tlp.tlp_id` (seeded 1..4 in `p1_reference`)."""

from __future__ import annotations

TH4_TLP_NAMES = {0: "white", 1: "green", 2: "amber", 3: "red"}


def th4_to_iris(th4_tlp: int | None) -> str:
    if th4_tlp is None:
        return "amber"
    return TH4_TLP_NAMES.get(int(th4_tlp), "amber")
