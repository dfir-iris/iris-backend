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

"""Exploration of an assembled graph (§4.2): neighbourhoods and paths.

Pure functions over the `{nodes, edges}` of `case_graph_assembly_build`.
"""

from collections import deque

from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


CASE_GRAPH_MAX_HOPS = 3
CASE_GRAPH_MAX_PATHS = 50
CASE_GRAPH_MAX_PATH_LENGTH = 12

# Upper bound of partial paths expanded by one search; a dense graph
# cannot make a request spin (the paths found so far are returned).
_PATH_SEARCH_BUDGET = 200000


def _adjacency(edges, directed):
    """`{node: [(neighbour, edge_key), ...]}` in edge order. With
    `directed`, a directed edge is only walked source -> target."""
    adjacency = {}
    for edge in edges:
        source, target = edge['source'], edge['target']
        adjacency.setdefault(source, []).append((target, edge['key']))
        if not directed or not edge.get('directed'):
            adjacency.setdefault(target, []).append((source, edge['key']))
    return adjacency


def case_graph_explore_neighbours(graph, node_key, hops=1):
    """Nodes within `hops` (1-3) of `node_key`, direction ignored, and
    the edges between them."""
    if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= CASE_GRAPH_MAX_HOPS:
        raise BusinessProcessingError(f'hops must be between 1 and {CASE_GRAPH_MAX_HOPS}')
    nodes_by_key = {node['key']: node for node in graph.get('nodes') or []}
    if node_key not in nodes_by_key:
        raise ObjectNotFoundError()
    adjacency = _adjacency(graph.get('edges') or [], directed=False)

    distance = {node_key: 0}
    queue = deque([node_key])
    while queue:
        current = queue.popleft()
        if distance[current] >= hops:
            continue
        for neighbour, _ in adjacency.get(current, ()):
            if neighbour not in distance:
                distance[neighbour] = distance[current] + 1
                queue.append(neighbour)

    nodes = [node for node in graph.get('nodes') or [] if node['key'] in distance]
    edges = [edge for edge in graph.get('edges') or []
             if edge['source'] in distance and edge['target'] in distance]
    return {'nodes': nodes, 'edges': edges}


def _distances_to(target, adjacency):
    """Hop distance of every node that can reach `target`."""
    reverse = {}
    for node, neighbours in adjacency.items():
        for neighbour, _ in neighbours:
            reverse.setdefault(neighbour, []).append(node)
    distance = {target: 0}
    queue = deque([target])
    while queue:
        current = queue.popleft()
        for previous in reverse.get(current, ()):
            if previous not in distance:
                distance[previous] = distance[current] + 1
                queue.append(previous)
    return distance


def _validate_int(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BusinessProcessingError(f'{name} must be between {low} and {high}')


def case_graph_explore_paths(graph, source, target, directed=True, max_paths=5, max_length=8):
    """Simple paths from `source` to `target`, shortest first (§4.2).

    A path is `{node_keys, edge_keys}`; between two nodes linked by
    several edges, the first one of the graph is used (stored edges come
    before derived ones). `directed=False` walks every edge both ways.
    """
    _validate_int('max_paths', max_paths, 1, CASE_GRAPH_MAX_PATHS)
    _validate_int('max_length', max_length, 1, CASE_GRAPH_MAX_PATH_LENGTH)
    node_keys = {node['key'] for node in graph.get('nodes') or []}
    if source not in node_keys or target not in node_keys:
        raise ObjectNotFoundError()
    if source == target:
        raise BusinessProcessingError('source and target must differ')

    adjacency = _adjacency(graph.get('edges') or [], directed=directed)
    distance = _distances_to(target, adjacency)
    if source not in distance:
        return {'paths': [], 'shortest_length': None}
    shortest = distance[source]
    if shortest > max_length:
        return {'paths': [], 'shortest_length': shortest}

    paths = []
    budget = [_PATH_SEARCH_BUDGET]

    def _walk(path_nodes, path_edges, length):
        if len(paths) >= max_paths or budget[0] <= 0:
            return
        budget[0] -= 1
        current = path_nodes[-1]
        if current == target:
            if len(path_edges) == length:
                paths.append({'node_keys': list(path_nodes), 'edge_keys': list(path_edges)})
            return
        remaining = length - len(path_edges)
        used = set()
        for neighbour, edge_key in adjacency.get(current, ()):
            if neighbour in used or neighbour in path_nodes:
                continue
            if distance.get(neighbour, remaining + 1) > remaining - 1:
                continue
            used.add(neighbour)
            path_nodes.append(neighbour)
            path_edges.append(edge_key)
            _walk(path_nodes, path_edges, length)
            path_nodes.pop()
            path_edges.pop()
            if len(paths) >= max_paths:
                return

    for length in range(shortest, max_length + 1):
        _walk([source], [], length)
        if len(paths) >= max_paths or budget[0] <= 0:
            break

    return {'paths': paths, 'shortest_length': shortest}
