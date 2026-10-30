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

"""Investigation gaps of a graph (§4.8), pure.

Computed on the §4.1 payload of `case_graph_get` (accepted edges): each
gap is `{code, severity, node_keys, edge_keys, message}`, most severe
first.
"""


GAP_INITIAL_ACCESS_UNKNOWN = 'initial_access_unknown'
GAP_ISOLATED_IOC = 'isolated_ioc'
GAP_UNSUPPORTED_EDGE = 'unsupported_edge'
GAP_COMPROMISED_WITHOUT_EVENT = 'compromised_without_event'
GAP_UNANALYSED_COMPROMISED = 'unanalysed_compromised'

CASE_GRAPH_GAP_CODES = (GAP_INITIAL_ACCESS_UNKNOWN, GAP_COMPROMISED_WITHOUT_EVENT, GAP_UNANALYSED_COMPROMISED,
                        GAP_UNSUPPORTED_EDGE, GAP_ISOLATED_IOC)

_SEVERITIES = ('high', 'medium', 'low')
_SEVERITY = {
    GAP_INITIAL_ACCESS_UNKNOWN: 'high',
    GAP_COMPROMISED_WITHOUT_EVENT: 'medium',
    GAP_UNANALYSED_COMPROMISED: 'medium',
    GAP_UNSUPPORTED_EDGE: 'low',
    GAP_ISOLATED_IOC: 'low',
}
_DONE = 'done'


def _gap(code, node_keys, edge_keys, message):
    return {'code': code, 'severity': _SEVERITY[code], 'node_keys': list(node_keys), 'edge_keys': list(edge_keys),
            'message': message}


def _edge_label(edge, labels):
    relation = edge.get('relation')
    relation = relation.get('name') if isinstance(relation, dict) else relation
    arrow = '->' if edge.get('directed') else '--'
    return f'{labels.get(edge["source"], edge["source"])} {arrow} {labels.get(edge["target"], edge["target"])} ({relation})'


def case_graph_gaps_compute(graph):
    """The gaps of a §4.1 graph:

    * `initial_access_unknown`: a compromised asset no directed edge
      points to (how the attacker got there is unknown);
    * `compromised_without_event`: a compromised asset with no timeline
      event;
    * `unanalysed_compromised`: a compromised asset whose analysis status
      is not `Done`;
    * `unsupported_edge`: a stored edge with no supporting event and no
      description;
    * `isolated_ioc`: an IOC with no edge at all.
    """
    nodes = graph.get('nodes') or []
    edges = graph.get('edges') or []
    labels = {node['key']: node.get('label') or node['key'] for node in nodes}
    incoming = {}
    touching = {}
    for edge in edges:
        if edge.get('directed'):
            incoming.setdefault(edge['target'], []).append(edge['key'])
        touching.setdefault(edge['source'], []).append(edge['key'])
        touching.setdefault(edge['target'], []).append(edge['key'])

    gaps = []
    for node in nodes:
        key = node['key']
        label = labels[key]
        if node.get('kind') == 'asset' and node.get('compromised'):
            if not incoming.get(key):
                gaps.append(_gap(GAP_INITIAL_ACCESS_UNKNOWN, [key], [],
                                 f'Compromised asset "{label}" has no incoming edge: its initial access is unknown'))
            if not node.get('event_count'):
                gaps.append(_gap(GAP_COMPROMISED_WITHOUT_EVENT, [key], [],
                                 f'Compromised asset "{label}" is not linked to any timeline event'))
            status = node.get('analysis_status')
            if (status or '').strip().lower() != _DONE:
                gaps.append(_gap(GAP_UNANALYSED_COMPROMISED, [key], [],
                                 f'Compromised asset "{label}" is not analysed (analysis status: '
                                 f'{status or "none"})'))
        elif node.get('kind') == 'ioc' and not touching.get(key):
            gaps.append(_gap(GAP_ISOLATED_IOC, [key], [], f'IOC "{label}" is not linked to anything'))

    for edge in edges:
        if not edge.get('editable') or edge.get('id') is None:
            continue
        if not edge.get('event_ids') and not (edge.get('description') or '').strip():
            gaps.append(_gap(GAP_UNSUPPORTED_EDGE, [edge['source'], edge['target']], [edge['key']],
                             f'Edge {_edge_label(edge, labels)} has no supporting event and no description'))

    gaps.sort(key=lambda gap: (_SEVERITIES.index(gap['severity']), CASE_GRAPH_GAP_CODES.index(gap['code'])))
    return gaps
