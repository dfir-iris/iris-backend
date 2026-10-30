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

"""Unit tests for the investigation graph MCP tools. The business layer
is mocked; these tests pin the registration, the projection and the
wiring."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.blueprints.rest.v2.mcp import classification
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import TOOL_REGISTRY
from app.blueprints.rest.v2.mcp.tools import case_graph as graph_tools
from app.models.errors import BusinessProcessingError


_TOOLS = 'app.blueprints.rest.v2.mcp.tools.case_graph'

_GRAPH = {
    'nodes': [{'key': 'asset:1', 'kind': 'asset', 'label': 'WKS-01', 'type': 'Windows', 'compromised': True,
               'analysis_status': 'Started', 'tags': [], 'event_count': 2, 'first_seen': None, 'last_seen': None,
               'cross_case_count': 0, 'enrichment': {'score': 3}, 'description': 'long text'}],
    'edges': [{'key': 'e:1', 'source': 'asset:1', 'target': 'ioc:5', 'relation': {'name': 'beacons_to', 'id': 3},
               'directed': True, 'origin': 'manual', 'review_state': 'accepted', 'event_ids': [10],
               'first_seen': None, 'last_seen': None, 'description': None, 'confidence': None,
               'custom_attributes': {'x': 1}}],
    'events': [], 'relation_types': [{'name': 'beacons_to'}], 'suggestions': [], 'meta': {'node_count': 1},
}


class TestRegistration(TestCase):

    def test_tools_are_registered_case_scoped_and_classified(self):
        for name in ('iris_case_graph_get', 'iris_case_graph_paths', 'iris_case_graph_gaps'):
            self.assertTrue(TOOL_REGISTRY[name].case_scoped, name)
            self.assertTrue(classification.is_read_only(name), name)
        self.assertTrue(TOOL_REGISTRY['iris_case_graph_edge_create'].case_scoped)
        self.assertTrue(classification.is_write('iris_case_graph_edge_create'))


class TestGraphGet(TestCase):

    def test_summary_projection(self):
        with patch(f'{_TOOLS}.case_graph_get', MagicMock(return_value=_GRAPH)) as get:
            result = graph_tools.iris_case_graph_get({'case_identifier': 4, 'include': ['events', 'bogus']})
        self.assertEqual(4, get.call_args.args[0])
        self.assertEqual({'events'}, set(get.call_args.args[1]))
        self.assertEqual('accepted', get.call_args.args[2])
        self.assertNotIn('enrichment', result['nodes'][0])
        self.assertEqual('beacons_to', result['edges'][0]['relation'])
        self.assertNotIn('custom_attributes', result['edges'][0])
        self.assertEqual(['beacons_to'], result['relation_types'])

    def test_full_view_returns_the_graph(self):
        with patch(f'{_TOOLS}.case_graph_get', MagicMock(return_value=_GRAPH)):
            result = graph_tools.iris_case_graph_get({'case_identifier': 4, 'view': 'full'})
        self.assertIs(_GRAPH, result)

    def test_business_error_is_invalid_params(self):
        with patch(f'{_TOOLS}.case_graph_get', MagicMock(side_effect=BusinessProcessingError('bad'))):
            with self.assertRaises(MCPError):
                graph_tools.iris_case_graph_get({'case_identifier': 4, 'review_state': 'nope'})


class TestEdgeCreate(TestCase):

    def test_payload_is_forwarded(self):
        create = MagicMock(return_value=_GRAPH['edges'][0])
        with patch(f'{_TOOLS}.case_graph_edge_create', create), \
                patch(f'{_TOOLS}.iris_current_user', SimpleNamespace(id=3)):
            result = graph_tools.iris_case_graph_edge_create({
                'case_identifier': 4, 'source': 'asset:1', 'target': 'ioc:5', 'relation': 'beacons_to',
                'event_ids': [10]})
        create.assert_called_once_with(4, 3, {'source': 'asset:1', 'target': 'ioc:5', 'relation': 'beacons_to',
                                              'event_ids': [10]})
        self.assertEqual('e:1', result['key'])
        self.assertEqual('beacons_to', result['relation'])


class TestPathsAndGaps(TestCase):

    def test_paths_defaults(self):
        paths = MagicMock(return_value={'paths': [], 'shortest_length': None})
        with patch(f'{_TOOLS}.case_graph_paths', paths):
            graph_tools.iris_case_graph_paths({'case_identifier': 4, 'source': 'asset:1', 'target': 'ioc:5'})
        kwargs = paths.call_args.kwargs
        self.assertEqual((True, 5, 8), (kwargs['directed'], kwargs['max_paths'], kwargs['max_length']))

    def test_paths_bounds(self):
        with self.assertRaises(MCPError):
            graph_tools.iris_case_graph_paths({'case_identifier': 4, 'source': 'a', 'target': 'b', 'max_paths': 99})

    def test_gaps(self):
        gaps = [{'code': 'isolated_ioc'}]
        with patch(f'{_TOOLS}.case_graph_gaps', MagicMock(return_value=gaps)):
            self.assertEqual({'total': 1, 'data': gaps}, graph_tools.iris_case_graph_gaps({'case_identifier': 4}))
