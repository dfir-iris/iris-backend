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

"""Integration tests for `/api/v2/manage/asset-stages` and the stage
endpoints of case assets.

The seeded stages are shared by the whole instance: every test only
deletes the stages it created and puts the original order back, so the
default taxonomy survives the suite.
"""

from unittest import TestCase
from uuid import uuid4

from iris import Iris

_STAGES = '/api/v2/manage/asset-stages'
_DEFAULT_STAGES = ('Identified', 'Isolated', 'Patched', 'Restored', 'Unpatched', 'Blocked')
_IDENTIFIER_FOR_NONEXISTENT_OBJECT = 123456789


class TestsRestAssetStages(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        self._initial_order = [stage['id'] for stage in self._list()]

    def tearDown(self):
        # Deleting the cases frees the stages their assets were in.
        self._subject.clear_database()
        for stage in self._list():
            if stage['id'] not in self._initial_order:
                self._subject.delete(f'{_STAGES}/{stage["id"]}')
        self._subject.create(f'{_STAGES}/reorder', {'ids': self._initial_order})

    def _list(self):
        return self._subject.get(_STAGES).json()

    def _stage_named(self, name):
        return next(stage for stage in self._list() if stage['name'] == name)

    def _create_stage(self, **kwargs):
        body = {'name': f'stage-{uuid4().hex[:12]}'}
        body.update(kwargs)
        return self._subject.create(_STAGES, body)

    def _create_asset(self):
        case_identifier = self._subject.create_dummy_case()
        body = {'asset_type_id': 1, 'asset_name': 'admin_laptop_test'}
        asset = self._subject.create(f'/api/v2/cases/{case_identifier}/assets', body).json()
        return case_identifier, asset['asset_id']

    def _set_stage(self, case_identifier, asset_identifier, body):
        return self._subject.update(f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}/stage', body)

    def test_default_stages_should_be_seeded(self):
        names = [stage['name'] for stage in self._list()]
        for name in _DEFAULT_STAGES:
            self.assertIn(name, names)

    def test_default_isolated_stage_should_be_optional(self):
        stages = {stage['name']: stage for stage in self._list()}
        self.assertNotIn('Analysed', stages)
        self.assertTrue(stages['Isolated']['is_optional'])
        self.assertFalse(stages['Patched']['is_optional'])

    def test_list_should_carry_in_use_count(self):
        for stage in self._list():
            self.assertIsInstance(stage['in_use_count'], int)

    def test_create_should_return_201_and_append_the_stage(self):
        response = self._create_stage(color='red', icon='bug', kind='exception', requires_reason=True)
        self.assertEqual(201, response.status_code)
        body = response.json()
        self.assertEqual('red', body['color'])
        self.assertEqual('bug', body['icon'])
        self.assertEqual('exception', body['kind'])
        self.assertTrue(body['requires_reason'])
        self.assertFalse(body['requires_decision'])
        self.assertFalse(body['is_optional'])
        self.assertEqual(0, body['in_use_count'])
        self.assertEqual(body['id'], self._list()[-1]['id'])

    def test_create_should_reject_duplicate_name_case_insensitively(self):
        response = self._subject.create(_STAGES, {'name': 'identified'})
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_unknown_color(self):
        response = self._create_stage(color='#ff0000')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_invalid_icon(self):
        response = self._create_stage(icon='<svg>')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_unknown_kind(self):
        response = self._create_stage(kind='finished')
        self.assertEqual(400, response.status_code)

    def test_create_should_reject_too_long_name(self):
        response = self._subject.create(_STAGES, {'name': 'x' * 65})
        self.assertEqual(400, response.status_code)

    def test_update_should_change_only_given_fields(self):
        stage = self._create_stage(color='blue').json()
        response = self._subject.update(f'{_STAGES}/{stage["id"]}', {'requires_decision': True})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body['requires_decision'])
        self.assertEqual('blue', body['color'])
        self.assertEqual(stage['name'], body['name'])

    def test_update_should_make_a_progress_stage_optional(self):
        stage = self._create_stage().json()
        response = self._subject.update(f'{_STAGES}/{stage["id"]}', {'is_optional': True})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()['is_optional'])

    def test_create_should_not_make_an_exception_stage_optional(self):
        response = self._create_stage(kind='exception', is_optional=True)
        self.assertEqual(201, response.status_code)
        self.assertFalse(response.json()['is_optional'])

    def test_create_should_reject_a_non_boolean_is_optional(self):
        response = self._create_stage(is_optional='yes')
        self.assertEqual(400, response.status_code)

    def test_update_should_return_404_when_absent(self):
        response = self._subject.update(f'{_STAGES}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}', {'color': 'red'})
        self.assertEqual(404, response.status_code)

    def test_delete_should_return_204(self):
        stage = self._create_stage().json()
        response = self._subject.delete(f'{_STAGES}/{stage["id"]}')
        self.assertEqual(204, response.status_code)
        self.assertNotIn(stage['id'], [entry['id'] for entry in self._list()])

    def test_delete_should_return_404_when_absent(self):
        response = self._subject.delete(f'{_STAGES}/{_IDENTIFIER_FOR_NONEXISTENT_OBJECT}')
        self.assertEqual(404, response.status_code)

    def test_delete_should_return_400_when_stage_is_in_use(self):
        stage = self._create_stage().json()
        case_identifier, asset_identifier = self._create_asset()
        self._set_stage(case_identifier, asset_identifier, {'stage_id': stage['id']})
        response = self._subject.delete(f'{_STAGES}/{stage["id"]}')
        self.assertEqual(400, response.status_code)
        self.assertEqual('Stage is in use by 1 asset', response.json()['message'])

    def test_delete_should_return_204_once_the_asset_using_it_is_deleted(self):
        stage = self._create_stage().json()
        case_identifier, asset_identifier = self._create_asset()
        self._set_stage(case_identifier, asset_identifier, {'stage_id': stage['id']})
        self._subject.delete(f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}')
        response = self._subject.delete(f'{_STAGES}/{stage["id"]}')
        self.assertEqual(204, response.status_code)

    def test_reorder_should_apply_the_new_order(self):
        reversed_order = list(reversed(self._initial_order))
        response = self._subject.create(f'{_STAGES}/reorder', {'ids': reversed_order})
        self.assertEqual(200, response.status_code)
        self.assertEqual(reversed_order, [stage['id'] for stage in response.json()])
        self.assertEqual(reversed_order, [stage['id'] for stage in self._list()])

    def test_reorder_should_reject_an_incomplete_list(self):
        response = self._subject.create(f'{_STAGES}/reorder', {'ids': self._initial_order[1:]})
        self.assertEqual(400, response.status_code)

    def test_preset_should_return_400_when_unknown(self):
        response = self._subject.create(f'{_STAGES}/presets/chaos', {})
        self.assertEqual(400, response.status_code)

    def test_preset_should_return_400_while_a_stage_is_in_use(self):
        case_identifier, asset_identifier = self._create_asset()
        self._set_stage(case_identifier, asset_identifier, {'stage_id': self._stage_named('Identified')['id']})
        response = self._subject.create(f'{_STAGES}/presets/compromise-simple', {})
        self.assertEqual(400, response.status_code)
        self.assertIn('Identified', [stage['name'] for stage in self._list()])

    def test_list_should_be_visible_to_a_user_without_permissions(self):
        user = self._subject.create_dummy_user()
        response = user.get(_STAGES)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self._initial_order, [stage['id'] for stage in response.json()])

    def test_create_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(_STAGES, {'name': f'stage-{uuid4().hex[:12]}'})
        self.assertEqual(403, response.status_code)

    def test_update_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.update(f'{_STAGES}/{self._initial_order[0]}', {'color': 'red'})
        self.assertEqual(403, response.status_code)

    def test_delete_should_return_403_for_a_non_administrator(self):
        stage = self._create_stage().json()
        user = self._subject.create_dummy_user()
        response = user.delete(f'{_STAGES}/{stage["id"]}')
        self.assertEqual(403, response.status_code)

    def test_reorder_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(f'{_STAGES}/reorder', {'ids': list(reversed(self._initial_order))})
        self.assertEqual(403, response.status_code)

    def test_preset_should_return_403_for_a_non_administrator(self):
        user = self._subject.create_dummy_user()
        response = user.create(f'{_STAGES}/presets/incident', {})
        self.assertEqual(403, response.status_code)


