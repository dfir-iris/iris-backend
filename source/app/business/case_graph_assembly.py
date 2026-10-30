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

"""Assembly of the investigation graph payload (`GET /graph`, §4.1).

Pure: `case_graph_assembly_build` takes the plain rows read by
`app.business.case_graph.case_graph_load_data` and returns the payload.
Nodes are matched by id, never by name; nothing is capped.
"""

from app.business.case_graph_derive import ORIGIN_ALERT
from app.business.case_graph_derive import ORIGIN_CROSS_CASE
from app.business.case_graph_derive import case_graph_derive_edges
from app.business.case_graph_derive import case_graph_derived_edge_key
from app.business.case_graph_derive import case_graph_event_in_graph
from app.business.case_graph_derive import case_graph_key
from app.business.case_graph_derive import case_graph_merge_edges


CASE_GRAPH_INCLUDES = ('events', 'evidences', 'tasks', 'notes', 'alerts', 'cross_case', 'suggestions')

# include value -> node kind it adds
CASE_GRAPH_INCLUDE_KINDS = {
    'events': 'event',
    'evidences': 'evidence',
    'tasks': 'task',
    'notes': 'note',
    'alerts': 'alert',
}

_COMPROMISED = 1
_ICON_PATH = '/static/assets/img/graph/'
_RESTRICTED_CASE_LABEL = 'Restricted case'
_SCORE_KEYS = ('score', 'risk_score', 'malicious_score', 'reputation', 'threat_score')


def case_graph_isoformat(value):
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def _split_tags(raw):
    if not raw:
        return []
    return [tag.strip() for tag in str(raw).split(',') if tag.strip()]


def _find_score(value, depth=0):
    if not isinstance(value, dict) or depth > 2:
        return None
    for key in _SCORE_KEYS:
        score = value.get(key)
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return score
    for nested in value.values():
        score = _find_score(nested, depth + 1)
        if score is not None:
            return score
    return None


def case_graph_enrichment_summary(enrichment):
    """`{sources, score?}` of an enrichment blob, None when empty."""
    if not isinstance(enrichment, dict) or not enrichment:
        return None
    summary = {'sources': sorted(str(key) for key in enrichment.keys())}
    score = _find_score(enrichment)
    if score is not None:
        summary['score'] = score
    return summary


def case_graph_relation_type_serialize(relation_type):
    """Plain dict of a relation type row (model or dict)."""
    get = relation_type.get if isinstance(relation_type, dict) else (
        lambda name: getattr(relation_type, name, None))
    return {
        'id': get('id'),
        'name': get('name'),
        'label': get('label'),
        'description': get('description'),
        'directed': bool(get('directed')),
        'color': get('color'),
        'line_style': get('line_style'),
        'stix_relationship': get('stix_relationship'),
        'is_system': bool(get('is_system')),
        'sort_order': get('sort_order') or 0,
    }


def _relation_ref(relation_type, name):
    if relation_type is None:
        return {'id': None, 'name': name, 'label': name, 'color': 'slate', 'line_style': 'solid'}
    return {'id': relation_type['id'], 'name': relation_type['name'], 'label': relation_type['label'],
            'color': relation_type['color'], 'line_style': relation_type['line_style']}


def _node(kind, object_id, label, **fields):
    node = {
        'key': case_graph_key(kind, object_id), 'kind': kind, 'id': object_id,
        'label': label, 'subtitle': None, 'type': None, 'icon': None,
        'compromised': None, 'analysis_status': None, 'flags': [], 'tags': [],
        'tlp': None, 'criticality': None, 'description': None,
        'first_seen': None, 'last_seen': None, 'event_count': 0,
        'cross_case_count': 0, 'enrichment': None, 'url': None,
    }
    node.update(fields)
    return node


def _asset_node(case_id, row, flags, criticality):
    compromised = row.get('asset_compromise_status_id') == _COMPROMISED
    icon_file = row.get('asset_icon_compromised') if compromised else row.get('asset_icon_not_compromised')
    return _node(
        'asset', row['asset_id'], row.get('asset_name') or f'Asset #{row["asset_id"]}',
        subtitle=row.get('asset_ip') or row.get('asset_domain') or None,
        type=row.get('type_name'),
        icon=f'{_ICON_PATH}{icon_file}' if icon_file else None,
        compromised=compromised,
        analysis_status=row.get('analysis_status'),
        flags=flags,
        tags=_split_tags(row.get('asset_tags')),
        criticality=criticality,
        description=row.get('asset_description'),
        enrichment=case_graph_enrichment_summary(row.get('asset_enrichment')),
        url=f'/case/{case_id}/assets/{row["asset_id"]}',
    )


