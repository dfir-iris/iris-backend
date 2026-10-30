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

"""MCP tools for the investigation graph of a case."""
from __future__ import annotations

from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import VIEW_FULL
from app.blueprints.rest.v2.mcp.tools._common import VIEW_SCHEMA_FRAGMENT
from app.blueprints.rest.v2.mcp.tools._common import requested_view
from app.business.case_graph import case_graph_edge_create
from app.business.case_graph import case_graph_gaps
from app.business.case_graph import case_graph_get
from app.business.case_graph import case_graph_paths
from app.business.case_graph_assembly import case_graph_parse_include
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


_INCLUDE_SCHEMA = {
    'type': 'array',
    'items': {'type': 'string', 'enum': ['events', 'evidences', 'tasks', 'notes', 'alerts', 'cross_case']},
    'description': 'Extra node kinds. By default the graph holds the assets, IOCs and free nodes.',
}

_NODE_SUMMARY_FIELDS = ('key', 'kind', 'label', 'type', 'compromised', 'analysis_status', 'tags',
                        'event_count', 'first_seen', 'last_seen', 'cross_case_count')
_EDGE_SUMMARY_FIELDS = ('key', 'source', 'target', 'directed', 'origin', 'review_state', 'event_ids',
                        'first_seen', 'last_seen', 'description', 'confidence')


def _readable_case_ids(case_ids):
    granted = ac_fast_check_current_user_has_cases_access(
        case_ids, [CaseAccessLevel.read_only, CaseAccessLevel.full_access])
    return set(granted or {})


def _relation_name(edge):
    relation = edge.get('relation')
    return relation.get('name') if isinstance(relation, dict) else relation


def _edge_summary(edge):
    row = {field: edge.get(field) for field in _EDGE_SUMMARY_FIELDS}
    row['relation'] = _relation_name(edge)
    return row


def _include(args):
    return case_graph_parse_include(args.get('include') or [])


def _business(operation):
    try:
        return operation()
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Object not found in this case.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc


@mcp_tool(
    name='iris_case_graph_get',
    description=(
        'Get the investigation graph of a case: nodes (assets, IOCs, free nodes such as actors or '
        'accounts, and optionally events, evidences, tasks, notes, alerts, other cases) and edges '
        '(stored by analysts, or derived from timeline events, each with its supporting event ids). '
        'Node keys look like `asset:12`, `ioc:7`, `node:3`; use them with `iris_case_graph_paths` and '
        '`iris_case_graph_edge_create`. Rows are a projection unless `view` is full.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **VIEW_SCHEMA_FRAGMENT,
            'include': _INCLUDE_SCHEMA,
            'review_state': {
                'type': 'string', 'enum': ['accepted', 'all'],
                'description': 'accepted (default) or all, which adds the pending and rejected stored edges.',
            },
        },
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
)
def iris_case_graph_get(args: dict) -> dict:
    graph = _business(lambda: case_graph_get(args['case_identifier'], _include(args),
                                             args.get('review_state') or 'accepted', _readable_case_ids))
    if requested_view(args) == VIEW_FULL:
        return graph
    return {
        'nodes': [{field: node.get(field) for field in _NODE_SUMMARY_FIELDS} for node in graph['nodes']],
        'edges': [_edge_summary(edge) for edge in graph['edges']],
        'relation_types': [row['name'] for row in graph['relation_types']],
        'meta': graph['meta'],
    }


@mcp_tool(
    name='iris_case_graph_edge_create',
    description=(
        'Create an edge of the investigation graph between two nodes of the case (keys from '
        '`iris_case_graph_get`). `relation` is a relation type name (related_to, lateral_movement, '
        'authenticated_to, executed_on, beacons_to, resolves_to, exfiltrated_to, ...); its direction '
        'defaults from the relation type. Cite the supporting timeline events in `event_ids`.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'source': {'type': 'string', 'description': 'Key of the source node, e.g. asset:12.'},
            'target': {'type': 'string', 'description': 'Key of the target node, e.g. ioc:7.'},
            'relation': {'type': 'string', 'description': 'Relation type name.'},
            'directed': {'type': 'boolean', 'description': 'Defaults to the direction of the relation type.'},
            'label': {'type': 'string', 'description': 'Short label.'},
            'description': {'type': 'string', 'description': 'Why the edge holds (markdown).'},
            'confidence': {'type': 'integer', 'description': '0 to 100.'},
            'event_ids': {'type': 'array', 'items': {'type': 'integer'},
                          'description': 'Ids of the supporting timeline events of the case.'},
            'first_seen': {'type': 'string', 'description': 'ISO 8601.'},
            'last_seen': {'type': 'string', 'description': 'ISO 8601.'},
        },
        'required': ['source', 'target', 'relation'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
)
def iris_case_graph_edge_create(args: dict) -> dict:
    payload = {field: args[field] for field in ('source', 'target', 'relation', 'directed', 'label', 'description',
                                                'confidence', 'event_ids', 'first_seen', 'last_seen')
               if field in args}
    edge = _business(lambda: case_graph_edge_create(args['case_identifier'], iris_current_user.id, payload))
    return _edge_summary(edge)


@mcp_tool(
    name='iris_case_graph_paths',
    description=(
        'Simple paths between two nodes of the investigation graph of a case, shortest first, e.g. how '
        'an attacker got from the initial access asset to a compromised server.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'source': {'type': 'string', 'description': 'Key of the start node.'},
            'target': {'type': 'string', 'description': 'Key of the end node.'},
            'directed': {'type': 'boolean', 'description': 'Follow the edge directions (default true).'},
            'max_paths': {'type': 'integer', 'description': '1 to 50, default 5.'},
            'max_length': {'type': 'integer', 'description': '1 to 12, default 8.'},
            'include': _INCLUDE_SCHEMA,
        },
        'required': ['source', 'target'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
)
def iris_case_graph_paths(args: dict) -> dict:
    max_paths = args.get('max_paths') or 5
    max_length = args.get('max_length') or 8
    if not 1 <= max_paths <= 50 or not 1 <= max_length <= 12:
        raise MCPError(protocol.INVALID_PARAMS, 'max_paths must be 1 to 50 and max_length 1 to 12.')
    directed = args.get('directed')
    return _business(lambda: case_graph_paths(
        args['case_identifier'], args['source'], args['target'],
        directed=True if directed is None else bool(directed), max_paths=max_paths, max_length=max_length,
        include=_include(args), readable_case_ids_resolver=_readable_case_ids))


@mcp_tool(
    name='iris_case_graph_gaps',
    description=(
        'Investigation gaps of a case graph, most severe first: compromised assets with unknown initial '
        'access, without timeline event or not analysed, edges with no supporting evidence, isolated IOCs.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    case_scoped=True,
)
def iris_case_graph_gaps(args: dict) -> dict:
    gaps = _business(lambda: case_graph_gaps(args['case_identifier']))
    return {'total': len(gaps), 'data': gaps}
