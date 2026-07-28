#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Classify every MCP tool as read-only or write.

Both sets are enumerated **explicitly** — heuristic classification
(prefix-based) would silently misclassify write tools with an
analytical-sounding name (e.g. `iris_alerts_escalate` creates a case).
A new MCP tool that isn't in either set fails the classification test
in CI with the name printed — the developer adding a tool must pick a
side.

At runtime, an unclassified tool is **refused** at dispatch. Fail-open
in either direction is worse: auto-executing a surprise write is
catastrophic, and silently prompting for approval on a benign read
teaches the analyst to click-through approvals.
"""
from __future__ import annotations


READ_ONLY_TOOLS: frozenset[str] = frozenset({
    'iris_cases_list', 'iris_cases_filter', 'iris_cases_get',
    'iris_alerts_list', 'iris_alerts_get', 'iris_alerts_related_get',
    'iris_case_iocs_list', 'iris_case_iocs_get',
    'iris_case_assets_list', 'iris_case_assets_get',
    'iris_case_notes_list', 'iris_case_notes_get',
    'iris_case_tasks_list', 'iris_case_tasks_get',
    'iris_search',
    'iris_me_get', 'iris_me_context_get',
})

WRITE_TOOLS: frozenset[str] = frozenset({
    # Cases
    'iris_cases_create', 'iris_cases_update',
    'iris_cases_close', 'iris_cases_reopen',
    # Alerts
    'iris_alerts_escalate', 'iris_alerts_merge',
    # IOCs / assets / notes / tasks (case-scoped mutating ops)
    'iris_case_iocs_create', 'iris_case_iocs_update', 'iris_case_iocs_delete',
    'iris_case_assets_create', 'iris_case_assets_update', 'iris_case_assets_delete',
    'iris_case_notes_create', 'iris_case_notes_update',
    'iris_case_tasks_create', 'iris_case_tasks_update',
    'iris_case_tasks_set_status',
})


def is_read_only(tool_name: str) -> bool:
    return tool_name in READ_ONLY_TOOLS


def is_write(tool_name: str) -> bool:
    return tool_name in WRITE_TOOLS


def is_classified(tool_name: str) -> bool:
    return tool_name in READ_ONLY_TOOLS or tool_name in WRITE_TOOLS


class UnclassifiedToolError(Exception):
    """Raised when a tool the LLM emitted is in neither set. Loop treats
    this as a fatal per-turn error and surfaces it to the analyst as
    'a new tool needs classification — contact your administrator'."""