def _ioc_node(case_id, row):
    return _node(
        'ioc', row['ioc_id'], row.get('ioc_value') or f'IOC #{row["ioc_id"]}',
        subtitle=row.get('type_name'),
        type=row.get('type_name'),
        tags=_split_tags(row.get('ioc_tags')),
        tlp=row.get('tlp_name'),
        description=row.get('ioc_description'),
        enrichment=case_graph_enrichment_summary(row.get('ioc_enrichment')),
        url=f'/case/{case_id}/iocs/{row["ioc_id"]}',
    )


def case_graph_assembly_free_node(row, tlps):
    """§4.1 node of a free node row (`case_graph_db_node_as_dict`)."""
    return _node(
        'node', row['node_id'], row.get('label') or f'Node #{row["node_id"]}',
        subtitle=row.get('node_type'),
        type=row.get('node_type'),
        icon=row.get('icon'),
        color=row.get('color'),
        tlp=tlps.get(row.get('tlp_id')),
        tlp_id=row.get('tlp_id'),
        description=row.get('description'),
        custom_attributes=row.get('custom_attributes'),
    )


def _event_node(case_id, row):
    date = case_graph_isoformat(row.get('event_date'))
    return _node(
        'event', row['event_id'], row.get('event_title') or f'Event #{row["event_id"]}',
        subtitle=date,
        type=row.get('category_name'),
        color=row.get('event_color'),
        tags=_split_tags(row.get('event_tags')),
        first_seen=date, last_seen=date, event_count=1,
        url=f'/case/{case_id}/timeline?sharedEventId={row["event_id"]}',
    )


def _evidence_node(case_id, row):
    return _node(
        'evidence', row['id'], row.get('filename') or f'Evidence #{row["id"]}',
        subtitle=row.get('type_name') or row.get('file_hash'),
        type=row.get('type_name'),
        description=row.get('file_description'),
        url=f'/case/{case_id}/evidence/{row["id"]}',
    )


def _task_node(case_id, row):
    return _node(
        'task', row['id'], row.get('task_title') or f'Task #{row["id"]}',
        subtitle=row.get('status_name'),
        type=row.get('status_name'),
        tags=_split_tags(row.get('task_tags')),
        description=row.get('task_description'),
        url=f'/case/{case_id}/tasks/{row["id"]}',
    )


def _note_node(case_id, row):
    return _node(
        'note', row['note_id'], row.get('note_title') or f'Note #{row["note_id"]}',
        subtitle=case_graph_isoformat(row.get('note_lastupdate') or row.get('note_creationdate')),
        url=f'/case/{case_id}/notes/{row["note_id"]}',
    )


def _alert_node(row):
    date = case_graph_isoformat(row.get('alert_source_event_time'))
    return _node(
        'alert', row['alert_id'], row.get('alert_title') or f'Alert #{row["alert_id"]}',
        subtitle=row.get('severity_name'),
        type=row.get('severity_name'),
        tags=_split_tags(row.get('alert_tags')),
        first_seen=date, last_seen=date,
        url=f'/alerts/{row["alert_id"]}',
    )


def case_graph_assembly_case_node(case_id, name, accessible):
    return _node('case', case_id, (name or f'Case #{case_id}') if accessible else _RESTRICTED_CASE_LABEL,
                 accessible=accessible, url=f'/case/{case_id}' if accessible else None)


def _account_keys(assets, iocs):
    keys = set()
    for row in assets:
        if 'account' in (row.get('type_name') or '').lower():
            keys.add(case_graph_key('asset', row['asset_id']))
    for row in iocs:
        if 'account' in (row.get('type_name') or '').lower():
            keys.add(case_graph_key('ioc', row['ioc_id']))
    return keys


def _stamp_entity_activity(nodes_by_key, events_by_id, event_links):
    seen = set()
    for link in event_links:
        event = events_by_id.get(link['event_id'])
        node = nodes_by_key.get(case_graph_key(link['kind'], link['object_id']))
        if event is None or node is None or (node['key'], event['event_id']) in seen:
            continue
        seen.add((node['key'], event['event_id']))
        node['event_count'] += 1
        date = event.get('event_date')
        if date is None:
            continue
        if node['_first'] is None or date < node['_first']:
            node['_first'] = date
        if node['_last'] is None or date > node['_last']:
            node['_last'] = date


def _cross_cases(rows, kind):
    result = {}
    for row in rows:
        result.setdefault(case_graph_key(kind, row['object_id']), set()).add(row['case_id'])
    return result


def _edge_dates(edge, events_by_id):
    dates = [events_by_id[event_id]['event_date'] for event_id in edge['event_ids']
             if event_id in events_by_id and events_by_id[event_id].get('event_date') is not None]
    return (min(dates) if dates else None), (max(dates) if dates else None)


