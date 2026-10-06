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

"""Asset stage taxonomy validation, presets, deletion and the stage of a
case asset. Every datamgmt helper is patched; models are transient."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.asset_stages import _asset_stages_validate
from app.business.asset_stages import asset_stages_apply_preset
from app.business.asset_stages import asset_stages_create
from app.business.asset_stages import asset_stages_decision_war_room_id
from app.business.asset_stages import asset_stages_delete
from app.business.asset_stages import asset_stages_history
from app.business.asset_stages import asset_stages_list
from app.business.asset_stages import asset_stages_reorder
from app.business.asset_stages import asset_stages_set_for_asset
from app.business.asset_stages import asset_stages_update
from app.models.assets import AssetStage
from app.models.assets import CaseAssets
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.asset_stages'


def _stage(stage_id, name, **overrides):
    attributes = {'id': stage_id, 'name': name, 'color': 'slate', 'icon': None, 'kind': 'progress',
                  'sort_order': stage_id, 'requires_reason': False, 'requires_decision': False,
                  'is_optional': False, 'description': None}
    attributes.update(overrides)
    return AssetStage(**attributes)


class _PatchedTestCase(TestCase):

    def _patch(self, target, **kwargs):
        patcher = patch(f'{_BUSINESS}.{target}', **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock


class TestAssetStagesValidate(_PatchedTestCase):

    def setUp(self):
        self.find_by_name = self._patch('asset_stages_db_find_by_name', return_value=None)

    def _error(self, body, existing=None):
        with self.assertRaises(BusinessProcessingError) as raised:
            _asset_stages_validate(body, existing=existing)
        return raised.exception.get_message()

    def test_create_applies_defaults(self):
        attributes = _asset_stages_validate({'name': '  Isolated  '})
        self.assertEqual({'name': 'Isolated', 'color': 'slate', 'kind': 'progress',
                          'requires_reason': False, 'requires_decision': False, 'is_optional': False}, attributes)

    def test_create_requires_a_name(self):
        self.assertIn('name', self._error({}))

    def test_empty_name_is_refused(self):
        self.assertIn('between 1 and 64', self._error({'name': '   '}))

    def test_name_longer_than_64_is_refused(self):
        self.assertIn('between 1 and 64', self._error({'name': 'x' * 65}))

    def test_name_of_64_characters_is_accepted(self):
        self.assertEqual('x' * 64, _asset_stages_validate({'name': 'x' * 64})['name'])

    def test_non_string_name_is_refused(self):
        self.assertIn('string', self._error({'name': 12}))

    def test_duplicate_name_is_refused(self):
        self.find_by_name.return_value = _stage(3, 'Isolated')
        self.assertIn('already exists', self._error({'name': 'isolated'}))

    def test_update_excludes_itself_from_the_uniqueness_check(self):
        _asset_stages_validate({'name': 'Isolated'}, existing=_stage(4, 'Isolated'))
        self.find_by_name.assert_called_once_with('Isolated', exclude_id=4)

    def test_unknown_color_is_refused(self):
        self.assertIn('color', self._error({'name': 'A', 'color': '#ff0000'}))

    def test_palette_color_is_accepted(self):
        self.assertEqual('emerald', _asset_stages_validate({'name': 'A', 'color': 'emerald'})['color'])

    def test_icon_must_match_the_pattern(self):
        for icon in ('Wrench', 'a b', '../x', 'x' * 65, 'wrench\n', 3):
            with self.subTest(icon=icon):
                self.assertIn('icon', self._error({'name': 'A', 'icon': icon}))

    def test_icon_can_be_cleared(self):
        self.assertIsNone(_asset_stages_validate({'name': 'A', 'icon': ''})['icon'])
        self.assertIsNone(_asset_stages_validate({'name': 'A', 'icon': None})['icon'])

    def test_unknown_kind_is_refused(self):
        self.assertIn('kind', self._error({'name': 'A', 'kind': 'finished'}))

    def test_booleans_are_strict(self):
        for value in (1, 'true', None):
            with self.subTest(value=value):
                self.assertIn('requires_reason', self._error({'name': 'A', 'requires_reason': value}))

    def test_is_optional_is_strict(self):
        self.assertIn('is_optional', self._error({'name': 'A', 'is_optional': 'yes'}))

    def test_progress_stage_can_be_optional(self):
        attributes = _asset_stages_validate({'name': 'Isolated', 'is_optional': True})
        self.assertTrue(attributes['is_optional'])

    def test_only_a_progress_stage_can_be_optional(self):
        for kind in ('done', 'exception'):
            with self.subTest(kind=kind):
                attributes = _asset_stages_validate({'name': 'A', 'kind': kind, 'is_optional': True})
                self.assertFalse(attributes['is_optional'])

    def test_leaving_progress_clears_optional(self):
        attributes = _asset_stages_validate({'kind': 'exception'}, existing=_stage(1, 'A', is_optional=True))
        self.assertEqual({'kind': 'exception', 'is_optional': False}, attributes)

    def test_optional_update_keeps_the_existing_kind_in_mind(self):
        self.assertEqual({'is_optional': True},
                         _asset_stages_validate({'is_optional': True}, existing=_stage(1, 'A')))
        self.assertEqual({'is_optional': False},
                         _asset_stages_validate({'is_optional': True}, existing=_stage(1, 'A', kind='done')))

    def test_description_too_long_is_refused(self):
        self.assertIn('description', self._error({'name': 'A', 'description': 'x' * 2001}))

    def test_partial_update_only_returns_given_fields(self):
        attributes = _asset_stages_validate({'kind': 'done'}, existing=_stage(1, 'A'))
        self.assertEqual({'kind': 'done'}, attributes)

    def test_unknown_fields_are_ignored(self):
        attributes = _asset_stages_validate({'kind': 'done', 'id': 99, 'sort_order': 7, 'in_use_count': 3},
                                            existing=_stage(1, 'A'))
        self.assertEqual({'kind': 'done'}, attributes)

    def test_non_dict_body_is_refused(self):
        self.assertEqual('Invalid request', self._error(['name']))


class TestAssetStagesCrud(_PatchedTestCase):

    def setUp(self):
        self._patch('asset_stages_db_find_by_name', return_value=None)
        self._patch('track_activity')
        self.count_assets = self._patch('asset_stages_db_count_assets', return_value=0)
        self.save = self._patch('asset_stages_db_save', return_value=True)
        self._patch('asset_stages_db_max_sort_order', return_value=6)

    def test_list_carries_in_use_counts(self):
        self._patch('asset_stages_db_list', return_value=[_stage(1, 'A'), _stage(2, 'B')])
        self._patch('asset_stages_db_in_use_counts', return_value={2: 5})
        result = asset_stages_list()
        self.assertEqual([0, 5], [entry['in_use_count'] for entry in result])
        self.assertEqual({'id', 'name', 'description', 'color', 'icon', 'kind', 'sort_order',
                          'requires_reason', 'requires_decision', 'is_optional', 'in_use_count'}, set(result[0]))

    def test_create_appends_at_the_end(self):
        result = asset_stages_create({'name': 'Wiped', 'kind': 'done'})
        self.assertEqual(7, result['sort_order'])
        self.assertEqual('done', result['kind'])
        self.assertEqual(0, result['in_use_count'])

    def test_create_reports_a_lost_unique_race(self):
        self.save.return_value = False
        with self.assertRaises(BusinessProcessingError):
            asset_stages_create({'name': 'Wiped'})

    def test_update_unknown_stage_is_not_found(self):
        self._patch('asset_stages_db_get', return_value=None)
        with self.assertRaises(ObjectNotFoundError):
            asset_stages_update(42, {'name': 'X'})

    def test_update_changes_given_fields_only(self):
        stage = _stage(1, 'A', color='red')
        self._patch('asset_stages_db_get', return_value=stage)
        asset_stages_update(1, {'requires_reason': True})
        self.assertTrue(stage.requires_reason)
        self.assertEqual('red', stage.color)
        self.assertEqual('A', stage.name)

    def test_delete_in_use_stage_is_refused(self):
        self._patch('asset_stages_db_get', return_value=_stage(1, 'A'))
        delete = self._patch('asset_stages_db_delete', return_value=True)
        self.count_assets.return_value = 3
        with self.assertRaises(BusinessProcessingError) as raised:
            asset_stages_delete(1)
        self.assertEqual('Stage is in use by 3 assets', raised.exception.get_message())
        delete.assert_not_called()

    def test_delete_unused_stage(self):
        stage = _stage(1, 'A')
        self._patch('asset_stages_db_get', return_value=stage)
        delete = self._patch('asset_stages_db_delete', return_value=True)
        asset_stages_delete(1)
        delete.assert_called_once_with(stage)


class TestAssetStagesReorder(_PatchedTestCase):

    def setUp(self):
        self._patch('track_activity')
        self._patch('asset_stages_db_commit', return_value=True)
        self._patch('asset_stages_db_in_use_counts', return_value={})
        self.stages = [_stage(1, 'A'), _stage(2, 'B'), _stage(3, 'C')]
        self._patch('asset_stages_db_list', return_value=self.stages)

    def test_permutation_sets_the_sort_order(self):
        asset_stages_reorder([3, 1, 2])
        self.assertEqual({1: 1, 2: 2, 3: 0}, {stage.id: stage.sort_order for stage in self.stages})

    def test_incomplete_or_duplicate_lists_are_refused(self):
        for ids in ([1, 2], [1, 2, 3, 4], [1, 1, 2, 3], [1, 2, 2], 'abc', None, [1, 2, True]):
            with self.subTest(ids=ids):
                with self.assertRaises(BusinessProcessingError):
                    asset_stages_reorder(ids)


class TestAssetStagesPresets(_PatchedTestCase):

    def setUp(self):
        self._patch('track_activity')
        self.counts = self._patch('asset_stages_db_in_use_counts', return_value={})
        self.replace = self._patch('asset_stages_db_replace_all', return_value=True)
        self._patch('asset_stages_db_list', return_value=[])

    def _applied(self, preset):
        asset_stages_apply_preset(preset)
        return self.replace.call_args[0][0]

    def test_incident_preset_matches_the_default_seed(self):
        stages = self._applied('incident')
        self.assertEqual(['Identified', 'Isolated', 'Patched', 'Restored', 'Unpatched', 'Blocked'],
                         [stage.name for stage in stages])
        self.assertEqual(list(range(6)), [stage.sort_order for stage in stages])
        self.assertEqual(['Unpatched', 'Blocked'], [stage.name for stage in stages if stage.requires_reason])
        self.assertEqual(['Isolated'], [stage.name for stage in stages if stage.is_optional])

    def test_compromise_simple_preset(self):
        stages = {stage.name: stage for stage in self._applied('compromise-simple')}
        self.assertEqual(['Compromised', 'Contained', 'Eradicated', 'Recovered', 'Accepted risk'], list(stages))
        self.assertTrue(stages['Contained'].is_optional)
        self.assertEqual('done', stages['Recovered'].kind)
        self.assertEqual('exception', stages['Accepted risk'].kind)
        self.assertTrue(stages['Accepted risk'].requires_reason)

    def test_presets_use_valid_attributes(self):
        for preset in ('incident', 'compromise-simple'):
            for stage in self._applied(preset):
                with self.subTest(preset=preset, stage=stage.name):
                    with patch(f'{_BUSINESS}.asset_stages_db_find_by_name', return_value=None):
                        attributes = _asset_stages_validate({'name': stage.name, 'color': stage.color,
                                                             'icon': stage.icon, 'kind': stage.kind,
                                                             'is_optional': stage.is_optional})
                        self.assertEqual(stage.is_optional, attributes['is_optional'])

    def test_unknown_preset_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            asset_stages_apply_preset('chaos')
        self.replace.assert_not_called()

    def test_vulnerability_preset_is_retired(self):
        # Vulnerabilities are tracked as findings, not as asset stages.
        with self.assertRaises(BusinessProcessingError):
            asset_stages_apply_preset('vulnerability')
        self.replace.assert_not_called()

    def test_refused_while_a_stage_is_in_use(self):
        self.counts.return_value = {4: 2}
        with self.assertRaises(BusinessProcessingError):
            asset_stages_apply_preset('incident')
        self.replace.assert_not_called()

    def test_lost_race_is_reported(self):
        self.replace.return_value = False
        with self.assertRaises(BusinessProcessingError):
            asset_stages_apply_preset('incident')


class TestAssetStagesSetForAsset(_PatchedTestCase):

    def setUp(self):
        self.stages = {
            1: _stage(1, 'Identified'),
            2: _stage(2, 'Unpatched', kind='exception', requires_reason=True),
            3: _stage(3, 'Accepted', kind='exception', requires_decision=True),
        }
        self._patch('asset_stages_db_get', side_effect=self.stages.get)
        self.decision = self._patch('decision_belongs_to_case_war_room',
                                    return_value=SimpleNamespace(decision_id=9, war_room_id=5))
        self.add_history = self._patch('asset_stages_db_add_history')
        self.commit = self._patch('asset_stages_db_commit', return_value=True)
        self._patch('asset_stages_db_rollback')
        self.obj_history = self._patch('add_obj_history_entry')
        self.track = self._patch('track_activity')
        self.hook = self._patch('call_modules_hook', side_effect=lambda _name, data, **_kwargs: data)
        self.asset = CaseAssets(asset_id=11, case_id=7, asset_name='srv-01')

    def _set(self, stage_id, reason=None, decision_id=None, war_room_id=None):
        return asset_stages_set_for_asset(self.asset, stage_id, reason, decision_id, 3, war_room_id=war_room_id)

    def _error(self, *args, **kwargs):
        with self.assertRaises(BusinessProcessingError) as raised:
            self._set(*args, **kwargs)
        return raised.exception.get_message()

    def test_sets_the_stage_and_records_everything(self):
        result = self._set(1, reason='  scanned  ')
        self.assertIs(self.asset, result)
        self.assertEqual(1, self.asset.stage_id)
        self.assertEqual('scanned', self.asset.stage_reason)
        self.assertEqual(3, self.asset.stage_updated_by_id)
        self.assertIsNotNone(self.asset.stage_updated_at)
        entry = self.add_history.call_args[0][0]
        self.assertEqual((11, 7, None, None, 1, 'Identified', 'scanned', 3),
                         (entry.asset_id, entry.case_id, entry.from_stage_id, entry.from_stage_name,
                          entry.to_stage_id, entry.to_stage_name, entry.reason, entry.changed_by_id))
        self.obj_history.assert_called_once_with(self.asset, 'stage changed to "Identified"')
        self.commit.assert_called_once()
        self.hook.assert_called_once_with('on_postload_asset_update', self.asset, caseid=7)
        self.assertEqual(7, self.track.call_args.kwargs['caseid'])

    def test_history_keeps_the_previous_stage(self):
        self.asset.stage_id = 1
        self._set(2, reason='legacy OS')
        entry = self.add_history.call_args[0][0]
        self.assertEqual((1, 'Identified', 2, 'Unpatched'),
                         (entry.from_stage_id, entry.from_stage_name, entry.to_stage_id, entry.to_stage_name))

    def test_unknown_stage_is_refused(self):
        self.assertEqual('Unknown stage', self._error(42))
        self.commit.assert_not_called()

    def test_ids_must_be_integers(self):
        for args in (('1',), (True,), (1.0,), (1, None, '9'), (1, None, False)):
            with self.subTest(args=args):
                self.assertIn('must be an integer', self._error(*args))

    def test_reason_must_be_a_bounded_string(self):
        self.assertIn('reason', self._error(1, reason=['x']))
        self.assertIn('4000', self._error(1, reason='x' * 4001))

    def test_requires_reason_is_enforced(self):
        for reason in (None, '', '   '):
            with self.subTest(reason=reason):
                self.assertIn('requires a reason', self._error(2, reason=reason))

    def test_requires_decision_is_enforced(self):
        self.assertIn('requires a war-room decision', self._error(3))

    def test_decision_must_belong_to_an_attached_war_room(self):
        self.decision.return_value = None
        self.assertIn('Decision not found', self._error(3, decision_id=9))
        self.decision.assert_called_once_with(9, 7)
        self.commit.assert_not_called()

    def test_decision_sets_the_history_war_room(self):
        self._set(3, decision_id=9)
        entry = self.add_history.call_args[0][0]
        self.assertEqual((9, 5), (entry.decision_id, entry.war_room_id))
        self.assertEqual(9, self.asset.stage_decision_id)

    def test_case_details_stay_out_of_the_war_room_feed(self):
        self._set(3, decision_id=9)
        self.assertEqual(2, self.track.call_count)
        case_call, room_call = self.track.call_args_list
        self.assertIn('srv-01', case_call.args[0])
        self.assertEqual({'caseid': 7}, case_call.kwargs)
        self.assertEqual(('changed the stage of 1 asset',), room_call.args)
        self.assertEqual({'war_room_id': 5}, room_call.kwargs)

    def test_without_decision_only_the_case_feed_is_written(self):
        self._set(1)
        self.track.assert_called_once()
        self.assertEqual({'caseid': 7}, self.track.call_args.kwargs)

    def test_explicit_war_room_wins(self):
        self._set(3, decision_id=9, war_room_id=8)
        self.assertEqual(8, self.add_history.call_args[0][0].war_room_id)
        # The scope endpoints passing war_room_id log their own aggregate
        # war-room activity: only the case feed is written here.
        self.track.assert_called_once()
        self.assertEqual({'caseid': 7}, self.track.call_args.kwargs)

    def test_same_stage_reason_and_decision_is_a_no_op(self):
        self.asset.stage_id = 2
        self.asset.stage_reason = 'legacy OS'
        self._set(2, reason='legacy OS')
        self.add_history.assert_not_called()
        self.commit.assert_not_called()
        self.hook.assert_not_called()

    def test_changing_only_the_reason_is_recorded(self):
        self.asset.stage_id = 2
        self.asset.stage_reason = 'legacy OS'
        self._set(2, reason='vendor EOL')
        self.add_history.assert_called_once()

    def test_clearing_the_stage_clears_reason_and_decision(self):
        self.asset.stage_id = 3
        self.asset.stage_reason = 'x'
        self.asset.stage_decision_id = 9
        self._set(None, reason='rolled back')
        self.assertIsNone(self.asset.stage_id)
        self.assertIsNone(self.asset.stage_reason)
        self.assertIsNone(self.asset.stage_decision_id)
        entry = self.add_history.call_args[0][0]
        self.assertEqual(('Accepted', None, 'rolled back'),
                         (entry.from_stage_name, entry.to_stage_name, entry.reason))
        self.obj_history.assert_called_once_with(self.asset, 'stage changed to none')

    def test_failed_commit_is_reported(self):
        self.commit.return_value = False
        self.assertIn('Unable', self._error(1))
        self.hook.assert_not_called()

    def test_hook_returning_nothing_keeps_the_asset(self):
        self.hook.side_effect = None
        self.hook.return_value = None
        self.assertIs(self.asset, self._set(1))


class TestAssetStagesHelpers(_PatchedTestCase):

    def test_decision_war_room_id_rejects_non_integers(self):
        lookup = self._patch('asset_stages_db_decision_war_room_id', return_value=5)
        self.assertIsNone(asset_stages_decision_war_room_id('5'))
        self.assertIsNone(asset_stages_decision_war_room_id(True))
        lookup.assert_not_called()
        self.assertEqual(5, asset_stages_decision_war_room_id(9))

    def test_history_serialisation(self):
        entry = MagicMock(id=1, asset_id=11, case_id=7, from_stage_id=None, from_stage_name=None, to_stage_id=2,
                          to_stage_name='Unpatched', reason='legacy', decision_id=9, war_room_id=5,
                          changed_by_id=3)
        entry.changed_at.isoformat.return_value = '2026-10-06T10:00:00'
        self._patch('asset_stages_db_history', return_value=[(entry, 'Alice', 'Crisis', 4)])
        result = asset_stages_history(CaseAssets(asset_id=11))
        self.assertEqual([{
            'id': 1, 'asset_id': 11, 'case_id': 7, 'from_stage_id': None, 'from_stage_name': None,
            'to_stage_id': 2, 'to_stage_name': 'Unpatched', 'reason': 'legacy', 'decision_id': 9,
            'decision_number': 4, 'war_room_id': 5, 'war_room_name': 'Crisis', 'changed_by_id': 3,
            'changed_by_name': 'Alice', 'changed_at': '2026-10-06T10:00:00',
        }], result)
