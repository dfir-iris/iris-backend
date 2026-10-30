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

"""Unit tests for the war-room graph (§4.10): per-case graphs, prefixed
keys, restricted cases and `same_as` across cases. The per-case build and
the DB are mocked."""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.war_room_graph import war_room_graph_get
from app.business.war_room_graph import war_room_graph_same_as
from app.models.errors import BusinessProcessingError


_MODULE = 'app.business.war_room_graph'
_RELATION_TYPES = [{'id': 4, 'name': 'same_as', 'label': 'Same as', 'directed': False, 'color': 'slate',
                    'line_style': 'dashed', 'is_system': True, 'sort_order': 4}]


def _case_graph(case_id, ioc_value, managed_asset_id):
    graph = {
        'nodes': [{'key': 'asset:1', 'kind': 'asset', 'id': 1, 'label': f'WKS-{case_id}'},
                  {'key': f'ioc:{case_id}0', 'kind': 'ioc', 'id': case_id * 10, 'label': ioc_value},
                  {'key': 'case:99', 'kind': 'case', 'id': 99, 'label': 'Cross case'}],
        'edges': [{'key': 'd:abc', 'source': 'asset:1', 'target': f'ioc:{case_id}0', 'relation': {'name': 'co_occurs'},
                   'directed': False},
                  {'key': 'd:def', 'source': 'asset:1', 'target': 'case:99', 'relation': {'name': 'same_as'},
                   'directed': False}],
        'events': [{'event_id': case_id * 100, 'date': f'2026-10-0{case_id}T00:00:00', 'keys': ['asset:1']}],
        'relation_types': [],
        'suggestions': [],
        'meta': {},
    }
    data = {'iocs': [{'ioc_id': case_id * 10, 'ioc_value': ioc_value, 'ioc_type_id': 20}],
            'asset_managed': [{'asset_id': 1, 'managed_asset_id': managed_asset_id, 'criticality': None}]}
    return graph, data


class TestWarRoomGraph(TestCase):

    def _run(self, attached, readable, graphs, include=frozenset()):
        build = MagicMock(side_effect=lambda case_id, *_args: graphs[case_id])
        rows = [{'case_id': case_id, 'name': f'#{case_id} - case'} for case_id in readable]
        with patch(f'{_MODULE}.case_graph_build', build), \
                patch(f'{_MODULE}.case_graph_db_case_rows', MagicMock(return_value=rows)), \
                patch(f'{_MODULE}.case_graph_db_relation_types', MagicMock(return_value=_RELATION_TYPES)), \
                patch(f'{_MODULE}.case_graph_max_nodes', MagicMock(return_value=2000)):
            return war_room_graph_get(attached, readable, include), build

    def test_unreadable_case_is_a_bare_restricted_node(self):
        graph, build = self._run([1, 2], [1], {1: _case_graph(1, 'evil.example', 7)})
        self.assertEqual([1], [call.args[0] for call in build.call_args_list])
        restricted = next(node for node in graph['nodes'] if node['key'] == 'case:2')
        self.assertFalse(restricted['accessible'])
        self.assertEqual('Restricted case', restricted['label'])
        self.assertFalse(any(node['key'].startswith('c2/') for node in graph['nodes']))
        readable = next(node for node in graph['nodes'] if node['key'] == 'case:1')
        self.assertTrue(readable['accessible'])

    def test_keys_are_prefixed_with_the_case(self):
        graph, _ = self._run([1], [1], {1: _case_graph(1, 'evil.example', 7)})
        keys = {node['key'] for node in graph['nodes']}
        self.assertEqual({'case:1', 'c1/asset:1', 'c1/ioc:10'}, keys)
        edge = graph['edges'][0]
        self.assertEqual(('c1/d:abc', 'c1/asset:1', 'c1/ioc:10', 1),
                         (edge['key'], edge['source'], edge['target'], edge['case_id']))
        self.assertEqual(1, len(graph['edges']))
        self.assertEqual(['c1/asset:1'], graph['events'][0]['keys'])
        self.assertEqual(1, next(node for node in graph['nodes'] if node['key'] == 'c1/asset:1')['case_id'])

    def test_same_ioc_and_managed_asset_are_joined(self):
        graph, _ = self._run([1, 2], [1, 2], {1: _case_graph(1, 'Evil.example', 7),
                                              2: _case_graph(2, 'evil.example ', 7)})
        same_as = sorted((edge['source'], edge['target']) for edge in graph['edges']
                         if edge['relation']['name'] == 'same_as')
        self.assertEqual([('c1/asset:1', 'c2/asset:1'), ('c1/ioc:10', 'c2/ioc:20')], same_as)
        edge = next(edge for edge in graph['edges'] if edge['relation']['name'] == 'same_as')
        self.assertEqual(('cross_case', False, False), (edge['origin'], edge['directed'], edge['editable']))

    def test_different_values_are_not_joined(self):
        graph, _ = self._run([1, 2], [1, 2], {1: _case_graph(1, 'evil.example', 7),
                                              2: _case_graph(2, 'good.example', 8)})
        self.assertFalse(any(edge['relation']['name'] == 'same_as' for edge in graph['edges']))

    def test_include_is_restricted_to_case_kinds(self):
        _, build = self._run([1], [1], {1: _case_graph(1, 'evil.example', 7)},
                             include={'events', 'cross_case', 'suggestions'})
        self.assertEqual(frozenset({'events'}), build.call_args.args[1])

    def test_invalid_review_state_is_rejected_even_without_case(self):
        with self.assertRaises(BusinessProcessingError):
            war_room_graph_get([], [], review_state='pending')

    def test_meta_and_events_order(self):
        graph, _ = self._run([2, 1], [1, 2], {1: _case_graph(1, 'a.example', 7), 2: _case_graph(2, 'b.example', 8)})
        self.assertEqual([100, 200], [event['event_id'] for event in graph['events']])
        self.assertEqual([2, 1], graph['meta']['case_ids'])
        self.assertEqual(len(graph['nodes']), graph['meta']['node_count'])


class TestSameAs(TestCase):

    def test_group_is_a_star_without_same_case_pairs(self):
        identities = [('c1/ioc:1', 1, 'x'), ('c1/ioc:2', 1, 'x'), ('c2/ioc:3', 2, 'x'), ('c3/ioc:4', 3, 'x'),
                      ('c3/ioc:5', 3, None)]
        edges = war_room_graph_same_as(identities, {})
        self.assertEqual([('c1/ioc:1', 'c2/ioc:3'), ('c1/ioc:1', 'c3/ioc:4')],
                         [(edge['source'], edge['target']) for edge in edges])

    def test_single_case_group_gives_nothing(self):
        self.assertEqual([], war_room_graph_same_as([('c1/ioc:1', 1, 'x'), ('c1/ioc:2', 1, 'x')], {}))
