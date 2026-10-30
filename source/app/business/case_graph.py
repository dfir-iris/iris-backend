#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Investigation graph of a case: read, stored edges, free nodes, paste,
views, import / export (docs/investigation-graph.md, §4).

Authorization is NOT done here: the blueprint checks the case access
(read for reads, full access for writes) and hands over a resolver of
the other cases the caller may read (`readable_case_ids_resolver`, used
by `include=cross_case`).

`case_graph_get` is the single assembly entry point: suggestions, the
war-room graph, MCP tools and report contexts build on it.
"""

import datetime
import json
import re

from flask import current_app

from app.business.assets import assets_create
from app.business.case_graph_assembly import CASE_GRAPH_INCLUDE_KINDS
from app.business.case_graph_assembly import case_graph_assembly_build
from app.business.case_graph_assembly import case_graph_assembly_free_node
from app.business.case_graph_assembly import case_graph_isoformat
from app.business.case_graph_assembly import case_graph_relation_type_serialize
from app.business.case_graph_assembly import case_graph_stored_edge_serialize
from app.business.case_graph_derive import case_graph_key
from app.business.case_graph_derive import case_graph_parse_key
from app.business.case_graph_exchange import case_graph_exchange_parse_json
from app.business.case_graph_exchange import case_graph_exchange_parse_stix
from app.business.case_graph_exchange import case_graph_exchange_to_graphml
from app.business.case_graph_exchange import case_graph_exchange_to_stix
from app.business.case_graph_explore import case_graph_explore_neighbours
from app.business.case_graph_explore import case_graph_explore_paths
from app.business.case_graph_paste import CASE_GRAPH_PASTE_MAX_TEXT
from app.business.case_graph_paste import case_graph_paste_extract
from app.business.case_graph_paste import case_graph_paste_plan
from app.business.ioc_type_detect import ioc_type_detect_pick
from app.business.iocs import iocs_create
from app.datamgmt.case.case_graph_db import case_graph_db_add
from app.datamgmt.case.case_graph_db import case_graph_db_alert_link_rows
from app.datamgmt.case.case_graph_db import case_graph_db_alert_rows
from app.datamgmt.case.case_graph_db import case_graph_db_asset_cross_case_rows
from app.datamgmt.case.case_graph_db import case_graph_db_asset_flag_rows
from app.datamgmt.case.case_graph_db import case_graph_db_asset_managed_rows
from app.datamgmt.case.case_graph_db import case_graph_db_asset_rows
from app.datamgmt.case.case_graph_db import case_graph_db_case_rows
from app.datamgmt.case.case_graph_db import case_graph_db_commit
from app.datamgmt.case.case_graph_db import case_graph_db_delete
from app.datamgmt.case.case_graph_db import case_graph_db_delete_edges_touching
from app.datamgmt.case.case_graph_db import case_graph_db_edge_as_dict
from app.datamgmt.case.case_graph_db import case_graph_db_edge_event_ids
from app.datamgmt.case.case_graph_db import case_graph_db_edge_get
from app.datamgmt.case.case_graph_db import case_graph_db_edge_rows
from app.datamgmt.case.case_graph_db import case_graph_db_edge_set_events
from app.datamgmt.case.case_graph_db import case_graph_db_edges_touching
from app.datamgmt.case.case_graph_db import case_graph_db_event_dates
from app.datamgmt.case.case_graph_db import case_graph_db_event_link_rows
from app.datamgmt.case.case_graph_db import case_graph_db_event_rows
from app.datamgmt.case.case_graph_db import case_graph_db_evidence_rows
from app.datamgmt.case.case_graph_db import case_graph_db_existing_ids
from app.datamgmt.case.case_graph_db import case_graph_db_ioc_asset_link_rows
from app.datamgmt.case.case_graph_db import case_graph_db_ioc_cross_case_rows
from app.datamgmt.case.case_graph_db import case_graph_db_ioc_rows
from app.datamgmt.case.case_graph_db import case_graph_db_node_as_dict
from app.datamgmt.case.case_graph_db import case_graph_db_node_get
from app.datamgmt.case.case_graph_db import case_graph_db_node_rows
from app.datamgmt.case.case_graph_db import case_graph_db_note_rows
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_by_name
from app.datamgmt.case.case_graph_db import case_graph_db_relation_type_get
from app.datamgmt.case.case_graph_db import case_graph_db_relation_types
from app.datamgmt.case.case_graph_db import case_graph_db_rollback
from app.datamgmt.case.case_graph_db import case_graph_db_task_rows
from app.datamgmt.case.case_graph_db import case_graph_db_tlps
from app.datamgmt.case.case_graph_db import case_graph_db_view_get
from app.datamgmt.case.case_graph_db import case_graph_db_views_clear_report_default
from app.datamgmt.case.case_graph_db import case_graph_db_views_visible
from app.datamgmt.case.case_iocs_db import get_ioc_types_list
from app.datamgmt.manage.manage_attribute_db import get_default_custom_attributes
from app.iris_engine.case_graph_live import case_graph_live_emit
from app.iris_engine.utils.tracker import track_activity
from app.models.assets import ASSET_FLAG_COLORS
from app.models.case_graph import CASE_GRAPH_EDGE_KINDS
from app.models.case_graph import CASE_GRAPH_EDGE_ORIGINS
from app.models.case_graph import CASE_GRAPH_NODE_TYPES
from app.models.case_graph import CASE_GRAPH_REVIEW_STATES
from app.models.case_graph import CaseGraphEdge
from app.models.case_graph import CaseGraphNode
from app.models.case_graph import CaseGraphView
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.iocs import Ioc


CASE_GRAPH_EXPORT_FORMATS = ('json', 'stix', 'graphml')
CASE_GRAPH_IMPORT_FORMATS = ('json', 'stix')
CASE_GRAPH_VIEW_STATE_MAX = 1024 * 1024
CASE_GRAPH_VIEW_SNAPSHOT_MAX = 4 * 1024 * 1024
CASE_GRAPH_PASTE_MAX_VALUES = 1000
CASE_GRAPH_IMPORT_MAX_OBJECTS = 20000

_DEFAULT_MAX_NODES = 5000
_LABEL_MAX = 1024
_NAME_MAX = 255
_DESCRIPTION_MAX = 100000
_CUSTOM_ATTRIBUTES_MAX = 256 * 1024
_DEFAULT_RELATION = 'related_to'
_DEFAULT_IOC_TLP = 'amber'
_SKIPPED_DETAILS_MAX = 200
_ICON = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
_SNAPSHOT = re.compile(r'^data:image/png;base64,[A-Za-z0-9+/=\s]+$')


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _max_nodes():
    try:
        return int(current_app.config.get('CASE_GRAPH_MAX_NODES') or _DEFAULT_MAX_NODES)
    except (RuntimeError, TypeError, ValueError):
        return _DEFAULT_MAX_NODES


def _commit():
    if not case_graph_db_commit():
        raise BusinessProcessingError('The graph change conflicts with the current data')


def _emit(case_id, entity, action, key, user_id):
    case_graph_live_emit(case_id, entity, action, key=key, by_user_id=user_id)


# ------------------------------------------------------------ Validation --

def _validate_text(value, field, max_len, required=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise BusinessProcessingError(f'{field} is required')
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be a string')
    value = value.strip()
    if len(value) > max_len:
        raise BusinessProcessingError(f'{field} must be at most {max_len} characters')
    return value


def _validate_bool(value, field):
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def _validate_confidence(value):
    if value is None:
        return None
    if not _is_int(value) or not 0 <= value <= 100:
        raise BusinessProcessingError('confidence must be an integer between 0 and 100')
    return value


def _validate_datetime(value, field):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be an ISO 8601 date')
    raw = value.strip()
    if raw.endswith('Z') or raw.endswith('z'):
        raw = f'{raw[:-1]}+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError as e:
        raise BusinessProcessingError(f'{field} must be an ISO 8601 date') from e
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _validate_tlp(value, tlps):
    if value is None:
        return None
    if not _is_int(value) or value not in tlps:
        raise BusinessProcessingError('tlp_id is not a known TLP')
    return value


def _validate_custom_attributes(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BusinessProcessingError('custom_attributes must be an object')
    if len(json.dumps(value, default=str)) > _CUSTOM_ATTRIBUTES_MAX:
        raise BusinessProcessingError('custom_attributes is too large')
    return value


def _validate_color(value):
    if value is None:
        return None
    if value not in ASSET_FLAG_COLORS:
        raise BusinessProcessingError(f'color must be one of {", ".join(ASSET_FLAG_COLORS)}')
    return value


def _validate_icon(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not _ICON.match(value):
        raise BusinessProcessingError('icon must be a lucide icon name')
    return value


def _validate_endpoint(case_id, raw, field):
    try:
        kind, object_id = case_graph_parse_key(raw)
    except BusinessProcessingError as e:
        raise BusinessProcessingError(f'{field}: {e.get_message()}') from e
    if kind not in CASE_GRAPH_EDGE_KINDS:
        raise BusinessProcessingError(f'{field}: {kind} nodes cannot be linked')
    if not case_graph_db_existing_ids(case_id, kind, [object_id]):
        raise BusinessProcessingError(f'{field} {raw} is not in this case')
    return kind, object_id


def _validate_event_ids(case_id, value):
    if value is None:
        return []
    if not isinstance(value, list) or not all(_is_int(item) for item in value):
        raise BusinessProcessingError('event_ids must be a list of event ids')
    event_ids = list(dict.fromkeys(value))
    missing = set(event_ids) - case_graph_db_existing_ids(case_id, 'event', event_ids)
    if missing:
        raise BusinessProcessingError(f'events {sorted(missing)} are not in this case')
    return event_ids


def _resolve_relation(payload, required):
    relation_type_id = payload.get('relation_type_id')
    if relation_type_id is not None:
        if not _is_int(relation_type_id):
            raise BusinessProcessingError('relation_type_id must be an integer')
        relation_type = case_graph_db_relation_type_get(relation_type_id)
        if relation_type is None:
            raise BusinessProcessingError(f'Unknown relation type {relation_type_id}')
        return relation_type
    name = payload.get('relation')
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise BusinessProcessingError('relation must be a relation type name')
        relation_type = case_graph_db_relation_type_by_name(name.strip())
        if relation_type is None:
            raise BusinessProcessingError(f'Unknown relation type {name!r}')
        return relation_type
    if required:
        raise BusinessProcessingError('relation_type_id or relation is required')
    return None


# ------------------------------------------------------------------ Read --

def case_graph_load_data(case_id, include=frozenset()):
    """Plain rows `case_graph_assembly_build` needs. Kinds outside
    `include` are only loaded when a stored edge references them."""
    include = set(include or ())
    stored_edges = case_graph_db_edge_rows(case_id)
    referenced = set()
    for edge in stored_edges:
        referenced.add(edge['source_kind'])
        referenced.add(edge['target_kind'])

    def _needed(include_name):
        return include_name in include or CASE_GRAPH_INCLUDE_KINDS[include_name] in referenced

    data = {
        'case_id': case_id,
        'relation_types': case_graph_db_relation_types(),
        'tlps': case_graph_db_tlps(),
        'assets': case_graph_db_asset_rows(case_id),
        'asset_flags': case_graph_db_asset_flag_rows(case_id),
        'asset_managed': case_graph_db_asset_managed_rows(case_id),
        'iocs': case_graph_db_ioc_rows(case_id),
        'free_nodes': case_graph_db_node_rows(case_id),
        'events': case_graph_db_event_rows(case_id),
        'event_links': case_graph_db_event_link_rows(case_id),
        'ioc_asset_links': case_graph_db_ioc_asset_link_rows(case_id),
        'stored_edges': stored_edges,
        'evidences': case_graph_db_evidence_rows(case_id) if _needed('evidences') else [],
        'tasks': case_graph_db_task_rows(case_id) if _needed('tasks') else [],
        'notes': case_graph_db_note_rows(case_id) if _needed('notes') else [],
        'alerts': case_graph_db_alert_rows(case_id) if _needed('alerts') else [],
        'alert_links': [],
        'ioc_cross_cases': case_graph_db_ioc_cross_case_rows(case_id),
        'asset_cross_cases': case_graph_db_asset_cross_case_rows(case_id),
        'cases': [],
        'suggestions': [],
    }
    if 'alerts' in include:
        data['alert_links'] = case_graph_db_alert_link_rows(case_id, [row['alert_id'] for row in data['alerts']])
    if 'cross_case' in include:
        other_ids = {row['case_id'] for row in data['ioc_cross_cases']}
        other_ids.update(row['case_id'] for row in data['asset_cross_cases'])
        data['cases'] = case_graph_db_case_rows(sorted(other_ids))
    return data


def case_graph_get(case_id, include=frozenset(), review_state='accepted', readable_case_ids_resolver=None):
    """The §4.1 payload. `readable_case_ids_resolver(case_ids)` returns the
    ids among `case_ids` the caller may read (cross-case nodes of the
    others are anonymised); without it every other case is unreadable."""
    if review_state not in ('accepted', 'all'):
        raise BusinessProcessingError('review_state must be accepted or all')
    data = case_graph_load_data(case_id, include)
    readable = set()
    if 'cross_case' in (include or ()) and data['cases'] and readable_case_ids_resolver is not None:
        readable = set(readable_case_ids_resolver([row['case_id'] for row in data['cases']]))
    return case_graph_assembly_build(data, include=include, review_state=review_state, readable_case_ids=readable,
                                     generated_at=_utcnow(), max_nodes=_max_nodes())


def case_graph_neighbours(case_id, node_key, hops=1, include=frozenset(), review_state='accepted',
                          readable_case_ids_resolver=None):
    graph = case_graph_get(case_id, include, review_state, readable_case_ids_resolver)
    return case_graph_explore_neighbours(graph, node_key, hops)


def case_graph_paths(case_id, source, target, directed=True, max_paths=5, max_length=8, include=frozenset(),
                     review_state='accepted', readable_case_ids_resolver=None):
    graph = case_graph_get(case_id, include, review_state, readable_case_ids_resolver)
    return case_graph_explore_paths(graph, source, target, directed=directed, max_paths=max_paths,
                                    max_length=max_length)


# ---------------------------------------------------------- Stored edges --

def _relation_types_by_id():
    return {row['id']: row for row in (case_graph_relation_type_serialize(relation_type)
                                       for relation_type in case_graph_db_relation_types())}


def _edge_payload(case_id, edge):
    event_ids = case_graph_db_edge_event_ids([edge.edge_id]).get(edge.edge_id, [])
    row = case_graph_db_edge_as_dict(edge, event_ids)
    return case_graph_stored_edge_serialize(row, _relation_types_by_id(), case_graph_db_tlps(),
                                            case_graph_db_event_dates(case_id, event_ids))


def _get_edge(case_id, edge_id):
    edge = case_graph_db_edge_get(case_id, edge_id)
    if edge is None:
        raise ObjectNotFoundError()
    return edge


def _edge_label(edge, relation_name):
    arrow = '->' if edge.directed else '--'
    return (f'{case_graph_key(edge.source_kind, edge.source_id)} {arrow} '
            f'{case_graph_key(edge.target_kind, edge.target_id)} ({relation_name})')


def _find_duplicate(case_id, source, target, relation_type_id, directed, exclude_edge_id=None):
    """A stored edge linking the same pair with the same relation (an
    undirected one either way round)."""
    for edge in case_graph_db_edges_touching(case_id, source[0], [source[1]]):
        if edge.edge_id == exclude_edge_id or edge.relation_type_id != relation_type_id:
            continue
        forward = (edge.source_kind, edge.source_id) == source and (edge.target_kind, edge.target_id) == target
        backward = (edge.source_kind, edge.source_id) == target and (edge.target_kind, edge.target_id) == source
        if forward or (backward and not directed and not edge.directed):
            return edge
    return None


def _apply_edge_fields(case_id, edge, payload, tlps):
    if 'label' in payload:
        edge.label = _validate_text(payload['label'], 'label', _LABEL_MAX)
    if 'description' in payload:
        edge.description = _validate_text(payload['description'], 'description', _DESCRIPTION_MAX)
    if 'confidence' in payload:
        edge.confidence = _validate_confidence(payload['confidence'])
    if 'first_seen' in payload:
        edge.first_seen = _validate_datetime(payload['first_seen'], 'first_seen')
    if 'last_seen' in payload:
        edge.last_seen = _validate_datetime(payload['last_seen'], 'last_seen')
    if edge.first_seen and edge.last_seen and edge.first_seen > edge.last_seen:
        raise BusinessProcessingError('first_seen must not be after last_seen')
    if 'tlp_id' in payload:
        edge.tlp_id = _validate_tlp(payload['tlp_id'], tlps)
    if 'custom_attributes' in payload:
        edge.custom_attributes = _validate_custom_attributes(payload['custom_attributes'])


def _edge_new(case_id, user_id, source, target, relation_type, directed, origin, review_state):
    if source == target:
        raise BusinessProcessingError('source and target must differ')
    edge = CaseGraphEdge()
    edge.case_id = case_id
    edge.source_kind, edge.source_id = source
    edge.target_kind, edge.target_id = target
    edge.relation_type_id = relation_type.id
    edge.directed = directed
    edge.origin = origin
    edge.review_state = review_state
    edge.created_by_id = user_id
    now = _utcnow()
    edge.created_at = now
    edge.updated_at = now
    return edge


def case_graph_edge_create(case_id, user_id, payload, origin='manual', review_state='accepted'):
    """Create a stored edge (§4.3); the §4.1 edge. `origin` and
    `review_state` are set by the caller (`ai` / `pending`, `suggestion`,
    `module`…), never by the request body."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    if origin not in CASE_GRAPH_EDGE_ORIGINS:
        raise BusinessProcessingError(f'Unknown edge origin {origin!r}')
    if review_state not in CASE_GRAPH_REVIEW_STATES:
        raise BusinessProcessingError(f'Unknown review state {review_state!r}')
    source = _validate_endpoint(case_id, payload.get('source'), 'source')
    target = _validate_endpoint(case_id, payload.get('target'), 'target')
    relation_type = _resolve_relation(payload, required=True)
    directed = payload.get('directed')
    directed = relation_type.directed if directed is None else _validate_bool(directed, 'directed')
    if _find_duplicate(case_id, source, target, relation_type.id, directed) is not None:
        raise BusinessProcessingError('An edge with the same endpoints and relation already exists')
    event_ids = _validate_event_ids(case_id, payload.get('event_ids'))

    edge = _edge_new(case_id, user_id, source, target, relation_type, bool(directed), origin, review_state)
    _apply_edge_fields(case_id, edge, payload, case_graph_db_tlps())
    case_graph_db_add(edge)
    case_graph_db_edge_set_events(edge.edge_id, event_ids)
    _commit()

    track_activity(f'added graph edge {_edge_label(edge, relation_type.name)}', caseid=case_id)
    _emit(case_id, 'edge', 'created', f'e:{edge.edge_id}', user_id)
    return _edge_payload(case_id, edge)


