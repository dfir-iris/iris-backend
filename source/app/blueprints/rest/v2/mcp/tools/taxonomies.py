#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tool exposing IRIS's seed taxonomies (statuses, types, TLPs...).

A single tool returns every enum-like lookup table in one payload so an
AI client can resolve a human-readable status (e.g. "Done") to its
numeric ID without probing `iris_case_*_update` with trial values. The
tables are small (typically <20 rows each) and rarely change, so the
client should call this once per session and cache the result.
"""
from __future__ import annotations

from typing import Any

from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.models.alert_clusters import AlertClusterStatus
from app.models.alerts import AlertResolutionStatus
from app.models.alerts import AlertStatus
from app.models.alerts import Severity
from app.models.assets import AnalysisStatus
from app.models.assets import AssetsType
from app.models.assets import CompromiseStatus
from app.models.authorization import Permissions
from app.models.cases import CaseClassification
from app.models.cases import CaseState
from app.models.evidences import EvidenceTypes
from app.models.iocs import Tlp
from app.models.models import EventCategory
from app.models.models import IocType
from app.models.models import TaskStatus


# (taxonomy_key, model, id_column, name_column, description_column|None)
# Ordered from most-frequently-referenced to least so a truncated read
# still surfaces the taxonomies most often needed for asset/task updates.
_TAXONOMIES: tuple[tuple[str, Any, str, str, str | None], ...] = (
    ('analysis_statuses', AnalysisStatus, 'id', 'name', None),
    ('task_statuses', TaskStatus, 'id', 'status_name', 'status_description'),
    ('case_states', CaseState, 'state_id', 'state_name', 'state_description'),
    ('case_classifications', CaseClassification, 'id', 'name', 'description'),
    ('severities', Severity, 'severity_id', 'severity_name', 'severity_description'),
    ('tlps', Tlp, 'tlp_id', 'tlp_name', None),
    ('alert_statuses', AlertStatus, 'status_id', 'status_name', 'status_description'),
    ('alert_resolutions', AlertResolutionStatus,
     'resolution_status_id', 'resolution_status_name', 'resolution_status_description'),
    ('alert_cluster_statuses', AlertClusterStatus,
     'status_id', 'status_name', 'status_description'),
    ('asset_types', AssetsType, 'asset_id', 'asset_name', 'asset_description'),
    ('ioc_types', IocType, 'type_id', 'type_name', 'type_description'),
    ('evidence_types', EvidenceTypes, 'id', 'name', 'description'),
    ('event_categories', EventCategory, 'id', 'name', None),
)


def _dump_model_taxonomy(
    model: Any,
    id_column: str,
    name_column: str,
    description_column: str | None,
) -> list[dict[str, Any]]:
    id_col = getattr(model, id_column)
    rows = model.query.order_by(id_col.asc()).all()
    result: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            'id': getattr(row, id_column),
            'name': getattr(row, name_column),
        }
        if description_column is not None:
            entry['description'] = getattr(row, description_column)
        result.append(entry)
    return result


def _dump_compromise_status() -> list[dict[str, Any]]:
    return [{'id': member.value, 'name': member.name} for member in CompromiseStatus]


@mcp_tool(
    name='iris_taxonomies_list',
    description=(
        'Return every IRIS seed taxonomy in one payload: analysis statuses '
        '(asset `analysis_status_id`), task statuses, case states, case '
        'classifications, severities, TLPs, alert statuses/resolutions/'
        'cluster statuses, asset types, IOC types, evidence types, event '
        'categories, and the asset compromise-status enum. Rows are '
        '`{id, name, description?}`. Tables are small and rarely change — '
        'call this once per session and cache the map before setting any '
        '`*_status_id` / `*_type_id` field via an update tool.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_taxonomies_list(_args: dict) -> dict:
    payload: dict[str, list[dict[str, Any]]] = {}
    for key, model, id_col, name_col, desc_col in _TAXONOMIES:
        payload[key] = _dump_model_taxonomy(model, id_col, name_col, desc_col)
    payload['asset_compromise_statuses'] = _dump_compromise_status()
    return payload
