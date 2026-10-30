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

"""Unit tests for the case graph exploration (§4.2): neighbours and paths."""

from unittest import TestCase

from app.business.case_graph_explore import case_graph_explore_neighbours
from app.business.case_graph_explore import case_graph_explore_paths
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _graph(edges, extra_nodes=()):
    keys = set(extra_nodes)
    for _, source, target, _ in edges:
        keys.update((source, target))
    return {'nodes': [{'key': key} for key in sorted(keys)],
            'edges': [{'key': key, 'source': source, 'target': target, 'directed': directed}
                      for key, source, target, directed in edges]}


# a -> b -> c -> d, a -> d (directed), d - e (undirected), x isolated
_CHAIN = _graph([('e:1', 'n:a', 'n:b', True), ('e:2', 'n:b', 'n:c', True), ('e:3', 'n:c', 'n:d', True),
                 ('e:4', 'n:a', 'n:d', True), ('d:5', 'n:d', 'n:e', False)], extra_nodes=('n:x',))


class TestNeighbours(TestCase):

    def test_one_hop_ignores_direction(self):
        result = case_graph_explore_neighbours(_CHAIN, 'n:d', 1)
        self.assertEqual({'n:a', 'n:c', 'n:d', 'n:e'}, {node['key'] for node in result['nodes']})
        self.assertEqual({'e:3', 'e:4', 'd:5'}, {edge['key'] for edge in result['edges']})

    def test_two_hops(self):
        result = case_graph_explore_neighbours(_CHAIN, 'n:e', 2)
        self.assertEqual({'n:a', 'n:c', 'n:d', 'n:e'}, {node['key'] for node in result['nodes']})

    def test_edges_between_neighbours_are_included(self):
        result = case_graph_explore_neighbours(_CHAIN, 'n:b', 1)
        self.assertEqual({'e:1', 'e:2'}, {edge['key'] for edge in result['edges']})
        result = case_graph_explore_neighbours(_CHAIN, 'n:b', 2)
        self.assertEqual({'e:1', 'e:2', 'e:3', 'e:4'}, {edge['key'] for edge in result['edges']})

    def test_isolated_node(self):
        result = case_graph_explore_neighbours(_CHAIN, 'n:x', 3)
        self.assertEqual(['n:x'], [node['key'] for node in result['nodes']])
        self.assertEqual([], result['edges'])

    def test_unknown_node(self):
        with self.assertRaises(ObjectNotFoundError):
            case_graph_explore_neighbours(_CHAIN, 'n:zz', 1)

    def test_hops_bounds(self):
        for hops in (0, 4, True, '1'):
            with self.subTest(hops=hops), self.assertRaises(BusinessProcessingError):
                case_graph_explore_neighbours(_CHAIN, 'n:a', hops)


class TestPaths(TestCase):

    def test_shortest_first(self):
        result = case_graph_explore_paths(_CHAIN, 'n:a', 'n:d')
        self.assertEqual(1, result['shortest_length'])
        self.assertEqual([{'node_keys': ['n:a', 'n:d'], 'edge_keys': ['e:4']},
                          {'node_keys': ['n:a', 'n:b', 'n:c', 'n:d'], 'edge_keys': ['e:1', 'e:2', 'e:3']}],
                         result['paths'])

    def test_directed_blocks_reverse_walk(self):
        result = case_graph_explore_paths(_CHAIN, 'n:d', 'n:a')
        self.assertEqual({'paths': [], 'shortest_length': None}, result)

    def test_undirected_walks_both_ways(self):
        result = case_graph_explore_paths(_CHAIN, 'n:d', 'n:a', directed=False)
        self.assertEqual(1, result['shortest_length'])
        self.assertEqual(['n:d', 'n:a'], result['paths'][0]['node_keys'])

    def test_undirected_edge_is_walked_both_ways_even_when_directed(self):
        result = case_graph_explore_paths(_CHAIN, 'n:e', 'n:d')
        self.assertEqual([['n:e', 'n:d']], [path['node_keys'] for path in result['paths']])

    def test_max_paths(self):
        result = case_graph_explore_paths(_CHAIN, 'n:a', 'n:d', max_paths=1)
        self.assertEqual(1, len(result['paths']))

    def test_max_length(self):
        result = case_graph_explore_paths(_CHAIN, 'n:a', 'n:d', max_length=2)
        self.assertEqual([['n:a', 'n:d']], [path['node_keys'] for path in result['paths']])

    def test_shortest_longer_than_max_length(self):
        result = case_graph_explore_paths(_CHAIN, 'n:b', 'n:e', max_length=2)
        self.assertEqual({'paths': [], 'shortest_length': 3}, result)

    def test_paths_are_simple(self):
        graph = _graph([('e:1', 'n:a', 'n:b', False), ('e:2', 'n:b', 'n:a', False), ('e:3', 'n:b', 'n:c', False)])
        result = case_graph_explore_paths(graph, 'n:a', 'n:c', directed=False, max_paths=10)
        for path in result['paths']:
            self.assertEqual(len(path['node_keys']), len(set(path['node_keys'])))
        self.assertEqual([['n:a', 'n:b', 'n:c']], [path['node_keys'] for path in result['paths']])

    def test_no_path(self):
        result = case_graph_explore_paths(_CHAIN, 'n:a', 'n:x')
        self.assertEqual({'paths': [], 'shortest_length': None}, result)

    def test_unknown_endpoint(self):
        with self.assertRaises(ObjectNotFoundError):
            case_graph_explore_paths(_CHAIN, 'n:a', 'n:zz')

    def test_same_endpoints(self):
        with self.assertRaises(BusinessProcessingError):
            case_graph_explore_paths(_CHAIN, 'n:a', 'n:a')

    def test_bounds(self):
        for kwargs in ({'max_paths': 0}, {'max_paths': 51}, {'max_length': 0}, {'max_length': 13}):
            with self.subTest(**kwargs), self.assertRaises(BusinessProcessingError):
                case_graph_explore_paths(_CHAIN, 'n:a', 'n:d', **kwargs)
