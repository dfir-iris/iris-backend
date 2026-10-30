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

"""Integration tests for `/api/v2/cases/<id>/graph` (investigation graph)
and the event roles it is derived from."""

import json
from unittest import TestCase

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY


class TestsRestCaseGraph(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _asset(self, case_id, name):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']

    def _ioc(self, case_id, value):
        body = {'ioc_type_id': 1, 'ioc_tlp_id': 2, 'ioc_value': value, 'ioc_description': '', 'ioc_tags': ''}
        return self._subject.create(f'/api/v2/cases/{case_id}/iocs', body).json()['ioc_id']

    def _event(self, case_id, assets=(), iocs=(), assets_roles=None, iocs_roles=None, **extra):
        body = {'event_title': 'title', 'event_category_id': 1, 'event_date': '2026-03-26T00:00:00.000',
                'event_tz': '+00:00', 'event_assets': list(assets), 'event_iocs': list(iocs), **extra}
        if assets_roles is not None:
            body['event_assets_roles'] = assets_roles
        if iocs_roles is not None:
            body['event_iocs_roles'] = iocs_roles
        return self._subject.create(f'/api/v2/cases/{case_id}/events', body)

    def _graph(self, case_id, user=None, **query):
        return (user or self._subject).get(f'/api/v2/cases/{case_id}/graph', query_parameters=query or None)

    def _edge(self, case_id, source, target, relation='related_to', user=None, **body):
        return (user or self._subject).create(f'/api/v2/cases/{case_id}/graph/edges',
                                              {'source': source, 'target': target, 'relation': relation, **body})

    def _node(self, case_id, label, node_type='actor', **body):
        return self._subject.create(f'/api/v2/cases/{case_id}/graph/nodes',
                                    {'label': label, 'node_type': node_type, **body})

    @staticmethod
    def _keys(graph, collection='nodes'):
        return {item['key'] for item in graph[collection]}

    # ------------------------------------------------------------ Graph --

    def test_get_graph_should_return_200(self):
        case_id = self._subject.create_dummy_case()
        response = self._graph(case_id)
        self.assertEqual(200, response.status_code)
        body = response.json()
        for field in ('nodes', 'edges', 'events', 'relation_types', 'suggestions', 'meta'):
            self.assertIn(field, body)
        self.assertIn('related_to', {row['name'] for row in body['relation_types']})

    def test_get_graph_should_return_assets_and_iocs(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'WKS-01')
        ioc_id = self._ioc(case_id, '10.0.0.1')
        self.assertTrue({f'asset:{asset_id}', f'ioc:{ioc_id}'} <= self._keys(self._graph(case_id).json()))

    def test_get_graph_should_derive_co_occurrence_from_events(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        event_id = self._event(case_id, [first, second]).json()['event_id']
        edges = self._graph(case_id).json()['edges']
        derived = [edge for edge in edges if {edge['source'], edge['target']} == {f'asset:{first}', f'asset:{second}'}]
        self.assertEqual(1, len(derived))
        self.assertEqual(('co_occurs', False, 'event', [event_id]),
                         (derived[0]['relation']['name'], derived[0]['editable'], derived[0]['origin'],
                          derived[0]['event_ids']))

    def test_get_graph_should_derive_directed_edges_from_roles(self):
        case_id = self._subject.create_dummy_case()
        source, target = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        self._event(case_id, [source, target], assets_roles={str(source): 'source', str(target): 'target'})
        edges = self._graph(case_id).json()['edges']
        edge = next(edge for edge in edges if edge['source'] == f'asset:{source}')
        self.assertEqual((f'asset:{target}', True), (edge['target'], edge['directed']))

    def test_get_graph_should_include_events_on_request(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'A')
        event_id = self._event(case_id, [asset_id]).json()['event_id']
        self.assertNotIn(f'event:{event_id}', self._keys(self._graph(case_id).json()))
        self.assertIn(f'event:{event_id}', self._keys(self._graph(case_id, include='events').json()))

    def test_get_graph_should_reject_an_invalid_review_state(self):
        case_id = self._subject.create_dummy_case()
        self.assertEqual(400, self._graph(case_id, review_state='pending').status_code)

    def test_get_graph_should_return_403_without_case_access(self):
        case_id = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self.assertEqual(403, self._graph(case_id, user=user).status_code)

    def test_get_graph_should_mark_unreadable_cross_cases(self):
        case_id = self._subject.create_dummy_case()
        other_case_id = self._subject.create_dummy_case()
        ioc_id = self._ioc(case_id, 'shared.example')
        self._ioc(other_case_id, 'shared.example')
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        graph = self._graph(case_id, user=user, include='cross_case').json()
        ioc = next(node for node in graph['nodes'] if node['key'] == f'ioc:{ioc_id}')
        self.assertEqual(1, ioc['cross_case_count'])
        case_node = next(node for node in graph['nodes'] if node['key'] == f'case:{other_case_id}')
        self.assertEqual((False, 'Restricted case'), (case_node['accessible'], case_node['label']))
        admin_graph = self._graph(case_id, include='cross_case').json()
        case_node = next(node for node in admin_graph['nodes'] if node['key'] == f'case:{other_case_id}')
        self.assertTrue(case_node['accessible'])

    def test_neighbours_should_return_200(self):
        case_id = self._subject.create_dummy_case()
        first, second, third = self._asset(case_id, 'A'), self._asset(case_id, 'B'), self._asset(case_id, 'C')
        self._edge(case_id, f'asset:{first}', f'asset:{second}')
        self._edge(case_id, f'asset:{second}', f'asset:{third}')
        response = self._subject.get(f'/api/v2/cases/{case_id}/graph/neighbours',
                                     query_parameters={'node': f'asset:{first}', 'hops': 1})
        self.assertEqual(200, response.status_code)
        self.assertEqual({f'asset:{first}', f'asset:{second}'}, self._keys(response.json()))

    def test_neighbours_should_return_404_for_an_unknown_node(self):
        case_id = self._subject.create_dummy_case()
        response = self._subject.get(f'/api/v2/cases/{case_id}/graph/neighbours', query_parameters={'node': 'asset:999999'})
        self.assertEqual(404, response.status_code)

    def test_paths_should_return_the_shortest_first(self):
        case_id = self._subject.create_dummy_case()
        first, second, third = self._asset(case_id, 'A'), self._asset(case_id, 'B'), self._asset(case_id, 'C')
        self._edge(case_id, f'asset:{first}', f'asset:{second}', 'lateral_movement')
        self._edge(case_id, f'asset:{second}', f'asset:{third}', 'lateral_movement')
        self._edge(case_id, f'asset:{first}', f'asset:{third}', 'lateral_movement')
        body = self._subject.get(f'/api/v2/cases/{case_id}/graph/paths', query_parameters={
            'source': f'asset:{first}', 'target': f'asset:{third}'}).json()
        self.assertEqual(1, body['shortest_length'])
        self.assertEqual(2, len(body['paths']))
        reverse = self._subject.get(f'/api/v2/cases/{case_id}/graph/paths', query_parameters={
            'source': f'asset:{third}', 'target': f'asset:{first}'}).json()
        self.assertEqual([], reverse['paths'])

    # ------------------------------------------------------------ Edges --

    def test_create_edge_should_return_201(self):
        case_id = self._subject.create_dummy_case()
        asset_id, ioc_id = self._asset(case_id, 'A'), self._ioc(case_id, '10.0.0.1')
        response = self._edge(case_id, f'asset:{asset_id}', f'ioc:{ioc_id}', 'beacons_to', confidence=70)
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual((f'e:{body["id"]}', 'beacons_to', True, 70, 'manual', True),
                         (body['key'], body['relation']['name'], body['directed'], body['confidence'],
                          body['origin'], body['editable']))
        self.assertIn(body['key'], self._keys(self._graph(case_id).json(), 'edges'))

    def test_create_edge_should_reject_a_duplicate(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        self._edge(case_id, f'asset:{first}', f'asset:{second}')
        self.assertEqual(400, self._edge(case_id, f'asset:{second}', f'asset:{first}').status_code)

    def test_create_edge_should_reject_an_endpoint_of_another_case(self):
        case_id = self._subject.create_dummy_case()
        other_case_id = self._subject.create_dummy_case()
        asset_id, other_asset_id = self._asset(case_id, 'A'), self._asset(other_case_id, 'B')
        self.assertEqual(400, self._edge(case_id, f'asset:{asset_id}', f'asset:{other_asset_id}').status_code)

    def test_create_edge_should_reject_an_unknown_relation(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        self.assertEqual(400, self._edge(case_id, f'asset:{first}', f'asset:{second}', 'nope').status_code)

    def test_create_edge_should_return_403_with_read_only_access(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        self.assertEqual(200, self._graph(case_id, user=user).status_code)
        self.assertEqual(403, self._edge(case_id, f'asset:{first}', f'asset:{second}', user=user).status_code)

    def _timeline_event(self, **extra):
        return {'event_title': 'A moved to B', 'event_category_id': 1, 'event_date': '2026-03-26T00:00:00.000',
                'event_tz': '+00:00', **extra}

    def test_create_edge_with_a_timeline_event_should_record_and_cite_it(self):
        case_id = self._subject.create_dummy_case()
        source, ioc_id = self._asset(case_id, 'A'), self._ioc(case_id, '10.0.0.1')
        response = self._edge(case_id, f'asset:{source}', f'ioc:{ioc_id}', 'beacons_to',
                              timeline_event=self._timeline_event())
        self.assertEqual(201, response.status_code)
        body = response.json()
        event_id = body['timeline_event_id']
        self.assertEqual([event_id], body['event_ids'])
        event = self._subject.get(f'/api/v2/cases/{case_id}/events/{event_id}').json()
        self.assertEqual(('A moved to B', {str(source): 'source'}, {str(ioc_id): 'target'}),
                         (event['event_title'], event['event_assets_roles'], event['event_iocs_roles']))
        edges = [edge for edge in self._graph(case_id).json()['edges']
                 if {edge['source'], edge['target']} == {f'asset:{source}', f'ioc:{ioc_id}'}]
        self.assertEqual([body['key']], [edge['key'] for edge in edges])

    def test_create_edge_with_a_timeline_event_should_link_undirected_endpoints_as_involved(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        body = self._edge(case_id, f'asset:{first}', f'asset:{second}', 'related_to', directed=False,
                          timeline_event=self._timeline_event()).json()
        event = self._subject.get(f'/api/v2/cases/{case_id}/events/{body["timeline_event_id"]}').json()
        self.assertEqual({str(first): 'involved', str(second): 'involved'}, event['event_assets_roles'])

    def test_create_edge_with_an_invalid_timeline_event_should_create_nothing(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        response = self._edge(case_id, f'asset:{first}', f'asset:{second}',
                              timeline_event=self._timeline_event(event_date='not a date'))
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self._graph(case_id, include='events').json()['events'])
        self.assertNotIn('e:', ''.join(self._keys(self._graph(case_id).json(), 'edges')))

    def test_create_duplicate_edge_with_a_timeline_event_should_not_record_it(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        self._edge(case_id, f'asset:{first}', f'asset:{second}')
        response = self._edge(case_id, f'asset:{first}', f'asset:{second}', timeline_event=self._timeline_event())
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self._graph(case_id, include='events').json()['events'])

    def test_create_edge_with_an_invalid_field_and_a_timeline_event_should_not_record_it(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        response = self._edge(case_id, f'asset:{first}', f'asset:{second}', confidence=150,
                              timeline_event=self._timeline_event())
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self._graph(case_id, include='events').json()['events'])

    def test_edge_timeline_events_should_list_the_events_linking_both_nodes(self):
        case_id = self._subject.create_dummy_case()
        first, second, third = self._asset(case_id, 'A'), self._asset(case_id, 'B'), self._asset(case_id, 'C')
        linked = self._event(case_id, [first, second], assets_roles={str(first): 'source', str(second): 'target'})
        self._event(case_id, [first, third])
        response = self._subject.get(f'/api/v2/cases/{case_id}/graph/edges/timeline-events',
                                     query_parameters={'source': f'asset:{second}', 'target': f'asset:{first}'})
        self.assertEqual(200, response.status_code)
        self.assertEqual([(linked.json()['event_id'], 'target', 'source', [])],
                         [(event['event_id'], event['source_role'], event['target_role'], event['edge_keys'])
                          for event in response.json()])

    def test_edge_timeline_events_should_list_the_events_cited_by_an_edge(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        event_id = self._event(case_id, [first]).json()['event_id']
        edge = self._edge(case_id, f'asset:{first}', f'asset:{second}', event_ids=[event_id]).json()
        events = self._subject.get(f'/api/v2/cases/{case_id}/graph/edges/timeline-events',
                                   query_parameters={'source': f'asset:{first}', 'target': f'asset:{second}'}).json()
        self.assertEqual([(event_id, 'involved', None, [edge['key']])],
                         [(event['event_id'], event['source_role'], event['target_role'], event['edge_keys'])
                          for event in events])

    def test_edge_timeline_events_should_require_both_nodes(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'A')
        response = self._subject.get(f'/api/v2/cases/{case_id}/graph/edges/timeline-events',
                                     query_parameters={'source': f'asset:{asset_id}'})
        self.assertEqual(400, response.status_code)

    def test_edge_timeline_events_should_return_403_without_case_access(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        user = self._subject.create_dummy_user()
        response = user.get(f'/api/v2/cases/{case_id}/graph/edges/timeline-events',
                            query_parameters={'source': f'asset:{first}', 'target': f'asset:{second}'})
        self.assertEqual(403, response.status_code)

    def test_update_edge_should_return_200(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_id = self._edge(case_id, f'asset:{first}', f'asset:{second}').json()['id']
        response = self._subject.update(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}',
                                        {'label': 'via RDP', 'relation': 'lateral_movement'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(('via RDP', 'lateral_movement'), (response.json()['label'], response.json()['relation']['name']))

    def test_reverse_edge_should_swap_the_endpoints(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_id = self._edge(case_id, f'asset:{first}', f'asset:{second}', 'lateral_movement').json()['id']
        body = self._subject.create(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}/reverse', {}).json()
        self.assertEqual((f'asset:{second}', f'asset:{first}'), (body['source'], body['target']))

    def test_review_edge_should_change_its_state(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_id = self._edge(case_id, f'asset:{first}', f'asset:{second}').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}/review', {'decision': 'reject'})
        self.assertEqual('rejected', response.json()['review_state'])
        self.assertNotIn(f'e:{edge_id}', self._keys(self._graph(case_id).json(), 'edges'))
        self.assertIn(f'e:{edge_id}', self._keys(self._graph(case_id, review_state='all').json(), 'edges'))
        bad = self._subject.create(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}/review', {'decision': 'maybe'})
        self.assertEqual(400, bad.status_code)

    def test_delete_edge_should_return_204(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_id = self._edge(case_id, f'asset:{first}', f'asset:{second}').json()['id']
        self.assertEqual(204, self._subject.delete(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}').status_code)
        self.assertEqual(404, self._subject.delete(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}').status_code)

    def test_deleting_an_asset_should_delete_its_edges(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_id = self._edge(case_id, f'asset:{first}', f'asset:{second}').json()['id']
        self._subject.delete(f'/api/v2/cases/{case_id}/assets/{first}')
        self.assertNotIn(f'e:{edge_id}', self._keys(self._graph(case_id, review_state='all').json(), 'edges'))
        self.assertEqual(404, self._subject.update(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}',
                                                   {'label': 'x'}).status_code)

    def test_edge_of_another_case_should_return_404(self):
        case_id = self._subject.create_dummy_case()
        other_case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_id = self._edge(case_id, f'asset:{first}', f'asset:{second}').json()['id']
        self.assertEqual(404, self._subject.delete(f'/api/v2/cases/{other_case_id}/graph/edges/{edge_id}').status_code)

    # ------------------------------------------------------------ Nodes --

    def test_create_node_should_return_201(self):
        case_id = self._subject.create_dummy_case()
        response = self._node(case_id, 'APT-X', description='Actor')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(('node', 'APT-X', 'actor'), (body['kind'], body['label'], body['type']))
        self.assertIn(body['key'], self._keys(self._graph(case_id).json()))

    def test_create_node_should_reject_an_unknown_type(self):
        case_id = self._subject.create_dummy_case()
        self.assertEqual(400, self._node(case_id, 'X', 'spaceship').status_code)

    def test_update_and_get_node(self):
        case_id = self._subject.create_dummy_case()
        node_id = self._node(case_id, 'APT-X').json()['id']
        self._subject.update(f'/api/v2/cases/{case_id}/graph/nodes/{node_id}', {'label': 'APT-Y'})
        body = self._subject.get(f'/api/v2/cases/{case_id}/graph/nodes/{node_id}').json()
        self.assertEqual('APT-Y', body['label'])

    def test_delete_node_should_delete_its_edges(self):
        case_id = self._subject.create_dummy_case()
        node_id = self._node(case_id, 'APT-X').json()['id']
        asset_id = self._asset(case_id, 'A')
        edge_id = self._edge(case_id, f'node:{node_id}', f'asset:{asset_id}').json()['id']
        self.assertEqual(204, self._subject.delete(f'/api/v2/cases/{case_id}/graph/nodes/{node_id}').status_code)
        graph = self._graph(case_id).json()
        self.assertNotIn(f'node:{node_id}', self._keys(graph))
        self.assertNotIn(f'e:{edge_id}', self._keys(graph, 'edges'))

    def test_promote_node_to_ioc_should_move_its_edges(self):
        case_id = self._subject.create_dummy_case()
        node_id = self._node(case_id, 'evil.example', 'other').json()['id']
        asset_id = self._asset(case_id, 'A')
        edge_id = self._edge(case_id, f'asset:{asset_id}', f'node:{node_id}').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/nodes/{node_id}/promote',
                                        {'kind': 'ioc', 'ioc_type_id': 1, 'ioc_tlp_id': 2})
        self.assertEqual(201, response.status_code)
        new_key = response.json()['key']
        self.assertTrue(new_key.startswith('ioc:'))
        graph = self._graph(case_id).json()
        self.assertNotIn(f'node:{node_id}', self._keys(graph))
        edge = next(edge for edge in graph['edges'] if edge['key'] == f'e:{edge_id}')
        self.assertEqual(new_key, edge['target'])

    def test_promote_node_to_asset(self):
        case_id = self._subject.create_dummy_case()
        node_id = self._node(case_id, 'SRV-02', 'infrastructure').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/nodes/{node_id}/promote',
                                        {'kind': 'asset', 'asset_type_id': 1})
        self.assertEqual(201, response.status_code)
        self.assertEqual('SRV-02', response.json()['node']['label'])

    def test_promote_node_should_reject_an_unknown_kind(self):
        case_id = self._subject.create_dummy_case()
        node_id = self._node(case_id, 'X').json()['id']
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/nodes/{node_id}/promote', {'kind': 'note'})
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------------ Paste --

    def test_paste_should_create_and_link_iocs(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'A')
        existing_id = self._ioc(case_id, '10.0.0.9')
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/paste', {
            'text': 'beacon to evil[.]example and 10.0.0.9 then hxxps://evil.example/x', 'link_to': f'asset:{asset_id}'})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual([f'ioc:{existing_id}'], body['existing'])
        self.assertEqual(2, len(body['created']))
        self.assertEqual(3, len(body['edges']))
        again = self._subject.create(f'/api/v2/cases/{case_id}/graph/paste', {
            'text': 'evil.example', 'link_to': f'asset:{asset_id}'}).json()
        self.assertEqual(([], []), (again['created'], again['edges']))

    def test_paste_should_reject_an_empty_text(self):
        case_id = self._subject.create_dummy_case()
        self.assertEqual(400, self._subject.create(f'/api/v2/cases/{case_id}/graph/paste', {'text': ' '}).status_code)

    # ------------------------------------------------------------ Views --

    def test_views_crud(self):
        case_id = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/views',
                                        {'name': 'Lateral', 'state': {'layout': 'cose'}})
        self.assertEqual(201, response.status_code)
        view_id = response.json()['view_id']
        self.assertEqual(['Lateral'], [view['name'] for view in
                                       self._subject.get(f'/api/v2/cases/{case_id}/graph/views').json()])
        updated = self._subject.update(f'/api/v2/cases/{case_id}/graph/views/{view_id}', {'is_shared': True}).json()
        self.assertTrue(updated['is_shared'])
        self.assertEqual({'layout': 'cose'}, self._subject.get(f'/api/v2/cases/{case_id}/graph/views/{view_id}').json()['state'])
        self.assertEqual(204, self._subject.delete(f'/api/v2/cases/{case_id}/graph/views/{view_id}').status_code)

    def test_private_view_should_be_hidden_from_other_users(self):
        case_id = self._subject.create_dummy_case()
        view_id = self._subject.create(f'/api/v2/cases/{case_id}/graph/views', {'name': 'Mine'}).json()['view_id']
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id)
        self.assertEqual([], user.get(f'/api/v2/cases/{case_id}/graph/views').json())
        self.assertEqual(404, user.get(f'/api/v2/cases/{case_id}/graph/views/{view_id}').status_code)

    def test_report_default_requires_a_shared_view(self):
        case_id = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/views',
                                        {'name': 'R', 'is_report_default': True})
        self.assertEqual(400, response.status_code)

    # ---------------------------------------------------- Import/export --

    def test_export_formats(self):
        case_id = self._subject.create_dummy_case()
        asset_id, ioc_id = self._asset(case_id, 'A'), self._ioc(case_id, '10.0.0.1')
        self._edge(case_id, f'asset:{asset_id}', f'ioc:{ioc_id}', 'beacons_to')
        exported = self._subject.get(f'/api/v2/cases/{case_id}/graph/export', query_parameters={'format': 'json'}).json()
        self.assertEqual(('json', f'case-{case_id}-graph.json'), (exported['format'], exported['filename']))
        self.assertIn('views', exported['content'])
        stix = self._subject.get(f'/api/v2/cases/{case_id}/graph/export', query_parameters={'format': 'stix'}).json()
        self.assertEqual('bundle', stix['content']['type'])
        self.assertIn('beacons-to', {obj.get('relationship_type') for obj in stix['content']['objects']})
        graphml = self._subject.get(f'/api/v2/cases/{case_id}/graph/export', query_parameters={'format': 'graphml'}).json()
        self.assertIn('<graphml', graphml['content'])
        bad = self._subject.get(f'/api/v2/cases/{case_id}/graph/export', query_parameters={'format': 'csv'})
        self.assertEqual(400, bad.status_code)

    def test_export_then_import_into_another_case(self):
        case_id = self._subject.create_dummy_case()
        node_id = self._node(case_id, 'APT-X').json()['id']
        ioc_id = self._ioc(case_id, 'evil.example')
        self._edge(case_id, f'node:{node_id}', f'ioc:{ioc_id}', 'downloaded_from')
        for export_format in ('json', 'stix'):
            with self.subTest(export_format=export_format):
                content = self._subject.get(f'/api/v2/cases/{case_id}/graph/export',
                                            query_parameters={'format': export_format}).json()['content']
                target_case_id = self._subject.create_dummy_case()
                response = self._subject.create(f'/api/v2/cases/{target_case_id}/graph/import',
                                                {'format': export_format, 'content': json.dumps(content)})
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertEqual(1, body['edges_created'])
                graph = self._graph(target_case_id).json()
                self.assertEqual({'APT-X', 'evil.example'}, {node['label'] for node in graph['nodes']})
                self.assertEqual(['downloaded_from'], [edge['relation']['name'] for edge in graph['edges']])

    def test_import_should_reject_invalid_content(self):
        case_id = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/import', {'format': 'stix', 'content': '{'})
        self.assertEqual(400, response.status_code)

    # ------------------------------------------------------ Suggestions --

    def _ip_asset(self, case_id, name, ip):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name, 'asset_ip': ip}).json()['asset_id']

    def _suggestions(self, case_id, user=None):
        return (user or self._subject).get(f'/api/v2/cases/{case_id}/graph/suggestions')

    def test_suggestions_should_match_an_asset_ip_with_an_ioc(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._ip_asset(case_id, 'WKS-01', '10.0.0.1')
        ioc_id = self._ioc(case_id, '10.0.0.1')
        self._ioc(case_id, '10.0.0.12')
        response = self._suggestions(case_id)
        self.assertEqual(200, response.status_code)
        suggestions = response.json()
        self.assertEqual([('ip_match', f'asset:{asset_id}', f'ioc:{ioc_id}', 'same_as')],
                         [(row['kind'], row['source'], row['target'], row['relation']) for row in suggestions])
        self.assertRegex(suggestions[0]['key'], r'^s:[0-9a-f]{16}$')
        self.assertEqual(suggestions, self._graph(case_id, include='suggestions').json()['suggestions'])
        self.assertEqual([], self._graph(case_id).json()['suggestions'])

    def test_suggestions_should_mention_entities_on_word_boundaries(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'WKS-01')
        ioc_id = self._ioc(case_id, '10.0.0.1')
        other_ioc_id = self._ioc(case_id, '10.0.0.12')
        self._event(case_id, event_content='WKS-01 connected to 10.0.0.12 on port 443')
        pairs = {frozenset((row['source'], row['target'])) for row in self._suggestions(case_id).json()
                 if row['kind'] == 'text_mention'}
        self.assertIn(frozenset((f'asset:{asset_id}', f'ioc:{other_ioc_id}')), pairs)
        self.assertFalse(any(f'ioc:{ioc_id}' in pair for pair in pairs))

    def test_accept_suggestion_should_store_an_edge(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._ip_asset(case_id, 'WKS-01', '10.0.0.1')
        ioc_id = self._ioc(case_id, '10.0.0.1')
        key = self._suggestions(case_id).json()[0]['key']
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/suggestions/accept',
                                        {'keys': [key, 's:0000000000000000']})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(['s:0000000000000000'], body['unknown_keys'])
        edge = body['edges'][0]
        self.assertEqual((f'asset:{asset_id}', f'ioc:{ioc_id}', 'same_as', 'suggestion', 'accepted', 90),
                         (edge['source'], edge['target'], edge['relation']['name'], edge['origin'],
                          edge['review_state'], edge['confidence']))
        self.assertEqual([], self._suggestions(case_id).json())

    def test_dismiss_suggestion_should_hide_it(self):
        case_id = self._subject.create_dummy_case()
        self._ip_asset(case_id, 'WKS-01', '10.0.0.1')
        self._ioc(case_id, '10.0.0.1')
        key = self._suggestions(case_id).json()[0]['key']
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/suggestions/dismiss', {'keys': [key]})
        self.assertEqual((200, {'dismissed': 1}), (response.status_code, response.json()))
        self.assertEqual([], self._suggestions(case_id).json())
        again = self._subject.create(f'/api/v2/cases/{case_id}/graph/suggestions/dismiss', {'keys': [key]})
        self.assertEqual({'dismissed': 0}, again.json())

    def test_rejected_edge_should_not_be_suggested_again(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._ip_asset(case_id, 'WKS-01', '10.0.0.1')
        ioc_id = self._ioc(case_id, '10.0.0.1')
        edge_id = self._edge(case_id, f'ioc:{ioc_id}', f'asset:{asset_id}', 'same_as').json()['id']
        self._subject.create(f'/api/v2/cases/{case_id}/graph/edges/{edge_id}/review', {'decision': 'reject'})
        self.assertEqual([], self._suggestions(case_id).json())

    def test_suggestion_review_should_reject_invalid_keys(self):
        case_id = self._subject.create_dummy_case()
        for path in ('accept', 'dismiss'):
            for keys in (None, [], ['e:1']):
                response = self._subject.create(f'/api/v2/cases/{case_id}/graph/suggestions/{path}', {'keys': keys})
                self.assertEqual(400, response.status_code, (path, keys))

    def test_suggestion_review_should_return_403_with_read_only_access(self):
        case_id = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        self.assertEqual(200, self._suggestions(case_id, user=user).status_code)
        for path in ('accept', 'dismiss', 'ai'):
            response = user.create(f'/api/v2/cases/{case_id}/graph/suggestions/{path}',
                                   {'keys': ['s:0000000000000000']})
            self.assertEqual(403, response.status_code, path)

    def test_suggestions_should_return_403_without_case_access(self):
        case_id = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self.assertEqual(403, self._suggestions(case_id, user=user).status_code)
        self.assertEqual(403, user.get(f'/api/v2/cases/{case_id}/graph/gaps').status_code)
        self.assertEqual(403, user.create(f'/api/v2/cases/{case_id}/graph/explain', {}).status_code)

    # --------------------------------------------------------------- AI --

    def test_ai_suggestions_should_return_409_without_an_llm(self):
        case_id = self._subject.create_dummy_case()
        self._asset(case_id, 'WKS-01')
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/suggestions/ai', {})
        self.assertEqual(409, response.status_code)
        self.assertEqual([], self._graph(case_id, review_state='all').json()['edges'])

    def test_explain_should_return_409_without_an_llm(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'WKS-01')
        response = self._subject.create(f'/api/v2/cases/{case_id}/graph/explain', {'node_keys': [f'asset:{asset_id}']})
        self.assertEqual(409, response.status_code)

    def test_explain_should_reject_an_invalid_payload(self):
        case_id = self._subject.create_dummy_case()
        for body in ({'view_id': 'x'}, {'node_keys': 'asset:1'}):
            self.assertEqual(400, self._subject.create(f'/api/v2/cases/{case_id}/graph/explain', body).status_code)

    def test_explain_should_be_allowed_with_read_only_access(self):
        case_id = self._subject.create_dummy_case()
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        self.assertEqual(409, user.create(f'/api/v2/cases/{case_id}/graph/explain', {}).status_code)

    # ------------------------------------------------------------- Gaps --

    def test_gaps_should_list_isolated_iocs_and_compromised_assets(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._subject.create(f'/api/v2/cases/{case_id}/assets', {
            'asset_type_id': 1, 'asset_name': 'WKS-01', 'asset_compromise_status_id': 1}).json()['asset_id']
        ioc_id = self._ioc(case_id, 'evil.example')
        response = self._subject.get(f'/api/v2/cases/{case_id}/graph/gaps')
        self.assertEqual(200, response.status_code)
        gaps = {(gap['code'], tuple(gap['node_keys'])) for gap in response.json()}
        self.assertIn(('initial_access_unknown', (f'asset:{asset_id}',)), gaps)
        self.assertIn(('compromised_without_event', (f'asset:{asset_id}',)), gaps)
        self.assertIn(('isolated_ioc', (f'ioc:{ioc_id}',)), gaps)

    def test_gaps_should_list_unsupported_edges(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        edge_key = self._edge(case_id, f'asset:{first}', f'asset:{second}', 'lateral_movement').json()['key']
        gaps = self._subject.get(f'/api/v2/cases/{case_id}/graph/gaps').json()
        self.assertEqual([('unsupported_edge', [edge_key])],
                         [(gap['code'], gap['edge_keys']) for gap in gaps if gap['edge_keys']])

    # ----------------------------------------------------------- Report --

    def test_report_should_render_the_graph_and_its_snapshot(self):
        data = {'report_name': 'graph', 'report_type': 1, 'report_language': 1, 'report_description': 'graph',
                'report_name_format': 'report_name_format'}
        report_identifier = self._subject.create_report(data, 'variable_case_graph.md')
        case_id = self._subject.create_dummy_case()
        self._asset(case_id, 'WKS-01')
        self._ioc(case_id, 'evil.example')
        rendered = self._subject.get(f'/case/report/generate-investigation/{report_identifier}',
                                     {'cid': case_id, 'safe': True}).text
        self.assertEqual('2|0|None', rendered.strip())
        self._subject.create(f'/api/v2/cases/{case_id}/graph/views', {
            'name': 'Report', 'is_shared': True, 'is_report_default': True,
            'snapshot': 'data:image/png;base64,iVBORw0KGgo='})
        rendered = self._subject.get(f'/case/report/generate-investigation/{report_identifier}',
                                     {'cid': case_id, 'safe': True}).text
        self.assertEqual('2|0|iVBORw0KGgo=', rendered.strip())

    # ------------------------------------------------------- Event roles --

    def test_create_event_should_return_roles(self):
        case_id = self._subject.create_dummy_case()
        first, second = self._asset(case_id, 'A'), self._asset(case_id, 'B')
        ioc_id = self._ioc(case_id, '10.0.0.1')
        body = self._event(case_id, [first, second], [ioc_id], assets_roles={str(first): 'source'},
                           iocs_roles={str(ioc_id): 'target'}).json()
        self.assertEqual({str(first): 'source', str(second): 'involved'}, body['event_assets_roles'])
        self.assertEqual({str(ioc_id): 'target'}, body['event_iocs_roles'])
        read = self._subject.get(f'/api/v2/cases/{case_id}/events/{body["event_id"]}').json()
        self.assertEqual(body['event_assets_roles'], read['event_assets_roles'])

    def test_create_event_should_reject_an_invalid_role(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'A')
        self.assertEqual(400, self._event(case_id, [asset_id], assets_roles={str(asset_id): 'attacker'}).status_code)

    def test_update_event_should_change_roles(self):
        case_id = self._subject.create_dummy_case()
        asset_id = self._asset(case_id, 'A')
        event = self._event(case_id, [asset_id]).json()
        self.assertEqual({str(asset_id): 'involved'}, event['event_assets_roles'])
        body = {'event_title': 'title', 'event_category_id': 1, 'event_date': '2026-03-26T00:00:00.000',
                'event_tz': '+00:00', 'event_assets': [asset_id], 'event_iocs': [],
                'event_assets_roles': {str(asset_id): 'target'}}
        updated = self._subject.update(f'/api/v2/cases/{case_id}/events/{event["event_id"]}', body).json()
        self.assertEqual({str(asset_id): 'target'}, updated['event_assets_roles'])
