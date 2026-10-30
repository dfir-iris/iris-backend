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

"""War-room graph (§4.10): the union of the graphs of the cases attached
to a war room.

The graph of each readable case is the §4.1 graph of that case, its node
keys prefixed with `c<case_id>/` (`c5/asset:12`). Each attached case is a
`case:<id>` node; an unreadable one only that (`accessible: false`,
nothing of it is loaded). IOCs with the same value and type, and assets
of the same managed asset, in different cases are joined by undirected
`same_as` edges (origin `cross_case`).
"""

import datetime

from app.business.case_graph import case_graph_build
from app.business.case_graph import case_graph_max_nodes
from app.business.case_graph_assembly import case_graph_assembly_case_node
from app.business.case_graph_assembly import case_graph_assembly_derived_edge
from app.business.case_graph_assembly import case_graph_isoformat
from app.business.case_graph_assembly import case_graph_relation_type_serialize
from app.business.case_graph_derive import ORIGIN_CROSS_CASE
from app.business.case_graph_derive import case_graph_key
from app.datamgmt.case.case_graph_db import case_graph_db_case_rows
from app.datamgmt.case.case_graph_db import case_graph_db_relation_types
from app.models.errors import BusinessProcessingError


WAR_ROOM_GRAPH_INCLUDES = ('events', 'evidences', 'tasks', 'notes', 'alerts')


def war_room_graph_prefix_key(case_id, key):
    return f'c{case_id}/{key}'


def _prefixed_graph(case_id, graph):
    nodes = []
    for node in graph.get('nodes') or []:
        if node.get('kind') == 'case':
            # Cross-case nodes are not built here (no `cross_case` include).
            continue
        nodes.append(dict(node, key=war_room_graph_prefix_key(case_id, node['key']), case_id=case_id))
    node_keys = {node['key'] for node in nodes}
    edges = []
    for edge in graph.get('edges') or []:
        source = war_room_graph_prefix_key(case_id, edge['source'])
        target = war_room_graph_prefix_key(case_id, edge['target'])
        if source not in node_keys or target not in node_keys:
            continue
        edges.append(dict(edge, key=war_room_graph_prefix_key(case_id, edge['key']), source=source, target=target,
                          case_id=case_id))
    events = [dict(event, keys=[war_room_graph_prefix_key(case_id, key) for key in event.get('keys') or []],
                   case_id=case_id)
              for event in graph.get('events') or []]
    return nodes, edges, events


def war_room_graph_same_as(identities, relation_types_by_name):
    """`same_as` edges between nodes of different cases sharing an
    identity. `identities` is `[(prefixed node key, case_id, identity)]`
    (identity None: none); each node of a group is linked to the first
    node of the group found in another case (a star, not every pair)."""
    groups = {}
    for key, case_id, identity in identities:
        if identity is None:
            continue
        groups.setdefault(identity, []).append((case_id, key))
    edges = []
    seen = set()
    for identity in sorted(groups, key=str):
        members = sorted(groups[identity])
        if len({case_id for case_id, _ in members}) < 2:
            continue
        hub_case, hub = members[0]
        for case_id, key in members[1:]:
            if case_id == hub_case:
                continue
            source, target = sorted((hub, key))
            if (source, target) in seen:
                continue
            seen.add((source, target))
            edges.append(case_graph_assembly_derived_edge(
                {'source': source, 'target': target, 'relation': 'same_as', 'directed': False,
                 'origin': ORIGIN_CROSS_CASE, 'event_ids': [], 'first_seen': None, 'last_seen': None},
                relation_types_by_name))
    return edges


def _identities(case_id, data, node_keys):
    identities = []
    for row in data.get('iocs') or []:
        key = war_room_graph_prefix_key(case_id, case_graph_key('ioc', row['ioc_id']))
        value = (row.get('ioc_value') or '').strip().lower()
        if key in node_keys and value:
            identities.append((key, case_id, ('ioc', value, row.get('ioc_type_id'))))
    for row in data.get('asset_managed') or []:
        key = war_room_graph_prefix_key(case_id, case_graph_key('asset', row['asset_id']))
        if key in node_keys:
            identities.append((key, case_id, ('asset', row['managed_asset_id'])))
    return identities


def war_room_graph_get(attached_case_ids, readable_case_ids, include=frozenset(), review_state='accepted'):
    """§4.10 payload: `{nodes, edges, events, relation_types, suggestions,
    meta}` as §4.1, plus `case_id` on every node (but `case:` ones), edge
    and event. Only the cases of `readable_case_ids` are loaded."""
    if review_state not in ('accepted', 'all'):
        raise BusinessProcessingError('review_state must be accepted or all')
    readable = [case_id for case_id in attached_case_ids if case_id in set(readable_case_ids)]
    include = frozenset(value for value in include or () if value in WAR_ROOM_GRAPH_INCLUDES)
    names = {row['case_id']: row.get('name') for row in case_graph_db_case_rows(readable)}
    relation_types = [case_graph_relation_type_serialize(row) for row in case_graph_db_relation_types()]
    relation_types_by_name = {row['name']: row for row in relation_types}

    nodes = [case_graph_assembly_case_node(case_id, names.get(case_id), case_id in names)
             for case_id in attached_case_ids]
    edges = []
    events = []
    identities = []
    for case_id in readable:
        graph, data = case_graph_build(case_id, include, review_state)
        case_nodes, case_edges, case_events = _prefixed_graph(case_id, graph)
        nodes.extend(case_nodes)
        edges.extend(case_edges)
        events.extend(case_events)
        identities.extend(_identities(case_id, data, {node['key'] for node in case_nodes}))
    edges.extend(war_room_graph_same_as(identities, relation_types_by_name))
    events.sort(key=lambda event: (event.get('date') or '', event['event_id']))

    max_nodes = case_graph_max_nodes()
    meta = {'node_count': len(nodes), 'edge_count': len(edges),
            'generated_at': case_graph_isoformat(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)),
            'case_ids': list(attached_case_ids), 'readable_case_ids': readable}
    if max_nodes and len(nodes) > max_nodes:
        meta['warning'] = (f'The graph has {len(nodes)} nodes, more than the {max_nodes} advised '
                           f'(IRIS_CASE_GRAPH_MAX_NODES): rendering may be slow.')
    return {'nodes': nodes, 'edges': edges, 'events': events, 'relation_types': relation_types,
            'suggestions': [], 'meta': meta}