class TestsRestCaseAssetStage(TestCase):

    def setUp(self) -> None:
        self._subject = Iris()
        case_identifier = self._subject.create_dummy_case()
        body = {'asset_type_id': 1, 'asset_name': 'srv-01'}
        asset = self._subject.create(f'/api/v2/cases/{case_identifier}/assets', body).json()
        self._case_identifier = case_identifier
        self._asset_identifier = asset['asset_id']
        stages = self._subject.get(_STAGES).json()
        self._stages = {stage['name']: stage['id'] for stage in stages}

    def tearDown(self):
        self._subject.clear_database()

    def _path(self, suffix='stage', case_identifier=None, asset_identifier=None):
        case_identifier = case_identifier or self._case_identifier
        asset_identifier = asset_identifier or self._asset_identifier
        return f'/api/v2/cases/{case_identifier}/assets/{asset_identifier}/{suffix}'

    def _set(self, body):
        return self._subject.update(self._path(), body)

    def test_set_stage_should_return_the_asset_with_its_stage(self):
        response = self._set({'stage_id': self._stages['Isolated']})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(self._stages['Isolated'], body['stage_id'])
        self.assertEqual('Isolated', body['stage']['name'])
        self.assertIsNotNone(body['stage_updated_at'])

    def test_get_asset_should_include_the_stage(self):
        self._set({'stage_id': self._stages['Isolated']})
        response = self._subject.get(f'/api/v2/cases/{self._case_identifier}/assets/{self._asset_identifier}')
        self.assertEqual(self._stages['Isolated'], response.json()['stage_id'])

    def test_list_assets_should_filter_by_stage(self):
        self._set({'stage_id': self._stages['Isolated']})
        other = self._subject.create(f'/api/v2/cases/{self._case_identifier}/assets',
                                     {'asset_type_id': 1, 'asset_name': 'srv-02'}).json()
        response = self._subject.get(f'/api/v2/cases/{self._case_identifier}/assets',
                                     query_parameters={'stage_id': self._stages['Isolated']}).json()
        identifiers = [asset['asset_id'] for asset in response['data']]
        self.assertIn(self._asset_identifier, identifiers)
        self.assertNotIn(other['asset_id'], identifiers)

    def test_set_stage_should_require_stage_id(self):
        response = self._set({'reason': 'x'})
        self.assertEqual(400, response.status_code)

    def test_set_stage_should_reject_unknown_stage(self):
        response = self._set({'stage_id': _IDENTIFIER_FOR_NONEXISTENT_OBJECT})
        self.assertEqual(400, response.status_code)

    def test_set_stage_should_reject_non_integer_stage(self):
        response = self._set({'stage_id': str(self._stages['Isolated'])})
        self.assertEqual(400, response.status_code)

    def test_set_stage_should_enforce_requires_reason(self):
        response = self._set({'stage_id': self._stages['Unpatched']})
        self.assertEqual(400, response.status_code)
        self.assertEqual('Stage "Unpatched" requires a reason', response.json()['message'])

    def test_set_stage_should_accept_a_reason(self):
        response = self._set({'stage_id': self._stages['Unpatched'], 'reason': 'legacy OS'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('legacy OS', response.json()['stage_reason'])

    def test_set_stage_should_reject_a_decision_of_an_unattached_war_room(self):
        response = self._set({'stage_id': self._stages['Isolated'],
                              'decision_id': _IDENTIFIER_FOR_NONEXISTENT_OBJECT})
        self.assertEqual(400, response.status_code)

    def test_clear_stage_should_reset_reason(self):
        self._set({'stage_id': self._stages['Unpatched'], 'reason': 'legacy OS'})
        response = self._set({'stage_id': None})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertIsNone(body['stage_id'])
        self.assertIsNone(body['stage_reason'])

    def test_history_should_list_changes_newest_first(self):
        self._set({'stage_id': self._stages['Identified']})
        self._set({'stage_id': self._stages['Unpatched'], 'reason': 'legacy OS'})
        response = self._subject.get(self._path('stage-history'))
        self.assertEqual(200, response.status_code)
        history = response.json()
        self.assertEqual(2, len(history))
        latest = history[0]
        self.assertEqual('Identified', latest['from_stage_name'])
        self.assertEqual('Unpatched', latest['to_stage_name'])
        self.assertEqual('legacy OS', latest['reason'])
        self.assertIsNotNone(latest['changed_by_name'])
        self.assertIsNone(history[1]['from_stage_name'])

    def test_delete_asset_should_return_204_when_it_has_a_stage_history(self):
        self._set({'stage_id': self._stages['Identified']})
        self._set({'stage_id': self._stages['Unpatched'], 'reason': 'legacy OS'})
        response = self._subject.delete(f'/api/v2/cases/{self._case_identifier}/assets/{self._asset_identifier}')
        self.assertEqual(204, response.status_code)
        response = self._subject.get(self._path('stage-history'))
        self.assertEqual(404, response.status_code)

    def test_setting_the_same_stage_twice_should_record_once(self):
        self._set({'stage_id': self._stages['Identified']})
        self._set({'stage_id': self._stages['Identified']})
        history = self._subject.get(self._path('stage-history')).json()
        self.assertEqual(1, len(history))

    def test_set_stage_should_return_404_for_an_asset_of_another_case(self):
        other_case_identifier = self._subject.create_dummy_case()
        response = self._subject.update(self._path(case_identifier=other_case_identifier),
                                        {'stage_id': self._stages['Isolated']})
        self.assertEqual(404, response.status_code)

    def test_history_should_return_404_for_an_asset_of_another_case(self):
        other_case_identifier = self._subject.create_dummy_case()
        response = self._subject.get(self._path('stage-history', case_identifier=other_case_identifier))
        self.assertEqual(404, response.status_code)

    def test_set_stage_should_return_404_when_case_is_absent(self):
        response = self._subject.update(self._path(case_identifier=_IDENTIFIER_FOR_NONEXISTENT_OBJECT),
                                        {'stage_id': self._stages['Isolated']})
        self.assertEqual(404, response.status_code)

    def test_set_stage_should_return_403_without_case_access(self):
        user = self._subject.create_dummy_user()
        response = user.update(self._path(), {'stage_id': self._stages['Isolated']})
        self.assertEqual(403, response.status_code)

    def test_set_stage_should_return_403_for_a_read_only_user(self):
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, self._case_identifier, access_level=0x2)
        response = user.update(self._path(), {'stage_id': self._stages['Isolated']})
        self.assertEqual(403, response.status_code)

    def test_history_should_be_readable_by_a_read_only_user(self):
        self._set({'stage_id': self._stages['Identified']})
        user = self._subject.create_dummy_user()
        self._subject.grant_case_access(user, self._case_identifier, access_level=0x2)
        response = user.get(self._path('stage-history'))
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.json()))

    def test_history_should_return_403_without_case_access(self):
        user = self._subject.create_dummy_user()
        response = user.get(self._path('stage-history'))
        self.assertEqual(403, response.status_code)
