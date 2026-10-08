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

"""Integration tests for `/api/v2/manage/asset-flags` and the flag
endpoints of case assets.

The seeded flags are shared by the whole instance: every test only
deletes the flags it created and puts the original order back, so the
default taxonomy survives the suite.
"""

import json
from unittest import TestCase
from uuid import uuid4

from iris import Iris

_FLAGS = '/api/v2/manage/asset-flags'
_DEFAULT_FLAGS = ('Isolated', 'Credentials reset', 'Patched', 'Reimaged', 'Monitored', 'Restored',
                  "Can't be patched", 'Blocked')
_STATUS_TIMELINE = 'Asset status'
_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestAssetFlags(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        self._initial_order = [flag['id'] for flag in self._list()]

    def tearDown(self):
        # Deleting the cases frees the flags set on their assets.
        self._subject.clear_database()
        for flag in self._list():
            if flag['id'] not in self._initial_order:
                self._subject.delete(f'{_FLAGS}/{flag["id"]}')
        self._subject.create(f'{_FLAGS}/reorder', {'ids': self._initial_order})

    def _list(self):
        return self._subject.get(_FLAGS).json()

    def _flag_named(self, name):
        return next(flag for flag in self._list() if flag['name'] == name)

    def _create_flag(self, **kwargs):
        body = {'name': f'flag-{uuid4().hex[:12]}'}
        body.update(kwargs)
        return self._subject.create(_FLAGS, body)

    def _create_asset(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'asset_type_id': 1, 'asset_name': 'admin_laptop_test'}
        asset = self._subject.create(f'/api/v2/cases/{case_identifier}/assets', body).json()
        return case_identifier, asset['asset_id']

    def _set_flag(self, case_identifier, asset_identifier, flag_id, body=None):
        return self._subject.update(f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}/flags/{flag_id}',
                                    body or {})

    def test_default_flags_should_be_seeded(self):
        names = [flag['name'] for flag in self._list()]
        for name in _DEFAULT_FLAGS:
            self.assertIn(name, names)

    def test_default_exception_flags_should_require_a_reason(self):
        flags = {flag['name']: flag for flag in self._list()}
        self.assertEqual('exception', flags["Can't be patched"]['kind'])
        self.assertTrue(flags["Can't be patched"]['requires_reason'])
        self.assertEqual('status', flags['Isolated']['kind'])
        self.assertFalse(flags['Isolated']['requires_reason'])

    def test_list_should_carry_in_use_count(self):
        for flag in self._list():
            self.assertIsInstance(flag['in_use_count'], int)

    def test_create_should_return_201_and_append_the_flag(self):
        response = self._create_flag(color='red', icon='bug', kind='exception', requires_reason=True,
                                     description='Under legal hold')
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual('red', body['color'])
        self.assertEqual('bug', body['icon'])
        self.assertEqual('exception', body['kind'])
        self.assertEqual('Under legal hold', body['description'])
        self.assertTrue(body['requires_reason'])
        self.assertFalse(body['requires_decision'])
        self.assertEqual(0, body['in_use_count'])
        self.assertEqual(body['id'], self._list()[-1]['id'])

    def test_create_should_default_to_a_slate_status_flag(self):
        body = self._create_flag().json()
        self.assertEqual('slate', body['color'])
        self.assertEqual('status', body['kind'])

    def test_create_should_reject_duplicate_name_case_insensitively(self):
        response = self._subject.create(_FLAGS, {'name': 'isolated'})
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_unknown_color(self):
        response = self._create_flag(color='#ff0000')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_invalid_icon(self):
        response = self._create_flag(icon='<svg>')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_unknown_kind(self):
        response = self._create_flag(kind='progress')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_too_long_name(self):
        response = self._subject.create(_FLAGS, {'name': 'x' * 65})
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_a_non_boolean_requires_reason(self):
        response = self._create_flag(requires_reason='yes')
        self.assertEqual(400, response.status_code)

    def test_update_should_change_only_given_fields(self):
        flag = self._create_flag(color='blue').json()
        response = self._subject.update(f'{_FLAGS}/{flag["id"]}', {'requires_decision': True})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body['requires_decision'])
        self.assertEqual('blue', body['color'])
        self.assertEqual(flag['name'], body['name'])

    def test_update_should_return_404_when_absent(self):
        response = self._subject.update(f'{_FLAGS}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {'color': 'red'})
        self.assertEqual(404, response.status_code)

    def test_delete_should_return_204(self):
        flag = self._create_flag().json()
        response = self._subject.delete(f'{_FLAGS}/{flag["id"]}')
        self.assertEqual(204, response.status_code)
        self.assertNotIn(flag['id'], [entry['id'] for entry in self._list()])

    def test_delete_should_return_404_when_absent(self):
        response = self._subject.delete(f'{_FLAGS}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_should_return_400_when_flag_is_in_use(self):
        flag = self._create_flag().json()
        case_identifier, asset_identifier = self._create_asset()
        self._set_flag(case_identifier, asset_identifier, flag['id'])
        response = self._subject.delete(f'{_FLAGS}/{flag["id"]}')
        self.assertEqual(400, response.status_code)
        self.assertEqual('Flag is set on 1 asset', response.json()['message'])

    def test_delete_should_return_204_once_the_asset_using_it_is_deleted(self):
        flag = self._create_flag().json()
        case_identifier, asset_identifier = self._create_asset()
        self._set_flag(case_identifier, asset_identifier, flag['id'])
        self._subject.delete(f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}')
        response = self._subject.delete(f'{_FLAGS}/{flag["id"]}')
        self.assertEqual(204, response.status_code)

    def test_delete_should_return_204_once_the_flag_is_removed(self):
        flag = self._create_flag().json()
        case_identifier, asset_identifier = self._create_asset()
        self._set_flag(case_identifier, asset_identifier, flag['id'])
        self._subject.delete(f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}/flags/{flag["id"]}')
        response = self._subject.delete(f'{_FLAGS}/{flag["id"]}')
        self.assertEqual(204, response.status_code)

    def test_reorder_should_apply_the_new_order(self):
        reversed_order = list(reversed(self._initial_order))
        response = self._subject.create(f'{_FLAGS}/reorder', {'ids': reversed_order})
        self.assertEqual(200, response.status_code)
        self.assertEqual(reversed_order, [flag['id'] for flag in response.json()])
        self.assertEqual(reversed_order, [flag['id'] for flag in self._list()])

    def test_reorder_should_reject_an_incomplete_list(self):
        response = self._subject.create(f'{_FLAGS}/reorder', {'ids': self._initial_order[1:]})
        self.assertEqual(400, response.status_code)

    def test_preset_should_return_400_when_unknown(self):
        response = self._subject.create(f'{_FLAGS}/presets/chaos', {})
        self.assertEqual(400, response.status_code)

    def test_preset_should_return_400_while_a_flag_is_in_use(self):
        case_identifier, asset_identifier = self._create_asset()
        self._set_flag(case_identifier, asset_identifier, self._flag_named('Isolated')['id'])
        response = self._subject.create(f'{_FLAGS}/presets/vulnerability', {})
        self.assertEqual(400, response.status_code)
        self.assertIn('Isolated', [flag['name'] for flag in self._list()])

    def test_list_should_be_visible_to_a_user_without_permissions(self):
        user = self._subject.create_dummy_user()
        response = user.get(_FLAGS)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self._initial_order, [flag['id'] for flag in response.json()])

    def test_create_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(_FLAGS, {'name': f'flag-{uuid4().hex[:12]}'})
        self.assertEqual(403, response.status_code)

    def test_update_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.update(f'{_FLAGS}/{self._initial_order[0]}', {'color': 'red'})
        self.assertEqual(403, response.status_code)

    def test_delete_should_return_403_for_a_non_administrator(self):
        flag = self._create_flag().json()
        user = self._subject.create_dummy_user()
        response = user.delete(f'{_FLAGS}/{flag["id"]}')
        self.assertEqual(403, response.status_code)

    def test_reorder_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(f'{_FLAGS}/reorder', {'ids': list(reversed(self._initial_order))})
        self.assertEqual(403, response.status_code)

    def test_preset_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(f'{_FLAGS}/presets/incident', {})
        self.assertEqual(403, response.status_code)


class TestsRestCaseAssetFlags(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        case_identifier = self._subject.create_dummy_case()
        body = {'asset_type_id': 1, 'asset_name': 'srv-01'}
        asset = self._subject.create(f'/api/v2/cases/{case_identifier}/assets', body).json()
        self._case_identifier = case_identifier
        self._asset_identifier = asset['asset_id']
        flags = self._subject.get(_FLAGS).json()
        self._flags = {flag['name']: flag['id'] for flag in flags}

    def tearDown(self):
        self._subject.clear_database()

    def _asset_path(self, case_identifier=None, asset_identifier=None):
        case_identifier = case_identifier or self._case_identifier
        asset_identifier = asset_identifier or self._asset_identifier
        return f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}'

    def _flag_path(self, name, case_identifier=None):
        return f'{self._asset_path(case_identifier)}/flags/{self._flags[name]}'

    def _history_path(self, case_identifier=None):
        return f'{self._asset_path(case_identifier)}/flag-history'

    def _set(self, name, body=None):
        return self._subject.update(self._flag_path(name), body or {})

    def _clear(self, name):
        return self._subject.delete(self._flag_path(name))

    def _status_timeline(self):
        timelines = self._subject.get(f'/api/v2/cases/{self._case_identifier}/timelines').json()
        return next((timeline for timeline in timelines if timeline['name'] == _STATUS_TIMELINE), None)

    def _event(self, event_id):
        return self._subject.get(f'/api/v2/cases/{self._case_identifier}/events/{event_id}').json()

    def _flags_of(self, asset):
        return {entry['flag']['name']: entry for entry in asset['flags']}

    def test_set_flag_should_return_the_asset_with_its_flags(self):
        response = self._set('Isolated')
        self.assertEqual(200, response.status_code)
        flags = self._flags_of(response.json())
        self.assertEqual(['Isolated'], list(flags))
        self.assertIsNotNone(flags['Isolated']['set_at'])
        self.assertIsNotNone(flags['Isolated']['event_id'])

    def test_flags_should_combine(self):
        self._set('Isolated')
        response = self._set('Credentials reset')
        self.assertEqual({'Isolated', 'Credentials reset'}, set(self._flags_of(response.json())))

    def test_get_asset_should_include_the_flags(self):
        self._set('Isolated')
        response = self._subject.get(self._asset_path())
        self.assertEqual({'Isolated'}, set(self._flags_of(response.json())))

    def test_list_assets_should_include_the_flags(self):
        self._set('Isolated')
        response = self._subject.get(f'/api/v2/cases/{self._case_identifier}/assets').json()
        asset = next(entry for entry in response['data'] if entry['asset_id'] == self._asset_identifier)
        self.assertEqual({'Isolated'}, set(self._flags_of(asset)))

    def test_list_assets_should_filter_by_flag(self):
        body = {'asset_type_id': 1, 'asset_name': 'srv-02'}
        self._subject.create(f'/api/v2/cases/{self._case_identifier}/assets', body)
        self._set('Isolated')
        self._set('Credentials reset')
        conditions = json.dumps([{'field': 'flags.flag_id', 'operator': 'eq', 'value': self._flags['Isolated']}])
        response = self._subject.get(f'/api/v2/cases/{self._case_identifier}/assets',
                                     query_parameters={'custom_conditions': conditions}).json()
        self.assertEqual([self._asset_identifier], [entry['asset_id'] for entry in response['data']])

    def test_update_asset_should_not_write_the_flags(self):
        self._set('Isolated')
        response = self._subject.update(self._asset_path(), {'asset_name': 'srv-01b', 'flags': []})
        self.assertEqual(200, response.status_code)
        self.assertEqual({'Isolated'}, set(self._flags_of(response.json())))

    def test_set_flag_should_create_the_asset_status_timeline_and_event(self):
        self.assertIsNone(self._status_timeline())
        flag = self._flags_of(self._set('Isolated', {'reason': 'EDR containment'}).json())['Isolated']
        timeline = self._status_timeline()
        self.assertIsNotNone(timeline)
        event = self._event(flag['event_id'])
        self.assertEqual('srv-01: Isolated', event['event_title'])
        self.assertEqual('EDR containment', event['event_content'])
        self.assertEqual([timeline['timeline_id']], event['timeline_ids'])
        self.assertIn(self._asset_identifier, event['event_assets'])

    def test_set_flag_should_use_the_given_date_for_the_event(self):
        flag = self._flags_of(self._set('Isolated', {'date': '2026-03-04T05:06:07+02:00'}).json())['Isolated']
        self.assertTrue(self._event(flag['event_id'])['event_date'].startswith('2026-03-04T03:06:07'))

    def test_set_flag_should_reject_an_invalid_date(self):
        response = self._set('Isolated', {'date': 'yesterday'})
        self.assertEqual(400, response.status_code)

    def test_updating_the_reason_should_update_the_same_event(self):
        first = self._flags_of(self._set("Can't be patched", {'reason': 'legacy OS'}).json())["Can't be patched"]
        second = self._flags_of(self._set("Can't be patched", {'reason': 'vendor EOL'}).json())["Can't be patched"]
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertEqual('vendor EOL', second['reason'])
        self.assertEqual('vendor EOL', self._event(second['event_id'])['event_content'])

    def test_clear_flag_should_remove_it_and_add_a_removed_event(self):
        set_event_id = self._flags_of(self._set('Isolated').json())['Isolated']['event_id']
        response = self._clear('Isolated')
        self.assertEqual(200, response.status_code)
        self.assertEqual({}, self._flags_of(response.json()))
        history = self._subject.get(self._history_path()).json()
        self.assertEqual(['cleared', 'set'], [entry['action'] for entry in history])
        removed = self._event(history[0]['event_id'])
        self.assertEqual('srv-01: Isolated removed', removed['event_title'])
        self.assertNotEqual(set_event_id, history[0]['event_id'])

    def test_clear_flag_should_be_a_no_op_when_not_set(self):
        response = self._clear('Isolated')
        self.assertEqual(200, response.status_code)
        self.assertEqual([], self._subject.get(self._history_path()).json())

    def test_set_flag_should_reject_unknown_flag(self):
        response = self._subject.update(f'{self._asset_path()}/flags/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {})
        self.assertEqual(400, response.status_code)

    def test_set_flag_should_enforce_requires_reason(self):
        response = self._set("Can't be patched")
        self.assertEqual(400, response.status_code)
        self.assertEqual('Flag "Can\'t be patched" requires a reason', response.json()['message'])

    def test_set_flag_should_accept_a_reason(self):
        response = self._set("Can't be patched", {'reason': 'legacy OS'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('legacy OS', self._flags_of(response.json())["Can't be patched"]['reason'])

    def test_set_flag_should_reject_a_non_string_reason(self):
        response = self._set('Isolated', {'reason': 12})
        self.assertEqual(400, response.status_code)

    def test_set_flag_should_reject_a_decision_of_an_unattached_war_room(self):
        response = self._set('Isolated', {'decision_id': _IDENTIFIER_FOR_NONEXISTENT_OBJECT})
        self.assertEqual(400, response.status_code)

    def test_history_should_list_changes_newest_first(self):
        self._set('Isolated')
        self._set("Can't be patched", {'reason': 'legacy OS'})
        response = self._subject.get(self._history_path())
        self.assertEqual(200, response.status_code)
        history = response.json()
        self.assertEqual(2, len(history))
        latest = history[0]
        self.assertEqual("Can't be patched", latest['flag_name'])
        self.assertEqual('set', latest['action'])
        self.assertEqual('legacy OS', latest['reason'])
        self.assertIsNotNone(latest['changed_by_name'])
        self.assertEqual('Isolated', history[1]['flag_name'])

    def test_setting_the_same_flag_twice_should_record_once(self):
        self._set('Isolated')
        self._set('Isolated')
        history = self._subject.get(self._history_path()).json()
        self.assertEqual(1, len(history))

    def test_delete_asset_should_return_204_when_it_has_a_flag_history(self):
        self._set('Isolated')
        self._set("Can't be patched", {'reason': 'legacy OS'})
        self._clear('Isolated')
        response = self._subject.delete(self._asset_path())
        self.assertEqual(204, response.status_code)
        response = self._subject.get(self._history_path())
        self.assertEqual(404, response.status_code)

    def test_set_flag_should_return_404_for_an_asset_of_another_case(self):
        other_case_identifier = self._subject.create_dummy_case()
        response = self._subject.update(self._flag_path('Isolated', case_identifier=other_case_identifier), {})
        self.assertEqual(404, response.status_code)

    def test_history_should_return_404_for_an_asset_of_another_case(self):
        other_case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(self._history_path(case_identifier=other_case_identifier))
        self.assertEqual(404, response.status_code)

    def test_set_flag_should_return_404_when_case_is_absent(self):
        response = self._subject.update(self._flag_path('Isolated', case_identifier=_IDENTIFIER_FOR_NONEXISTENT_OBJECT),
                                        {})
        self.assertEqual(404, response.status_code)

    def test_set_flag_should_return_403_without_case_access(self):
        user = self._subject.create_dummy_user()
        response = user.update(self._flag_path('Isolated'), {})
        self.assertEqual(403, response.status_code)

    def test_set_flag_should_return_403_for_a_read_only_user(self):
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, self._case_identifier, access_level=0x2)
        response = user.update(self._flag_path('Isolated'), {})
        self.assertEqual(403, response.status_code)

    def test_clear_flag_should_return_403_for_a_read_only_user(self):
        self._set('Isolated')
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, self._case_identifier, access_level=0x2)
        response = user.delete(self._flag_path('Isolated'))
        self.assertEqual(403, response.status_code)

    def test_history_should_be_readable_by_a_read_only_user(self):
        self._set('Isolated')
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, self._case_identifier, access_level=0x2)
        response = user.get(self._history_path())
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.json()))

    def test_history_should_return_403_without_case_access(self):
        user = self._subject.create_dummy_user()
        response = user.get(self._history_path())
        self.assertEqual(403, response.status_code)