def case_graph_stored_edge_serialize(row, relation_types_by_id, tlps, events_by_id=None):
    """§4.1 edge of a stored edge row (`case_graph_db_edge_as_dict`)."""
    events_by_id = events_by_id or {}
    relation_type = relation_types_by_id.get(row['relation_type_id'])
    first_seen, last_seen = row.get('first_seen'), row.get('last_seen')
    if first_seen is None or last_seen is None:
        computed_first, computed_last = _edge_dates(row, events_by_id)
        first_seen = first_seen if first_seen is not None else computed_first
        last_seen = last_seen if last_seen is not None else computed_last
    return {
        'key': f'e:{row["edge_id"]}',
        'id': row['edge_id'],
        'source': case_graph_key(row['source_kind'], row['source_id']),
        'target': case_graph_key(row['target_kind'], row['target_id']),
        'relation': _relation_ref(relation_type, None if relation_type else str(row['relation_type_id'])),
        'directed': bool(row['directed']),
        'label': row.get('label'),
        'description': row.get('description'),
        'confidence': row.get('confidence'),
        'tlp': tlps.get(row.get('tlp_id')),
        'tlp_id': row.get('tlp_id'),
        'origin': row.get('origin') or 'manual',
        'review_state': row.get('review_state') or 'accepted',
        'first_seen': case_graph_isoformat(first_seen),
        'last_seen': case_graph_isoformat(last_seen),
        'event_ids': list(row.get('event_ids') or []),
        'count': max(1, len(row.get('event_ids') or [])),
        'editable': True,
        'custom_attributes': row.get('custom_attributes'),
        'created_by_id': row.get('created_by_id'),
    }


def case_graph_assembly_derived_edge(edge, relation_types_by_name):
    return {
        'key': case_graph_derived_edge_key(edge),
        'id': None,
        'source': edge['source'],
        'target': edge['target'],
        'relation': _relation_ref(relation_types_by_name.get(edge['relation']), edge['relation']),
        'directed': edge['directed'],
        'label': None,
        'description': None,
        'confidence': None,
        'tlp': None,
        'tlp_id': None,
        'origin': edge['origin'],
        'review_state': 'accepted',
        'first_seen': case_graph_isoformat(edge['first_seen']),
        'last_seen': case_graph_isoformat(edge['last_seen']),
        'event_ids': list(edge['event_ids']),
        'count': max(1, len(edge['event_ids'])),
        'editable': False,
    }


def case_graph_parse_include(raw):
    """Set of include values from a comma list (unknown values ignored)."""
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw).split(',')
    return {value.strip().lower() for value in values if value and value.strip().lower() in CASE_GRAPH_INCLUDES}