def case_graph_edge_update(case_id, user_id, edge_id, payload):
    """Partial update of a stored edge (§4.3)."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    edge = _get_edge(case_id, edge_id)
    if 'source' in payload:
        edge.source_kind, edge.source_id = _validate_endpoint(case_id, payload['source'], 'source')
    if 'target' in payload:
        edge.target_kind, edge.target_id = _validate_endpoint(case_id, payload['target'], 'target')
    if (edge.source_kind, edge.source_id) == (edge.target_kind, edge.target_id):
        raise BusinessProcessingError('source and target must differ')
    relation_type = _resolve_relation(payload, required=False)
    if relation_type is not None:
        edge.relation_type_id = relation_type.id
    else:
        relation_type = case_graph_db_relation_type_get(edge.relation_type_id)
    if 'directed' in payload:
        edge.directed = _validate_bool(payload['directed'], 'directed')
    if 'review_state' in payload:
        if payload['review_state'] not in CASE_GRAPH_REVIEW_STATES:
            raise BusinessProcessingError(f'review_state must be one of {", ".join(CASE_GRAPH_REVIEW_STATES)}')
        edge.review_state = payload['review_state']
    _apply_edge_fields(case_id, edge, payload, case_graph_db_tlps())
    if _find_duplicate(case_id, (edge.source_kind, edge.source_id), (edge.target_kind, edge.target_id),
                       edge.relation_type_id, edge.directed, exclude_edge_id=edge.edge_id) is not None:
        case_graph_db_rollback()
        raise BusinessProcessingError('An edge with the same endpoints and relation already exists')
    if 'event_ids' in payload:
        case_graph_db_edge_set_events(edge.edge_id, _validate_event_ids(case_id, payload['event_ids']))
    edge.updated_at = _utcnow()
    _commit()

    track_activity(f'updated graph edge {_edge_label(edge, relation_type.name)}', caseid=case_id)
    _emit(case_id, 'edge', 'updated', f'e:{edge.edge_id}', user_id)
    return _edge_payload(case_id, edge)


def case_graph_edge_delete(case_id, user_id, edge_id):
    edge = _get_edge(case_id, edge_id)
    relation_type = case_graph_db_relation_type_get(edge.relation_type_id)
    label = _edge_label(edge, relation_type.name if relation_type else edge.relation_type_id)
    case_graph_db_delete(edge)
    _commit()
    track_activity(f'deleted graph edge {label}', caseid=case_id)
    _emit(case_id, 'edge', 'deleted', f'e:{edge_id}', user_id)


def case_graph_edge_review(case_id, user_id, edge_id, decision):
    """`accept` / `reject` a (typically pending) edge."""
    states = {'accept': 'accepted', 'reject': 'rejected'}
    if decision not in states:
        raise BusinessProcessingError('decision must be accept or reject')
    edge = _get_edge(case_id, edge_id)
    edge.review_state = states[decision]
    edge.updated_at = _utcnow()
    _commit()
    relation_type = case_graph_db_relation_type_get(edge.relation_type_id)
    track_activity(f'{states[decision]} graph edge {_edge_label(edge, relation_type.name)}', caseid=case_id)
    _emit(case_id, 'edge', 'updated', f'e:{edge.edge_id}', user_id)
    return _edge_payload(case_id, edge)


def case_graph_edge_reverse(case_id, user_id, edge_id):
    """Swap the source and the target of a stored edge."""
    edge = _get_edge(case_id, edge_id)
    source, target = (edge.source_kind, edge.source_id), (edge.target_kind, edge.target_id)
    if _find_duplicate(case_id, target, source, edge.relation_type_id, edge.directed,
                       exclude_edge_id=edge.edge_id) is not None:
        raise BusinessProcessingError('The reversed edge already exists')
    edge.source_kind, edge.source_id = target
    edge.target_kind, edge.target_id = source
    edge.updated_at = _utcnow()
    _commit()
    relation_type = case_graph_db_relation_type_get(edge.relation_type_id)
    track_activity(f'reversed graph edge {_edge_label(edge, relation_type.name)}', caseid=case_id)
    _emit(case_id, 'edge', 'updated', f'e:{edge.edge_id}', user_id)
    return _edge_payload(case_id, edge)


# ------------------------------------------------------------ Free nodes --

def _get_node(case_id, node_id):
    node = case_graph_db_node_get(case_id, node_id)
    if node is None:
        raise ObjectNotFoundError()
    return node


def _node_payload(node):
    return case_graph_assembly_free_node(case_graph_db_node_as_dict(node), case_graph_db_tlps())


def _apply_node_fields(node, payload, creating):
    if creating or 'label' in payload:
        node.label = _validate_text(payload.get('label'), 'label', _LABEL_MAX, required=True)
    if creating or 'node_type' in payload:
        node_type = payload.get('node_type') or 'other'
        if node_type not in CASE_GRAPH_NODE_TYPES:
            raise BusinessProcessingError(f'node_type must be one of {", ".join(CASE_GRAPH_NODE_TYPES)}')
        node.node_type = node_type
    if 'description' in payload:
        node.description = _validate_text(payload['description'], 'description', _DESCRIPTION_MAX)
    if 'icon' in payload:
        node.icon = _validate_icon(payload['icon'])
    if 'color' in payload:
        node.color = _validate_color(payload['color'])
    if 'tlp_id' in payload:
        node.tlp_id = _validate_tlp(payload['tlp_id'], case_graph_db_tlps())
    if 'custom_attributes' in payload:
        node.custom_attributes = _validate_custom_attributes(payload['custom_attributes'])


def _node_new(case_id, user_id, payload):
    node = CaseGraphNode()
    node.case_id = case_id
    node.created_by_id = user_id
    now = _utcnow()
    node.created_at = now
    node.updated_at = now
    _apply_node_fields(node, payload, creating=True)
    return case_graph_db_add(node)


def case_graph_node_get(case_id, node_id):
    """§4.1 node of a free node."""
    return _node_payload(_get_node(case_id, node_id))


def case_graph_node_create(case_id, user_id, payload):
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    node = _node_new(case_id, user_id, payload)
    _commit()
    track_activity(f'added graph node "{node.label}"', caseid=case_id)
    _emit(case_id, 'node', 'created', f'node:{node.node_id}', user_id)
    return _node_payload(node)


def case_graph_node_update(case_id, user_id, node_id, payload):
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    node = _get_node(case_id, node_id)
    _apply_node_fields(node, payload, creating=False)
    node.updated_at = _utcnow()
    _commit()
    track_activity(f'updated graph node "{node.label}"', caseid=case_id)
    _emit(case_id, 'node', 'updated', f'node:{node.node_id}', user_id)
    return _node_payload(node)


def case_graph_node_delete(case_id, user_id, node_id):
    """Delete a free node and the stored edges touching it."""
    node = _get_node(case_id, node_id)
    label = node.label
    case_graph_db_delete_edges_touching('node', [node_id], case_id=case_id)
    case_graph_db_delete(node)
    _commit()
    track_activity(f'deleted graph node "{label}"', caseid=case_id)
    _emit(case_id, 'node', 'deleted', f'node:{node_id}', user_id)


def case_graph_node_promote(case_id, user, node_id, kind, case_object, ioc_links=None):
    """Turn a free node into the asset / IOC `case_object` (built by the
    blueprint from the request, as the asset / IOC routes do): create it
    with the asset / IOC business functions, move every stored edge of
    `node:<id>` onto it, delete the free node. `{key, node}`."""
    node = _get_node(case_id, node_id)
    if kind == 'asset':
        created = assets_create(user, case_id, case_object, ioc_links)
        new_endpoint = ('asset', created.asset_id)
    elif kind == 'ioc':
        case_object.case_id = case_id
        created = iocs_create(case_object)
        new_endpoint = ('ioc', created.ioc_id)
    else:
        raise BusinessProcessingError('kind must be asset or ioc')

    now = _utcnow()
    for edge in case_graph_db_edges_touching(case_id, 'node', [node_id]):
        if edge.source_kind == 'node' and edge.source_id == node_id:
            edge.source_kind, edge.source_id = new_endpoint
        if edge.target_kind == 'node' and edge.target_id == node_id:
            edge.target_kind, edge.target_id = new_endpoint
        edge.updated_at = now
    label = node.label
    case_graph_db_delete(node)
    _commit()

    new_key = case_graph_key(*new_endpoint)
    track_activity(f'promoted graph node "{label}" to {new_key}', caseid=case_id)
    _emit(case_id, 'node', 'deleted', f'node:{node_id}', user.id)
    graph = case_graph_get(case_id)
    promoted = next((item for item in graph['nodes'] if item['key'] == new_key), None)
    return {'key': new_key, 'node': promoted}


# ----------------------------------------------------------------- Paste --

def _default_ioc_attributes():
    try:
        return get_default_custom_attributes('ioc') or {}
    except Exception:
        return {}


def _default_ioc_tlp(tlps):
    return next((tlp_id for tlp_id, name in tlps.items() if (name or '').lower() == _DEFAULT_IOC_TLP), None)


def _create_ioc(case_id, value, ioc_type_id, tlp_id, description=None):
    ioc = Ioc()
    ioc.ioc_value = value
    ioc.ioc_type_id = ioc_type_id
    ioc.ioc_tlp_id = tlp_id
    ioc.ioc_description = description or ''
    ioc.ioc_tags = ''
    ioc.case_id = case_id
    ioc.custom_attributes = _default_ioc_attributes()
    return iocs_create(ioc)


def case_graph_paste(case_id, user_id, payload):
    """§4.5: extract indicators from free text, create the missing IOCs,
    optionally link every IOC to `link_to`."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    text = payload.get('text')
    if not isinstance(text, str) or not text.strip():
        raise BusinessProcessingError('text is required')
    if len(text) > CASE_GRAPH_PASTE_MAX_TEXT:
        raise BusinessProcessingError(f'text must be at most {CASE_GRAPH_PASTE_MAX_TEXT} characters')
    tlps = case_graph_db_tlps()
    tlp_id = payload.get('ioc_tlp_id')
    tlp_id = _default_ioc_tlp(tlps) if tlp_id is None else _validate_tlp(tlp_id, tlps)
    link_to = payload.get('link_to')
    link_endpoint = _validate_endpoint(case_id, link_to, 'link_to') if link_to else None
    relation_type = None
    if link_endpoint is not None:
        relation_type = _resolve_relation({'relation': payload.get('relation') or _DEFAULT_RELATION}, required=True)

    values = case_graph_paste_extract(text)
    if len(values) > CASE_GRAPH_PASTE_MAX_VALUES:
        raise BusinessProcessingError(f'At most {CASE_GRAPH_PASTE_MAX_VALUES} indicators can be pasted at once')
    plan = case_graph_paste_plan(values, get_ioc_types_list(), case_graph_db_ioc_rows(case_id))

    created = []
    for value, ioc_type in plan['create']:
        ioc = _create_ioc(case_id, value, ioc_type['type_id'], tlp_id)
        created.append(case_graph_key('ioc', ioc.ioc_id))
    existing = [case_graph_key('ioc', ioc_id) for ioc_id in plan['existing']]

    edges = []
    if link_endpoint is not None:
        for key in created + existing:
            target = case_graph_parse_key(key)
            if target == link_endpoint or _find_duplicate(case_id, link_endpoint, target, relation_type.id,
                                                          relation_type.directed) is not None:
                continue
            edge = _edge_new(case_id, user_id, link_endpoint, target, relation_type, bool(relation_type.directed),
                             'manual', 'accepted')
            case_graph_db_add(edge)
            edges.append(edge)
        _commit()
        if edges:
            track_activity(f'linked {len(edges)} pasted IOCs to {link_to} in the graph', caseid=case_id)
            _emit(case_id, 'edge', 'created', None, user_id)

    return {'created': created, 'existing': existing, 'edges': [_edge_payload(case_id, edge) for edge in edges],
            'ignored': plan['ignored']}


