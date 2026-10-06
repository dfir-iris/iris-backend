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

"""Integration tests for `/api/v2/war-rooms/<id>/decisions`."""

from unittest import TestCase

from iris import Iris

_WAR_ROOMS_READ_WRITE = 0x8000 | 0x10000
_PAST = '2020-01-01T08:00:00Z'
_FUTURE = '2099-01-01T08:00:00+02:00'


class TestsRestWarRoomDecisions(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self, name='Decisions Room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

    def _attach_case(self, room_id):
        case_id = self._subject.create_dummy_case()
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/cases', {'case_id': case_id})
        self.assertEqual(201, response.status_code)
        return case_id

    def _member(self, room_id):
        user = self._subject.create_dummy_user(permissions=_WAR_ROOMS_READ_WRITE)
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/members',
                                        {'user_id': user.get_identifier()})
        self.assertIn(response.status_code, (200, 201))
        return user

    def _create(self, room_id, actor=None, **body):
        body.setdefault('title', 'Isolate DC01')
        return (actor or self._subject).create(f'/api/v2/war-rooms/{room_id}/decisions', body)

    def test_create_should_return_201_with_number_and_target_at(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        response = self._create(room_id, target_at='2099-01-01T10:00:00+02:00', case_ids=[case_id],
                                rationale='Lateral movement observed')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual(1, body['number'])
        self.assertEqual('D-1', body['ref'])
        self.assertEqual('proposed', body['status'])
        self.assertTrue(body['target_at'].startswith('2099-01-01T08:00:00'))
        self.assertFalse(body['is_overdue'])
        self.assertEqual([case_id], body['case_ids'])
        self.assertTrue(body['cases'][0]['accessible'])

    def test_numbers_should_increase_per_room(self):
        room_id = self._room()
        other_room_id = self._room('Other')
        self.assertEqual(1, self._create(room_id).json()['number'])
        self.assertEqual(2, self._create(room_id).json()['number'])
        self.assertEqual(1, self._create(other_room_id).json()['number'])

    def test_create_should_reject_invalid_target_at(self):
        room_id = self._room()
        response = self._create(room_id, target_at='next tuesday')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_case_not_attached(self):
        room_id = self._room()
        case_id = self._subject.create_dummy_case()
        response = self._create(room_id, case_ids=[case_id])
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_boolean_ids(self):
        room_id = self._room()
        response = self._create(room_id, approver_ids=[True])
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_empty_title(self):
        room_id = self._room()
        response = self._create(room_id, title='   ')
        self.assertEqual(400, response.status_code)

    def test_past_target_should_be_flagged_overdue(self):
        room_id = self._room()
        decision_id = self._create(room_id, target_at=_PAST).json()['decision_id']
        body = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}').json()
        self.assertTrue(body['is_overdue'])

    def test_implemented_decision_should_not_be_overdue(self):
        room_id = self._room()
        decision_id = self._create(room_id, target_at=_PAST).json()['decision_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/implemented', {})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIsNotNone(body['implemented_at'])
        self.assertFalse(body['is_overdue'])
        reopened = self._subject.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/reopen', {})
        self.assertIsNone(reopened.json()['implemented_at'])
        self.assertTrue(reopened.json()['is_overdue'])

    def test_vote_by_all_approvers_should_approve(self):
        room_id = self._room()
        approver = self._member(room_id)
        decision_id = self._create(room_id, approver_ids=[approver.get_identifier()]).json()['decision_id']
        response = approver.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/vote',
                                   {'verdict': 'approved', 'comment': 'go'})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('approved', body['status'])
        self.assertEqual(approver.get_identifier(), body['decided_by_id'])
        self.assertEqual('approved', body['approvers'][0]['verdict'])

    def test_one_rejection_should_reject(self):
        room_id = self._room()
        first = self._member(room_id)
        second = self._member(room_id)
        decision_id = self._create(
            room_id, approver_ids=[first.get_identifier(), second.get_identifier()]
        ).json()['decision_id']
        response = first.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/vote',
                                {'verdict': 'approved'})
        self.assertEqual('proposed', response.json()['status'])
        response = second.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/vote',
                                 {'verdict': 'rejected'})
        self.assertEqual('rejected', response.json()['status'])

    def test_vote_by_non_approver_should_return_403(self):
        room_id = self._room()
        decision_id = self._create(room_id).json()['decision_id']
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/vote',
                                        {'verdict': 'approved'})
        self.assertEqual(403, response.status_code)

    def test_vote_should_reject_invalid_verdict(self):
        room_id = self._room()
        approver = self._member(room_id)
        decision_id = self._create(room_id, approver_ids=[approver.get_identifier()]).json()['decision_id']
        response = approver.create(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}/vote',
                                   {'verdict': 'maybe'})
        self.assertEqual(400, response.status_code)

    def test_patch_should_update_allow_listed_fields_only(self):
        room_id = self._room()
        decision_id = self._create(room_id).json()['decision_id']
        response = self._subject.patch(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}',
                                       {'title': 'Isolate DC02', 'number': 42, 'war_room_id': 999,
                                        'target_at': _FUTURE})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('Isolate DC02', body['title'])
        self.assertEqual(1, body['number'])
        self.assertEqual(room_id, body['war_room_id'])
        self.assertTrue(body['target_at'].startswith('2099-01-01T06:00:00'))

    def test_list_should_filter_by_status_and_query(self):
        room_id = self._room()
        self._create(room_id, title='Block egress')
        self._create(room_id, title='Reset passwords', status='approved')
        approved = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions',
                                     query_parameters={'status': 'approved'}).json()
        self.assertEqual(['Reset passwords'], [d['title'] for d in approved])
        searched = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions',
                                     query_parameters={'q': 'egress'}).json()
        self.assertEqual(['Block egress'], [d['title'] for d in searched])

    def test_supersede_should_mark_previous_decision(self):
        room_id = self._room()
        first_id = self._create(room_id, title='v1').json()['decision_id']
        second = self._create(room_id, title='v2', supersedes_id=first_id).json()
        self.assertEqual('D-1', second['supersedes_ref'])
        first = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions/{first_id}').json()
        self.assertEqual('superseded', first['status'])
        self.assertEqual(second['decision_id'], first['superseded_by_id'])

    def test_delete_should_return_204(self):
        room_id = self._room()
        decision_id = self._create(room_id).json()['decision_id']
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}')
        self.assertEqual(204, response.status_code)
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}')
        self.assertEqual(404, response.status_code)

    def test_user_without_war_room_access_should_get_403(self):
        room_id = self._room()
        decision_id = self._create(room_id).json()['decision_id']
        user = self._subject.create_dummy_user()
        self.assertEqual(403, user.get(f'/api/v2/war-rooms/{room_id}/decisions').status_code)
        self.assertEqual(403, user.get(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}').status_code)
        self.assertEqual(403, self._create(room_id, actor=user).status_code)

    def test_decision_of_another_room_should_return_404(self):
        room_id = self._room('Room A')
        other_room_id = self._room('Room B')
        decision_id = self._create(room_id).json()['decision_id']
        base = f'/api/v2/war-rooms/{other_room_id}/decisions/{decision_id}'
        self.assertEqual(404, self._subject.get(base).status_code)
        self.assertEqual(404, self._subject.patch(base, {'title': 'hijack'}).status_code)
        self.assertEqual(404, self._subject.create(f'{base}/implemented', {}).status_code)
        self.assertEqual(404, self._subject.create(f'{base}/vote', {'verdict': 'approved'}).status_code)
        self.assertEqual(404, self._subject.delete(base).status_code)
        still = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions/{decision_id}').json()
        self.assertEqual('Isolate DC01', still['title'])

    def test_supersedes_from_another_room_should_be_rejected(self):
        room_id = self._room('Room A')
        other_room_id = self._room('Room B')
        foreign_id = self._create(other_room_id).json()['decision_id']
        response = self._create(room_id, supersedes_id=foreign_id)
        self.assertEqual(400, response.status_code)

    def test_member_without_case_access_should_not_see_case_details(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        self._create(room_id, case_ids=[case_id])
        member = self._member(room_id)
        listed = member.get(f'/api/v2/war-rooms/{room_id}/decisions').json()
        self.assertEqual([{'case_id': case_id, 'accessible': False}], listed[0]['cases'])

    def test_asset_of_unreadable_case_should_be_rejected(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._subject.create(f'/api/v2/cases/{case_id}/assets',
                                        {'asset_type_id': 1, 'asset_name': 'dc01'}).json()['asset_id']
        member = self._member(room_id)
        response = self._create(room_id, actor=member, asset_ids=[asset_id])
        self.assertEqual(400, response.status_code)
        response = self._create(room_id, asset_ids=[asset_id])
        self.assertEqual(201, response.status_code)
        self.assertEqual('dc01', response.json()['assets'][0]['asset_name'])

    def test_member_should_not_link_an_unreadable_attached_case(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        member = self._member(room_id)
        response = self._create(room_id, actor=member, case_ids=[case_id])
        self.assertEqual(400, response.status_code)
        self.assertEqual(f'Case #{case_id} is not attached to this war room', response.json()['message'])

    def test_non_member_approver_should_get_a_generic_error(self):
        room_id = self._room()
        outsider = self._subject.create_dummy_user()
        response = self._create(room_id, approver_ids=[outsider.get_identifier()])
        self.assertEqual(400, response.status_code)
        self.assertNotIn(str(outsider.get_identifier()), response.json()['message'])

    def test_chat_decision_with_non_member_handle_should_not_reveal_the_user(self):
        room_id = self._room()
        outsider = self._subject.create_dummy_user()
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/chat',
                                        {'body': f'/decision Block TOR exits @{outsider.get_login()}'})
        self.assertEqual(400, response.status_code)
        self.assertEqual(f'Unknown or non-member user @{outsider.get_login()}', response.json()['message'])

    def test_chat_decision_should_be_a_candidate_until_registered(self):
        room_id = self._room()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/chat', {'body': '/decision Block TOR exits'})
        candidates = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions/chat-candidates').json()
        self.assertEqual(1, len(candidates))
        message_id = candidates[0]['message_id']
        response = self._create(room_id, title='Block TOR exits', chat_message_id=message_id)
        self.assertEqual(201, response.status_code)
        candidates = self._subject.get(f'/api/v2/war-rooms/{room_id}/decisions/chat-candidates').json()
        self.assertEqual([], candidates)
        response = self._create(room_id, title='Twice', chat_message_id=message_id)
        self.assertEqual(400, response.status_code)
