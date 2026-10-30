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

"""Integration tests for `/api/v2/war-rooms/<id>/graph` (§4.10)."""

from unittest import TestCase

from iris import Iris
from iris import IRIS_CASE_ACCESS_LEVEL_READ_ONLY


_WAR_ROOMS_READ_WRITE = 0x8000 | 0x10000


class TestsRestWarRoomGraph(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self):
        return self._subject.create('/api/v2/war-rooms', {'name': 'Graph Room'}).json()['war_room_id']

    def _attach_case(self, room_id):
        case_id = self._subject.create_dummy_case()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})
        return case_id

    def _asset(self, case_id, name):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']

    def _ioc(self, case_id, value):
        return self._subject.create(f'/api/v2/cases/{case_id}/iocs',
                                    {'ioc_type_id': 1, 'ioc_tlp_id': 2, 'ioc_value': value}).json()['ioc_id']

    def _member(self, room_id):
        member = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members', {'user_id': member.get_identifier()})
        return member

    def _graph(self, room_id, user=None, **query):
        return (user or self._subject).get(f'/api/v2/war-rooms/{room_id}/graph', query_parameters=query or None)

    def test_get_graph_should_prefix_the_keys_with_the_case(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'WKS-01')
        ioc_id = self._ioc(case_id, 'evil.example')
        self._subject.create(f'/api/v2/cases/{case_id}/graph/edges', {
            'source': f'asset:{asset_id}', 'target': f'ioc:{ioc_id}', 'relation': 'beacons_to'})
        response = self._graph(room_id)
        self.assertEqual(200, response.status_code)
        body = response.json()
        for field in ('nodes', 'edges', 'events', 'relation_types', 'suggestions', 'meta'):
            self.assertIn(field, body)
        keys = {node['key'] for node in body['nodes']}
        self.assertEqual({f'case:{case_id}', f'c{case_id}/asset:{asset_id}', f'c{case_id}/ioc:{ioc_id}'}, keys)
        edge = next(edge for edge in body['edges'] if edge['relation']['name'] == 'beacons_to')
        self.assertEqual((f'c{case_id}/asset:{asset_id}', f'c{case_id}/ioc:{ioc_id}', case_id),
                         (edge['source'], edge['target'], edge['case_id']))
        self.assertEqual([case_id], body['meta']['case_ids'])

    def test_get_graph_should_join_the_same_ioc_across_cases(self):
        room_id = self._room()
        first_case_id, second_case_id = self._attach_case(room_id), self._attach_case(room_id)
        first_ioc_id = self._ioc(first_case_id, 'Shared.example')
        second_ioc_id = self._ioc(second_case_id, 'shared.example')
        self._ioc(second_case_id, 'other.example')
        edges = [edge for edge in self._graph(room_id).json()['edges'] if edge['relation']['name'] == 'same_as']
        self.assertEqual([{f'c{first_case_id}/ioc:{first_ioc_id}', f'c{second_case_id}/ioc:{second_ioc_id}'}],
                         [{edge['source'], edge['target']} for edge in edges])
        self.assertEqual(('cross_case', False), (edges[0]['origin'], edges[0]['editable']))

    def test_member_should_only_see_the_cases_it_can_read(self):
        room_id = self._room()
        readable_case_id = self._attach_case(room_id)
        secret_case_id = self._attach_case(room_id)
        self._asset(readable_case_id, 'WKS-01')
        self._asset(secret_case_id, 'secret-host')
        self._ioc(secret_case_id, '9.9.9.9')
        member = self._member(room_id)
        self._subject.grant_case_access(member, readable_case_id, IRIS_CASE_ACCESS_LEVEL_READ_ONLY)
        response = self._graph(room_id, user=member)
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertNotIn('secret-host', response.text)
        self.assertNotIn('9.9.9.9', response.text)
        self.assertFalse(any(node['key'].startswith(f'c{secret_case_id}/') for node in body['nodes']))
        secret = next(node for node in body['nodes'] if node['key'] == f'case:{secret_case_id}')
        self.assertEqual((False, 'Restricted case'), (secret['accessible'], secret['label']))
        readable = next(node for node in body['nodes'] if node['key'] == f'case:{readable_case_id}')
        self.assertTrue(readable['accessible'])
        self.assertEqual([readable_case_id], body['meta']['readable_case_ids'])

    def test_get_graph_should_include_events_on_request(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'WKS-01')
        event_id = self._subject.create(f'/api/v2/cases/{case_id}/events', {
            'event_title': 'title', 'event_category_id': 1, 'event_date': '2026-03-26T00:00:00.000',
            'event_tz': '+00:00', 'event_assets': [asset_id], 'event_iocs': []}).json()['event_id']
        keys = {node['key'] for node in self._graph(room_id, include='events').json()['nodes']}
        self.assertIn(f'c{case_id}/event:{event_id}', keys)

    def test_get_graph_should_reject_an_invalid_review_state(self):
        room_id = self._room()
        self.assertEqual(400, self._graph(room_id, review_state='pending').status_code)

    def test_user_without_war_room_access_should_get_403(self):
        room_id = self._room()
        user = self._subject.create_dummy_user()
        self.assertEqual(403, self._graph(room_id, user=user).status_code)

    def test_unknown_war_room_should_return_404(self):
        self.assertEqual(404, self._graph(999999).status_code)