# ----------------------------------------------------------------- Views --

def _view_payload(view, with_snapshot):
    payload = {
        'view_id': view.view_id,
        'view_uuid': str(view.view_uuid) if view.view_uuid else None,
        'case_id': view.case_id,
        'name': view.name,
        'description': view.description,
        'owner_id': view.owner_id,
        'is_shared': bool(view.is_shared),
        'is_report_default': bool(view.is_report_default),
        'state': view.state if view.state is not None else {},
        'created_at': case_graph_isoformat(view.created_at),
        'updated_at': case_graph_isoformat(view.updated_at),
    }
    if with_snapshot:
        payload['snapshot'] = view.snapshot
    return payload


def _get_view(case_id, user_id, view_id):
    """A view the user may see (own or shared), else ObjectNotFoundError."""
    view = case_graph_db_view_get(case_id, view_id)
    if view is None or (view.owner_id != user_id and not view.is_shared):
        raise ObjectNotFoundError()
    return view


def _validate_state(value):
    if not isinstance(value, dict):
        raise BusinessProcessingError('state must be an object')
    if len(json.dumps(value, default=str)) > CASE_GRAPH_VIEW_STATE_MAX:
        raise BusinessProcessingError('state must be at most 1 MB')
    return value


def _validate_snapshot(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not _SNAPSHOT.match(value):
        raise BusinessProcessingError('snapshot must be a PNG data URL')
    if len(value) > CASE_GRAPH_VIEW_SNAPSHOT_MAX:
        raise BusinessProcessingError('snapshot must be at most 4 MB')
    return value


def _apply_view_fields(view, payload, creating, is_owner):
    if creating or 'name' in payload:
        view.name = _validate_text(payload.get('name'), 'name', _NAME_MAX, required=True)
    if 'description' in payload:
        view.description = _validate_text(payload['description'], 'description', _DESCRIPTION_MAX)
    if 'is_shared' in payload:
        is_shared = _validate_bool(payload['is_shared'], 'is_shared')
        if not is_owner and is_shared != bool(view.is_shared):
            raise BusinessProcessingError('Only the owner of a view can change its sharing')
        view.is_shared = is_shared
    if creating or 'state' in payload:
        view.state = _validate_state(payload.get('state', {}) if creating else payload['state'])
    if 'snapshot' in payload:
        view.snapshot = _validate_snapshot(payload['snapshot'])
    if 'is_report_default' in payload:
        view.is_report_default = _validate_bool(payload['is_report_default'], 'is_report_default')
    if not view.is_shared:
        if 'is_report_default' in payload and view.is_report_default:
            raise BusinessProcessingError('Only a shared view can be the report default')
        view.is_report_default = False


def case_graph_views_list(case_id, user_id):
    """Views the user sees (own + shared), without snapshots."""
    return [_view_payload(view, with_snapshot=False) for view in case_graph_db_views_visible(case_id, user_id)]


def case_graph_view_get(case_id, user_id, view_id):
    return _view_payload(_get_view(case_id, user_id, view_id), with_snapshot=True)


def case_graph_view_create(case_id, user_id, payload):
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    view = CaseGraphView()
    view.case_id = case_id
    view.owner_id = user_id
    view.is_shared = False
    view.is_report_default = False
    now = _utcnow()
    view.created_at = now
    view.updated_at = now
    _apply_view_fields(view, payload, creating=True, is_owner=True)
    case_graph_db_add(view)
    if view.is_report_default:
        case_graph_db_views_clear_report_default(case_id, except_view_id=view.view_id)
    _commit()
    track_activity(f'added graph view "{view.name}"', caseid=case_id)
    _emit(case_id, 'view', 'created', f'view:{view.view_id}', user_id)
    return _view_payload(view, with_snapshot=True)


def case_graph_view_update(case_id, user_id, view_id, payload):
    """Owner, or anyone (with full access) for a shared view."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    view = _get_view(case_id, user_id, view_id)
    _apply_view_fields(view, payload, creating=False, is_owner=view.owner_id == user_id)
    view.updated_at = _utcnow()
    if view.is_report_default:
        case_graph_db_views_clear_report_default(case_id, except_view_id=view.view_id)
    _commit()
    track_activity(f'updated graph view "{view.name}"', caseid=case_id)
    _emit(case_id, 'view', 'updated', f'view:{view.view_id}', user_id)
    return _view_payload(view, with_snapshot=True)


def case_graph_view_delete(case_id, user_id, view_id):
    view = _get_view(case_id, user_id, view_id)
    name = view.name
    case_graph_db_delete(view)
    _commit()
    track_activity(f'deleted graph view "{name}"', caseid=case_id)
    _emit(case_id, 'view', 'deleted', f'view:{view_id}', user_id)


# --------------------------------------------------------- Import/export --

def case_graph_export(case_id, user_id, export_format, include=frozenset(), readable_case_ids_resolver=None):
    """`{format, filename, content}`: content is the §4.1 payload + views
    (`json`), a STIX 2.1 bundle (`stix`) or a GraphML document (`graphml`,
    a string)."""
    if export_format not in CASE_GRAPH_EXPORT_FORMATS:
        raise BusinessProcessingError(f'format must be one of {", ".join(CASE_GRAPH_EXPORT_FORMATS)}')
    graph = case_graph_get(case_id, include, 'accepted', readable_case_ids_resolver)
    if export_format == 'json':
        content = dict(graph, views=case_graph_views_list(case_id, user_id))
        extension = 'json'
    elif export_format == 'stix':
        content = case_graph_exchange_to_stix(graph, graph['relation_types'], case_id,
                                              graph['meta']['generated_at'])
        extension = 'stix.json'
    else:
        content = case_graph_exchange_to_graphml(graph)
        extension = 'graphml'
    return {'format': export_format, 'filename': f'case-{case_id}-graph.{extension}', 'content': content}


class _Importer:
    """Resolves an import plan against the case and creates what is
    missing. IOCs go through `iocs_create` (committed one by one, as any
    IOC creation); free nodes and edges are committed together."""

    def __init__(self, case_id, user_id):
        self._case_id = case_id
        self._user_id = user_id
        self._refs = {}
        self.nodes_created = 0
        self.edges_created = 0
        self.skipped = []
        self._tlps = case_graph_db_tlps()
        self._ioc_types = get_ioc_types_list()
        self._iocs = {(row.get('ioc_value') or '').strip().lower(): row['ioc_id']
                      for row in case_graph_db_ioc_rows(case_id)}
        self._assets = {}
        for row in case_graph_db_asset_rows(case_id):
            self._assets.setdefault((row.get('asset_name') or '').strip().lower(), row['asset_id'])
        self._nodes = {((row.get('label') or '').strip().lower(), row.get('node_type')): row['node_id']
                       for row in case_graph_db_node_rows(case_id)}
        self._relation_types = case_graph_db_relation_types()

    def _skip(self, reason):
        self.skipped.append(reason)

    def _ioc_type(self, value, type_hint):
        if isinstance(type_hint, str) and type_hint:
            for ioc_type in self._ioc_types:
                if (ioc_type.get('type_name') or '').lower() == type_hint.lower():
                    return ioc_type
        return ioc_type_detect_pick(value, self._ioc_types)

    def _free_node(self, ref, label, node_type, description=None, icon=None, color=None):
        node_type = node_type if node_type in CASE_GRAPH_NODE_TYPES else 'other'
        existing = self._nodes.get((label.lower(), node_type))
        if existing is not None:
            self._refs[ref] = ('node', existing)
            return
        payload = {'label': label[:_LABEL_MAX], 'node_type': node_type,
                   'description': description if isinstance(description, str) else None}
        if isinstance(icon, str) and _ICON.match(icon):
            payload['icon'] = icon
        if color in ASSET_FLAG_COLORS:
            payload['color'] = color
        node = _node_new(self._case_id, self._user_id, payload)
        self._nodes[(label.lower(), node_type)] = node.node_id
        self._refs[ref] = ('node', node.node_id)
        self.nodes_created += 1

    def iocs(self, items):
        tlp_id = _default_ioc_tlp(self._tlps)
        for item in items:
            value = (item.get('value') or '').strip()
            if not value:
                self._skip(f'{item.get("ref")}: empty value')
                continue
            existing = self._iocs.get(value.lower())
            if existing is not None:
                self._refs[item['ref']] = ('ioc', existing)
                continue
            ioc_type = self._ioc_type(value, item.get('type_hint'))
            if ioc_type is None:
                self._skip(f'{item.get("ref")}: no IOC type for {value!r}')
                continue
            description = item.get('description') if isinstance(item.get('description'), str) else None
            ioc = _create_ioc(self._case_id, value, ioc_type['type_id'], tlp_id, description)
            self._iocs[value.lower()] = ioc.ioc_id
            self._refs[item['ref']] = ('ioc', ioc.ioc_id)
            self.nodes_created += 1

    def assets(self, items):
        """Matched by name against the case assets; an unknown asset
        becomes an `infrastructure` free node (assets need a type)."""
        for item in items:
            label = (item.get('label') or '').strip()
            if not label:
                self._skip(f'{item.get("ref")}: empty asset name')
                continue
            existing = self._assets.get(label.lower())
            if existing is not None:
                self._refs[item['ref']] = ('asset', existing)
                continue
            self._free_node(item['ref'], label, 'infrastructure', item.get('description'))

    def nodes(self, items):
        for item in items:
            label = (item.get('label') or '').strip()
            if not label:
                self._skip(f'{item.get("ref")}: empty label')
                continue
            self._free_node(item['ref'], label, item.get('node_type'), item.get('description'), item.get('icon'),
                            item.get('color'))

    def _relation(self, item):
        by_name = {relation_type.name: relation_type for relation_type in self._relation_types}
        if isinstance(item.get('relation'), str) and item['relation'] in by_name:
            return by_name[item['relation']]
        stix = item.get('stix_relationship')
        if isinstance(stix, str):
            for relation_type in self._relation_types:
                if relation_type.stix_relationship == stix:
                    return relation_type
        return by_name.get(_DEFAULT_RELATION)

    def edges(self, items):
        for item in items:
            source = self._refs.get(item.get('source_ref'))
            target = self._refs.get(item.get('target_ref'))
            if source is None or target is None or source == target:
                self._skip(f'{item.get("ref")}: endpoint not imported')
                continue
            relation_type = self._relation(item)
            if relation_type is None:
                self._skip(f'{item.get("ref")}: no relation type')
                continue
            directed = item.get('directed')
            directed = relation_type.directed if not isinstance(directed, bool) else directed
            if _find_duplicate(self._case_id, source, target, relation_type.id, directed) is not None:
                self._skip(f'{item.get("ref")}: edge already exists')
                continue
            edge = _edge_new(self._case_id, self._user_id, source, target, relation_type, bool(directed),
                             'import', 'accepted')
            fields = {}
            for name, validate in (('label', lambda v: _validate_text(v, 'label', _LABEL_MAX)),
                                   ('description', lambda v: _validate_text(v, 'description', _DESCRIPTION_MAX)),
                                   ('confidence', _validate_confidence),
                                   ('first_seen', lambda v: _validate_datetime(v, 'first_seen')),
                                   ('last_seen', lambda v: _validate_datetime(v, 'last_seen'))):
                try:
                    fields[name] = validate(item.get(name))
                except BusinessProcessingError:
                    fields[name] = None
            for name, value in fields.items():
                setattr(edge, name, value)
            case_graph_db_add(edge)
            self.edges_created += 1


def case_graph_import(case_id, user_id, payload):
    """§4.7 import; `{nodes_created, edges_created, skipped, skipped_details}`."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('A JSON object is required')
    import_format = payload.get('format')
    if import_format not in CASE_GRAPH_IMPORT_FORMATS:
        raise BusinessProcessingError(f'format must be one of {", ".join(CASE_GRAPH_IMPORT_FORMATS)}')
    if import_format == 'json':
        plan = case_graph_exchange_parse_json(payload.get('content'))
    else:
        plan = case_graph_exchange_parse_stix(payload.get('content'))
    total = sum(len(plan[name]) for name in ('iocs', 'nodes', 'assets', 'edges'))
    if total > CASE_GRAPH_IMPORT_MAX_OBJECTS:
        raise BusinessProcessingError(f'At most {CASE_GRAPH_IMPORT_MAX_OBJECTS} objects can be imported at once')

    importer = _Importer(case_id, user_id)
    importer.iocs(plan['iocs'])
    importer.assets(plan['assets'])
    importer.nodes(plan['nodes'])
    importer.edges(plan['edges'])
    _commit()

    skipped = [f'not imported: {item}' for item in plan['skipped']] + importer.skipped
    track_activity(f'imported a {import_format} graph: {importer.nodes_created} nodes and '
                   f'{importer.edges_created} edges created', caseid=case_id)
    if importer.nodes_created or importer.edges_created:
        _emit(case_id, 'edge', 'created', None, user_id)
    return {'nodes_created': importer.nodes_created, 'edges_created': importer.edges_created,
            'skipped': len(skipped), 'skipped_details': skipped[:_SKIPPED_DETAILS_MAX]}
