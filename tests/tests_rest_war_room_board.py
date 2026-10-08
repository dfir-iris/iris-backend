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

"""Integration tests for `GET /api/v2/war-rooms/<id>/board`."""

from unittest import TestCase

from iris import Iris

_WAR_ROOMS_READ_WRITE = 0x8000 | 0x10000


class TestsRestWarRoomBoard(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self, name='Board Room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

    def _attach_case(self, room_id):
        case_id = self._subject.create_dummy_case()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})
        return case_id

    def _asset(self, case_id, name):
        return self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                    {'asset_type_id': 1, 'asset_name': name}).json()['asset_id']

    def _board(self, room_id, actor=None):
        return (actor or self._subject).get(f'/api/v2/war-rooms/{room_id}/board')

    def test_board_should_return_the_payload_shape(self):
        room_id = self._room()
        response = self._board(room_id)
        self.assertEqual(200, response.status_code)
        body = response.json()
        for key in ('generated_at', 'kpis', 'flags', 'cases', 'attention', 'decisions'):
            self.assertIn(key, body)
        self.assertEqual(0, body['kpis']['cases'])

    def test_board_should_count_assets_of_attached_cases(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        self._asset(case_id, 'dc01')
        self._asset(case_id, 'ws042')
        body = self._board(room_id).json()
        self.assertEqual(1, body['kpis']['cases'])
        self.assertEqual(2, body['kpis']['assets'])
        self.assertEqual(2, body['kpis']['unflagged'])
        self.assertEqual(0, body['kpis']['flagged'])
        case = body['cases'][0]
        self.assertEqual(case_id, case['case_id'])
        self.assertTrue(case['accessible'])
        self.assertEqual(2, case['assets_total'])

    def test_board_should_count_flagged_assets_per_flag_and_kind(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        flagged_id = self._asset(case_id, 'dc01')
        self._asset(case_id, 'ws042')
        flags = {flag['name']: flag for flag in self._subject.get('/api/v2/manage/asset-flags').json()}
        flag = next(flag for flag in flags.values()
                    if flag['kind'] == 'status' and not flag['requires_reason'] and not flag['requires_decision'])
        self._subject.update(f'/api/v2/cases/{case_id}/assets/{flagged_id}/flags/{flag["id"]}', {})
        body = self._board(room_id).json()
        self.assertEqual(1, body['kpis']['flagged'])
        self.assertEqual(1, body['kpis']['unflagged'])
        self.assertIn(flag['id'], [entry['id'] for entry in body['flags']])
        case = body['cases'][0]
        self.assertEqual(1, case['by_flag'][str(flag['id'])])
        self.assertEqual(1, case['by_kind']['status'])
        self.assertEqual(1, case['by_kind']['none'])

    def test_overdue_decision_should_need_attention(self):
        room_id = self._room()
        decision = self._subject.create(f'/api/v2/war-rooms/{room_id}/decisions',
                                        {'title': 'Rotate krbtgt', 'target_at': '2020-01-01T00:00:00Z'}).json()
        body = self._board(room_id).json()
        self.assertEqual(1, body['kpis']['decisions_open'])
        self.assertEqual(1, body['kpis']['decisions_overdue'])
        overdue = [i for i in body['attention'] if i['type'] == 'decision_overdue']
        self.assertEqual(decision['decision_id'], overdue[0]['decision_id'])
        self.assertEqual('high', overdue[0]['severity'])

    def test_proposed_decision_should_be_listed_and_need_attention(self):
        room_id = self._room()
        decision = self._subject.create(f'/api/v2/war-rooms/{room_id}/decisions',
                                        {'title': 'Disable legacy VPN'}).json()
        body = self._board(room_id).json()
        self.assertEqual([decision['decision_id']], [d['decision_id'] for d in body['decisions']])
        self.assertEqual('proposed', body['decisions'][0]['status'])
        pending = [i for i in body['attention'] if i.get('decision_id') == decision['decision_id']]
        self.assertEqual(1, len(pending))
        self.assertEqual('decision_pending_approval', pending[0]['type'])

    def test_member_without_case_access_should_only_see_case_ids(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        self._asset(case_id, 'secret-host')
        member = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        self._subject.create(f'/api/v2/war-rooms/{room_id}/members', {'user_id': member.get_identifier()})
        response = self._board(room_id, member)
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual([{'case_id': case_id, 'accessible': False}], body['cases'])
        self.assertEqual(1, body['kpis']['cases'])
        self.assertEqual(0, body['kpis']['cases_accessible'])
        self.assertEqual(0, body['kpis']['assets'])
        self.assertNotIn('secret-host', response.text)

    def test_user_without_war_room_access_should_get_403(self):
        room_id = self._room()
        user = self._subject.create_dummy_user()
        self.assertEqual(403, self._board(room_id, user).status_code)

    def test_missing_room_should_return_404(self):
        self.assertEqual(404, self._board(999999999).status_code)
