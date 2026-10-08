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

"""Integration tests for `/api/v2/war-rooms/<id>/scope`."""

from unittest import TestCase

from iris import Iris

_WAR_ROOMS_READ_WRITE = 0x8000 | 0x10000


class TestsRestWarRoomScope(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()

    def tearDown(self):
        self._subject.clear_database()

    def _room(self, name='Scope Room'):
        return self._subject.create('/api/v2/war-rooms', {'name': name}).json()['war_room_id']

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

    def _flag_id(self):
        # Other suites may apply presets: pick any flag without extra requirements.
        flags = self._subject.get('/api/v2/manage/asset-flags').json()
        return next(flag['id'] for flag in flags
                    if not flag['requires_reason'] and not flag['requires_decision'])

    def test_list_assets_should_return_assets_of_attached_cases(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'DC01')
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/assets')
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body['truncated'])
        self.assertEqual(5000, body['limit'])
        self.assertEqual([case_id], [case['case_id'] for case in body['cases']])
        self.assertEqual(1, len(body['data']))
        asset = body['data'][0]
        self.assertEqual(asset_id, asset['asset_id'])
        self.assertEqual(case_id, asset['case_id'])
        self.assertEqual('1:dc01', asset['group_key'])
        self.assertEqual(0, asset['ioc_count'])

    def test_list_assets_should_ignore_assets_of_unattached_cases(self):
        room_id = self._room()
        self._attach_case(room_id)
        other_case_id = self._subject.create_dummy_case()
        self._asset(other_case_id, 'not-in-scope')
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/assets')
        self.assertNotIn('not-in-scope', response.text)

    def test_list_iocs_should_return_iocs_of_attached_cases(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        ioc_id = self._ioc(case_id, '8.8.8.8')
        body = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/iocs').json()
        self.assertEqual([ioc_id], [ioc['ioc_id'] for ioc in body['data']])

    def test_list_iocs_should_group_the_same_indicator_across_cases(self):
        room_id = self._room()
        first_case_id = self._attach_case(room_id)
        second_case_id = self._attach_case(room_id)
        self._ioc(first_case_id, 'Evil-Host.example')
        self._ioc(second_case_id, 'evil-host.example')
        body = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/iocs').json()
        self.assertEqual(1, len({ioc['group_key'] for ioc in body['data']}))

    def test_push_asset_should_create_then_report_exists(self):
        room_id = self._room()
        source_case_id = self._attach_case(room_id)
        target_case_id = self._attach_case(room_id)
        asset_id = self._asset(source_case_id, 'srv-push')
        body = {'asset_ids': [asset_id], 'case_ids': [target_case_id]}

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/push', body)
        self.assertEqual(200, response.status_code)
        row = response.json()['results'][0]
        self.assertEqual('created', row['status'])
        new_asset_id = row['new_asset_id']
        response = self._subject.get(f'/api/v2/cases/{target_case_id}/assets/{new_asset_id}')
        self.assertEqual('srv-push', response.json()['asset_name'])

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/push', body)
        row = response.json()['results'][0]
        self.assertEqual('exists', row['status'])
        self.assertEqual(new_asset_id, row['existing_asset_id'])

    def test_push_asset_to_unattached_case_should_be_denied(self):
        room_id = self._room()
        source_case_id = self._attach_case(room_id)
        outside_case_id = self._subject.create_dummy_case()
        asset_id = self._asset(source_case_id, 'srv-outside')
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/push',
                                        {'asset_ids': [asset_id], 'case_ids': [outside_case_id]})
        self.assertEqual(200, response.status_code)
        self.assertEqual('denied', response.json()['results'][0]['status'])
        assets = self._subject.get(f'/api/v2/cases/{outside_case_id}/assets').json()['data']
        self.assertEqual([], assets)

    def test_push_ioc_should_create_then_report_exists(self):
        room_id = self._room()
        source_case_id = self._attach_case(room_id)
        target_case_id = self._attach_case(room_id)
        ioc_id = self._ioc(source_case_id, '1.1.1.1')
        body = {'ioc_ids': [ioc_id], 'case_ids': [target_case_id]}
        first = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/iocs/push', body).json()
        self.assertEqual('created', first['results'][0]['status'])
        second = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/iocs/push', body).json()
        self.assertEqual('exists', second['results'][0]['status'])

    def test_push_ioc_should_report_exists_for_the_same_value_in_another_case(self):
        room_id = self._room()
        source_case_id = self._attach_case(room_id)
        target_case_id = self._attach_case(room_id)
        ioc_id = self._ioc(source_case_id, 'Bad-Domain.example')
        existing_id = self._ioc(target_case_id, 'bad-domain.example')
        body = {'ioc_ids': [ioc_id], 'case_ids': [target_case_id]}
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/iocs/push', body).json()
        self.assertEqual('exists', response['results'][0]['status'])
        self.assertEqual(existing_id, response['results'][0]['existing_ioc_id'])

    def test_create_asset_should_create_in_every_target_case(self):
        room_id = self._room()
        first_case_id = self._attach_case(room_id)
        second_case_id = self._attach_case(room_id)
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets', {
            'asset': {'asset_name': 'new-host', 'asset_type_id': 1},
            'case_ids': [first_case_id, second_case_id],
        })
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(['created', 'created'], [row['status'] for row in body['results']])
        self.assertEqual(1, len(body['customers']))

    def test_create_asset_with_invalid_payload_should_return_400(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets', {
            'asset': {'asset_name': 'x', 'asset_type_id': 'one'}, 'case_ids': [case_id],
        })
        self.assertEqual(400, response.status_code)

    def test_member_without_case_access_should_see_nothing_from_that_case(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        self._asset(case_id, 'secret-host')
        self._ioc(case_id, '9.9.9.9')
        member = self._member(room_id)
        assets = member.get(f'/api/v2/war-rooms/{room_id}/scope/assets')
        self.assertEqual(200, assets.status_code)
        self.assertEqual([], assets.json()['data'])
        self.assertEqual([], assets.json()['cases'])
        self.assertNotIn('secret-host', assets.text)
        iocs = member.get(f'/api/v2/war-rooms/{room_id}/scope/iocs')
        self.assertNotIn('9.9.9.9', iocs.text)
        export = member.get(f'/api/v2/war-rooms/{room_id}/scope/iocs/export', {'format': 'txt'})
        self.assertEqual(200, export.status_code)
        self.assertNotIn('9.9.9.9', export.text)

    def test_member_without_case_access_should_not_push_from_that_case(self):
        room_id = self._room()
        source_case_id = self._attach_case(room_id)
        target_case_id = self._attach_case(room_id)
        asset_id = self._asset(source_case_id, 'secret-host')
        member = self._member(room_id)
        response = member.create(f'/api/v2/war-rooms/{room_id}/scope/assets/push',
                                 {'asset_ids': [asset_id], 'case_ids': [target_case_id]})
        self.assertEqual(200, response.status_code)
        self.assertEqual('denied', response.json()['results'][0]['status'])
        self.assertNotIn('secret-host', response.text)

    def test_user_without_war_room_access_should_get_403(self):
        room_id = self._room()
        user = self._subject.create_dummy_user()
        self.assertEqual(403, user.get(f'/api/v2/war-rooms/{room_id}/scope/assets').status_code)

    def test_staging_lifecycle(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/staging', {
            'object_type': 'asset',
            'payload': {'asset_name': 'staged-host', 'asset_type_id': 1},
            'proposed_case_ids': [case_id],
            'note': 'seen in chat',
        })
        self.assertEqual(201, response.status_code)
        staged_id = response.json()['id']

        staged = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/staging').json()
        self.assertEqual([staged_id], [row['id'] for row in staged])

        response = self._subject.patch(f'/api/v2/war-rooms/{room_id}/scope/staging/{staged_id}',
                                       {'note': 'updated'})
        self.assertEqual('updated', response.json()['note'])

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/staging/{staged_id}/push', {})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual('created', body['results'][0]['status'])
        self.assertTrue(body['staged_deleted'])
        self.assertEqual([], self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/staging').json())

    def test_staging_invalid_payload_should_return_400(self):
        room_id = self._room()
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/staging',
                                        {'object_type': 'asset', 'payload': {'asset_name': 'x'}})
        self.assertEqual(400, response.status_code)

    def test_delete_staged_object(self):
        room_id = self._room()
        staged_id = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/staging', {
            'object_type': 'ioc', 'payload': {'ioc_value': '2.2.2.2', 'ioc_type_id': 1},
        }).json()['id']
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/scope/staging/{staged_id}')
        self.assertEqual(204, response.status_code)
        response = self._subject.delete(f'/api/v2/war-rooms/{room_id}/scope/staging/{staged_id}')
        self.assertEqual(404, response.status_code)

    def test_bulk_flag_should_update_then_report_unchanged(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'srv-flag')
        outside_case_id = self._subject.create_dummy_case()
        outside_asset_id = self._asset(outside_case_id, 'srv-outside')
        flag_id = self._flag_id()
        body = {'asset_ids': [asset_id, outside_asset_id], 'flag_id': flag_id}

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/flags', body)
        self.assertEqual(200, response.status_code)
        statuses = {row['asset_id']: row['status'] for row in response.json()['results']}
        self.assertEqual({asset_id: 'updated', outside_asset_id: 'denied'}, statuses)

        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/flags', body)
        statuses = {row['asset_id']: row['status'] for row in response.json()['results']}
        self.assertEqual('unchanged', statuses[asset_id])

        asset = self._subject.get(f'/api/v2/cases/{case_id}/assets/{asset_id}').json()
        self.assertEqual([flag_id], [entry['flag_id'] for entry in asset['flags']])
        outside = self._subject.get(f'/api/v2/cases/{outside_case_id}/assets/{outside_asset_id}').json()
        self.assertEqual([], outside['flags'])

    def test_bulk_flag_clear_should_remove_the_flag(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'srv-flag')
        flag_id = self._flag_id()
        path = f'/api/v2/war-rooms/{room_id}/scope/assets/flags'
        self._subject.create(path, {'asset_ids': [asset_id], 'flag_id': flag_id})

        response = self._subject.create(path, {'asset_ids': [asset_id], 'flag_id': flag_id, 'action': 'clear'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(['updated'], [row['status'] for row in response.json()['results']])
        asset = self._subject.get(f'/api/v2/cases/{case_id}/assets/{asset_id}').json()
        self.assertEqual([], asset['flags'])

        history = self._subject.get(f'/api/v2/cases/{case_id}/assets/{asset_id}/flag-history').json()
        self.assertEqual(['cleared', 'set'], [entry['action'] for entry in history])
        self.assertEqual(room_id, history[0]['war_room_id'])

    def test_bulk_flag_should_reject_an_unknown_action(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        asset_id = self._asset(case_id, 'srv-flag')
        response = self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/flags',
                                        {'asset_ids': [asset_id], 'flag_id': self._flag_id(), 'action': 'toggle'})
        self.assertEqual(400, response.status_code)

    def test_list_assets_should_carry_and_filter_the_flags(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        flagged_id = self._asset(case_id, 'srv-flagged')
        other_id = self._asset(case_id, 'srv-other')
        flag_id = self._flag_id()
        self._subject.create(f'/api/v2/war-rooms/{room_id}/scope/assets/flags',
                             {'asset_ids': [flagged_id], 'flag_id': flag_id})
        path = f'/api/v2/war-rooms/{room_id}/scope/assets'

        assets = {asset['asset_id']: asset for asset in self._subject.get(path).json()['data']}
        self.assertEqual([flag_id], [entry['flag_id'] for entry in assets[flagged_id]['flags']])
        self.assertEqual([], assets[other_id]['flags'])

        with_flag = self._subject.get(path, {'flag_id': flag_id}).json()['data']
        self.assertEqual([flagged_id], [asset['asset_id'] for asset in with_flag])
        unflagged = self._subject.get(path, {'flag_id': 'none'}).json()['data']
        self.assertEqual([other_id], [asset['asset_id'] for asset in unflagged])
        without = self._subject.get(path, {'without_flag_id': flag_id}).json()['data']
        self.assertEqual([other_id], [asset['asset_id'] for asset in without])

    def test_export_iocs_should_return_attachment(self):
        room_id = self._room()
        case_id = self._attach_case(room_id)
        self._ioc(case_id, '3.3.3.3')
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/iocs/export', {'format': 'csv'})
        self.assertEqual(200, response.status_code)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('3.3.3.3', response.text)
        response = self._subject.get(f'/api/v2/war-rooms/{room_id}/scope/iocs/export', {'format': 'xml'})
        self.assertEqual(400, response.status_code)