def case_graph_assembly_build(data, include=frozenset(), review_state='accepted', readable_case_ids=frozenset(),
                              generated_at=None, max_nodes=5000):
    """The §4.1 payload from the rows of `data` (see
    `case_graph_load_data`). `readable_case_ids`: the other cases the
    caller may read (only used with `cross_case`)."""
    case_id = data['case_id']
    include = set(include or ())
    tlps = data.get('tlps') or {}
    relation_types = [case_graph_relation_type_serialize(row) for row in data.get('relation_types') or []]
    relation_types_by_id = {row['id']: row for row in relation_types}
    relation_types_by_name = {row['name']: row for row in relation_types}

    assets = data.get('assets') or []
    iocs = data.get('iocs') or []
    events = data.get('events') or []
    event_links = data.get('event_links') or []
    events_in_graph = [event for event in events if case_graph_event_in_graph(event)]
    events_by_id = {event['event_id']: event for event in events_in_graph}

    flags_by_asset = {}
    for flag in data.get('asset_flags') or []:
        flags_by_asset.setdefault(flag['asset_id'], []).append(
            {'id': flag['id'], 'name': flag['name'], 'color': flag['color'], 'kind': flag['kind']})
    criticality_by_asset = {row['asset_id']: row['criticality'] for row in data.get('asset_managed') or []}

    # Every node the case data can provide, by key; `included` are emitted
    # whatever the edges, the others only when a stored edge reaches them.
    candidates = {}
    included = []

    def _register(node, always):
        node['_first'] = None
        node['_last'] = None
        candidates[node['key']] = node
        if always:
            included.append(node['key'])

    for row in assets:
        _register(_asset_node(case_id, row, flags_by_asset.get(row['asset_id'], []),
                              criticality_by_asset.get(row['asset_id'])), True)
    for row in iocs:
        _register(_ioc_node(case_id, row), True)
    for row in data.get('free_nodes') or []:
        _register(case_graph_assembly_free_node(row, tlps), True)
    for row in events:
        _register(_event_node(case_id, row), 'events' in include and case_graph_event_in_graph(row))
    for row in data.get('evidences') or []:
        _register(_evidence_node(case_id, row), 'evidences' in include)
    for row in data.get('tasks') or []:
        _register(_task_node(case_id, row), 'tasks' in include)
    for row in data.get('notes') or []:
        _register(_note_node(case_id, row), 'notes' in include)
    for row in data.get('alerts') or []:
        _register(_alert_node(row), 'alerts' in include)

    _stamp_entity_activity(candidates, events_by_id, event_links)

    # Cross-case counts are always computed; `case:<id>` nodes and their
    # `same_as` edges only with include=cross_case.
    cross = _cross_cases(data.get('ioc_cross_cases') or [], 'ioc')
    cross.update(_cross_cases(data.get('asset_cross_cases') or [], 'asset'))
    for key, case_ids in cross.items():
        if key in candidates:
            candidates[key]['cross_case_count'] = len(case_ids)

    derived = case_graph_derive_edges(events, event_links, data.get('ioc_asset_links') or [],
                                      include_events='events' in include,
                                      account_keys=_account_keys(assets, iocs))

    if 'alerts' in include:
        for link in data.get('alert_links') or []:
            derived.append({'source': case_graph_key('alert', link['alert_id']),
                            'target': case_graph_key(link['kind'], link['object_id']),
                            'relation': 'related_to', 'directed': False, 'origin': ORIGIN_ALERT,
                            'event_ids': [], 'first_seen': None, 'last_seen': None})

    if 'cross_case' in include:
        case_names = {row['case_id']: row.get('name') for row in data.get('cases') or []}
        readable = set(readable_case_ids or ())
        for key, case_ids in sorted(cross.items()):
            if key not in candidates:
                continue
            for other_case_id in sorted(case_ids):
                case_key = case_graph_key('case', other_case_id)
                if case_key not in candidates:
                    _register(case_graph_assembly_case_node(other_case_id, case_names.get(other_case_id),
                                                            other_case_id in readable), True)
                derived.append({'source': key, 'target': case_key, 'relation': 'same_as', 'directed': False,
                                'origin': ORIGIN_CROSS_CASE, 'event_ids': [], 'first_seen': None,
                                'last_seen': None})

    # Stored edges: filtered by review state, dangling ones skipped.
    stored = []
    for row in data.get('stored_edges') or []:
        if review_state != 'all' and (row.get('review_state') or 'accepted') != 'accepted':
            continue
        source = case_graph_key(row['source_kind'], row['source_id'])
        target = case_graph_key(row['target_kind'], row['target_id'])
        if source not in candidates or target not in candidates:
            continue
        relation_type = relation_types_by_id.get(row['relation_type_id'])
        stored.append(dict(row, source=source, target=target,
                           relation=relation_type['name'] if relation_type else None,
                           event_ids=list(row.get('event_ids') or [])))

    present = set(included)
    for edge in stored:
        present.add(edge['source'])
        present.add(edge['target'])
    derived = [edge for edge in derived if edge['source'] in present and edge['target'] in present]

    stored, derived = case_graph_merge_edges(
        stored, derived, {event_id: event.get('event_date') for event_id, event in events_by_id.items()})

    edges = [case_graph_stored_edge_serialize(row, relation_types_by_id, tlps, events_by_id) for row in stored]
    edges.extend(case_graph_assembly_derived_edge(edge, relation_types_by_name) for edge in derived)

    nodes = []
    for key, node in candidates.items():
        if key not in present:
            continue
        if node['kind'] in ('asset', 'ioc'):
            node['first_seen'] = case_graph_isoformat(node['_first'])
            node['last_seen'] = case_graph_isoformat(node['_last'])
        node.pop('_first')
        node.pop('_last')
        nodes.append(node)

    node_keys = {node['key'] for node in nodes}
    keys_by_event = {}
    for link in event_links:
        key = case_graph_key(link['kind'], link['object_id'])
        keys = keys_by_event.setdefault(link['event_id'], [])
        if key in node_keys and key not in keys:
            keys.append(key)
    payload_events = []
    for event in events_in_graph:
        keys = keys_by_event.get(event['event_id'], [])
        payload_events.append({
            'event_id': event['event_id'],
            'title': event.get('event_title'),
            'date': case_graph_isoformat(event.get('event_date')),
            'category': event.get('category_name'),
            'color': event.get('event_color'),
            'parent_event_id': event.get('parent_event_id'),
            'keys': keys,
        })

    meta = {'node_count': len(nodes), 'edge_count': len(edges), 'generated_at': case_graph_isoformat(generated_at)}
    if max_nodes and len(nodes) > max_nodes:
        meta['warning'] = (f'The graph has {len(nodes)} nodes, more than the {max_nodes} advised '
                           f'(IRIS_CASE_GRAPH_MAX_NODES): rendering may be slow.')

    return {
        'nodes': nodes,
        'edges': edges,
        'events': payload_events,
        'relation_types': relation_types,
        'suggestions': list(data.get('suggestions') or []) if 'suggestions' in include else [],
        'meta': meta,
    }
