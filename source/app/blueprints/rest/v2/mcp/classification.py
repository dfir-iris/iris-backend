#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Classify every MCP tool as read-only or write.

Two consumers rely on this:

  * `dispatch.py` — a write tool on a case (or war room) requires
    `full_access` on it, exactly like the REST route that does the same
    thing. Reads only need `read_only`.
  * `case_chat` (Yuki) — read tools auto-execute inside the loop, write
    tools stop the turn and wait for an explicit human approval.

Both sets are enumerated **explicitly** — heuristic classification
(prefix-based) would silently misclassify write tools with an
analytical-sounding name (e.g. `iris_alerts_escalate` creates a case).
A new MCP tool that isn't in either set fails the classification test
in CI with the name printed — the developer adding a tool must pick a
side.

Unclassified is treated as write everywhere: dispatch demands
`full_access` for it, and the chat loop **refuses** it outright.
Fail-open in either direction is worse: auto-executing a surprise write
is catastrophic, and silently prompting for approval on a benign read
teaches the analyst to click-through approvals.

This module lives under `mcp/` rather than `case_chat/` so `dispatch.py`
can import it without a cycle (`case_chat/__init__` pulls in the
transport, which pulls in dispatch).
"""
from __future__ import annotations


READ_ONLY_TOOLS: frozenset[str] = frozenset({
    'iris_cases_list', 'iris_cases_filter', 'iris_cases_get',
    'iris_case_access_list',
    'iris_customers_get', 'iris_customers_list',
    'iris_alerts_list', 'iris_alerts_get', 'iris_alerts_related_get',
    # Alerts — lookup reads (needed before iris_alerts_update)
    'iris_alerts_status_list', 'iris_alerts_resolution_list',
    'iris_alerts_severity_list', 'iris_alerts_classification_list',
    'iris_case_iocs_list', 'iris_case_iocs_get',
    'iris_case_assets_list', 'iris_case_assets_get',
    'iris_case_notes_list', 'iris_case_notes_get',
    'iris_case_notes_directories_list',
    'iris_case_events_list', 'iris_case_evidences_list',
    'iris_case_tasks_list', 'iris_case_tasks_get',
    'iris_case_graph_get', 'iris_case_graph_paths', 'iris_case_graph_gaps',
    'iris_search',
    'iris_me_get', 'iris_me_context_get',
    'iris_taxonomies_list',
    # War rooms — reads
    'iris_war_rooms_list', 'iris_war_rooms_get',
    'iris_war_room_chat_list',
    'iris_war_room_sitreps_list',
    'iris_war_room_notes_list',
    'iris_war_room_tasks_list',
    'iris_war_room_decisions_list',
    'iris_war_room_cases_list', 'iris_war_room_members_list',
    'iris_war_room_board_get', 'iris_war_room_teams_list',
    'iris_war_room_datastore_list',
    'iris_war_room_timelines_list', 'iris_war_room_timeline_events_list',
    'iris_war_room_linked_case_timelines_list', 'iris_war_room_linked_case_events_list',
    'iris_war_room_scope_assets_list', 'iris_war_room_scope_iocs_list',
    'iris_war_room_scope_registry_assets_list', 'iris_war_room_scope_staging_list',
    # Vulnerabilities — reads
    'iris_vulnerabilities_search', 'iris_vulnerabilities_get',
    'iris_case_vulnerabilities_list',
    'iris_war_room_vulnerabilities_matrix',
})

WRITE_TOOLS: frozenset[str] = frozenset({
    # Cases
    'iris_cases_create', 'iris_cases_update',
    'iris_cases_close', 'iris_cases_reopen',
    'iris_case_access_set_user', 'iris_case_access_set_group',
    # Alerts
    'iris_alerts_create', 'iris_alerts_update',
    'iris_alerts_escalate', 'iris_alerts_merge',
    # IOCs / assets / notes / tasks (case-scoped mutating ops)
    'iris_case_iocs_create', 'iris_case_iocs_update', 'iris_case_iocs_delete',
    'iris_case_assets_create', 'iris_case_assets_update', 'iris_case_assets_delete',
    'iris_case_events_create', 'iris_case_events_update', 'iris_case_events_delete',
    'iris_case_notes_create', 'iris_case_notes_update',
    'iris_case_notes_directories_create',
    'iris_case_datastore_files_create',
    'iris_case_tasks_create', 'iris_case_tasks_update',
    'iris_case_tasks_set_status',
    'iris_case_graph_edge_create',
    # War rooms — writes
    'iris_war_rooms_create',
    'iris_war_room_cases_attach', 'iris_war_room_members_add',
    'iris_war_room_chat_post',
    'iris_war_room_sitreps_create',
    'iris_war_room_notes_create', 'iris_war_room_notes_update',
    'iris_war_room_tasks_create',
    'iris_war_room_decisions_create',
    'iris_war_rooms_update',
    'iris_war_room_sitreps_update',
    'iris_war_room_tasks_update',
    'iris_war_room_decisions_update',
    'iris_war_room_timeline_events_create', 'iris_war_room_timeline_events_update',
    'iris_war_room_scope_assets_create', 'iris_war_room_scope_iocs_create',
    'iris_war_room_scope_assets_push', 'iris_war_room_scope_iocs_push',
    'iris_war_room_scope_assets_flag', 'iris_war_room_scope_assets_status',
    'iris_war_room_scope_registry_assets_attach',
    'iris_war_room_scope_staging_create', 'iris_war_room_scope_staging_push',
    'iris_war_room_vulnerabilities_track',
    # Vulnerabilities
    'iris_vulnerabilities_create', 'iris_case_vulnerabilities_create',
    'iris_vulnerabilities_update', 'iris_case_vulnerabilities_update',
    'iris_vulnerabilities_delete', 'iris_case_vulnerabilities_delete',
})


def is_read_only(tool_name: str) -> bool:
    return tool_name in READ_ONLY_TOOLS


def is_write(tool_name: str) -> bool:
    return tool_name in WRITE_TOOLS


def is_classified(tool_name: str) -> bool:
    return tool_name in READ_ONLY_TOOLS or tool_name in WRITE_TOOLS


def mutates(tool_name: str) -> bool:
    """True when the tool must be treated as mutating.

    Anything not explicitly listed as read-only counts, so a tool added
    without a classification is gated on `full_access` rather than
    slipping through on `read_only`.
    """
    return tool_name not in READ_ONLY_TOOLS


class UnclassifiedToolError(Exception):
    """Raised when a tool the LLM emitted is in neither set. Loop treats
    this as a fatal per-turn error and surfaces it to the analyst as
    'a new tool needs classification — contact your administrator'."""
