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

"""Asset flag taxonomy validation, presets, deletion and the flags of a
case asset. Every datamgmt helper is patched; models are transient."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business.asset_flags import ASSET_FLAGS_TIMELINE_NAME
from app.business.asset_flags import _asset_flags_validate
from app.business.asset_flags import asset_flags_apply_preset
from app.business.asset_flags import asset_flags_clear_for_asset
from app.business.asset_flags import asset_flags_create
from app.business.asset_flags import asset_flags_decision_war_room_id
from app.business.asset_flags import asset_flags_delete
from app.business.asset_flags import asset_flags_history
from app.business.asset_flags import asset_flags_is_unchanged
from app.business.asset_flags import asset_flags_list
from app.business.asset_flags import asset_flags_reorder
from app.business.asset_flags import asset_flags_set_for_asset
from app.business.asset_flags import asset_flags_update
from app.models.assets import AssetFlag
from app.models.assets import CaseAssetFlag
from app.models.assets import CaseAssetFlagHistory
from app.models.assets import CaseAssets
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_BUSINESS = 'app.business.asset_flags'


def _flag(flag_id, name, **overrides):
    attributes = {'id': flag_id, 'name': name, 'color': 'slate', 'icon': None, 'kind': 'status',
                  'sort_order': flag_id, 'requires_reason': False, 'requires_decision': False,
                  'description': None}
    attributes.update(overrides)
    return AssetFlag(**attributes)


class _PatchedTestCase(TestCase):

    def _patch(self, target, **kwargs):
        patcher = patch(f'{_BUSINESS}.{target}', **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock


class TestAssetFlagsValidate(_PatchedTestCase):

    def setUp(self):
        self.find_by_name = self._patch('asset_flags_db_find_by_name', return_value=None)

    def _error(self, body, existing=None):
        with self.assertRaises(BusinessProcessingError) as raised:
            _asset_flags_validate(body, existing=existing)
        return raised.exception.get_message()

    def test_create_applies_defaults(self):
        attributes = _asset_flags_validate({'name': '  Isolated  '})
        self.assertEqual({'name': 'Isolated', 'color': 'slate', 'kind': 'status',
                          'requires_reason': False, 'requires_decision': False}, attributes)

    def test_create_requires_a_name(self):
        self.assertIn('name', self._error({}))

    def test_empty_name_is_refused(self):
        self.assertIn('between 1 and 64', self._error({'name': '   '}))

    def test_name_longer_than_64_is_refused(self):
        self.assertIn('between 1 and 64', self._error({'name': 'x' * 65}))

    def test_name_of_64_characters_is_accepted(self):
        self.assertEqual('x' * 64, _asset_flags_validate({'name': 'x' * 64})['name'])

    def test_non_string_name_is_refused(self):
        self.assertIn('string', self._error({'name': 12}))

    def test_duplicate_name_is_refused(self):
        self.find_by_name.return_value = _flag(3, 'Isolated')
        self.assertIn('already exists', self._error({'name': 'isolated'}))

    def test_update_excludes_itself_from_the_uniqueness_check(self):
        _asset_flags_validate({'name': 'Isolated'}, existing=_flag(4, 'Isolated'))
        self.find_by_name.assert_called_once_with('Isolated', exclude_id=4)

    def test_unknown_color_is_refused(self):
        self.assertIn('color', self._error({'name': 'A', 'color': '#ff0000'}))

    def test_palette_color_is_accepted(self):
        self.assertEqual('teal', _asset_flags_validate({'name': 'A', 'color': 'teal'})['color'])

    def test_icon_must_match_the_pattern(self):
        for icon in ('Wrench', 'a b', '../x', 'x' * 65, 'wrench\n', 3):
            with self.subTest(icon=icon):
                self.assertIn('icon', self._error({'name': 'A', 'icon': icon}))

    def test_icon_can_be_cleared(self):
        self.assertIsNone(_asset_flags_validate({'name': 'A', 'icon': ''})['icon'])
        self.assertIsNone(_asset_flags_validate({'name': 'A', 'icon': None})['icon'])

    def test_unknown_kind_is_refused(self):
        for kind in ('progress', 'finished'):
            with self.subTest(kind=kind):
                self.assertIn('kind', self._error({'name': 'A', 'kind': kind}))

    def test_booleans_are_strict(self):
        for value in (1, 'true', None):
            with self.subTest(value=value):
                self.assertIn('requires_reason', self._error({'name': 'A', 'requires_reason': value}))

    def test_description_too_long_is_refused(self):
        self.assertIn('description', self._error({'name': 'A', 'description': 'x' * 2001}))

    def test_partial_update_only_returns_given_fields(self):
        attributes = _asset_flags_validate({'kind': 'done'}, existing=_flag(1, 'A'))
        self.assertEqual({'kind': 'done'}, attributes)

    def test_unknown_fields_are_ignored(self):
        attributes = _asset_flags_validate({'kind': 'done', 'id': 99, 'sort_order': 7, 'in_use_count': 3},
                                           existing=_flag(1, 'A'))
        self.assertEqual({'kind': 'done'}, attributes)

    def test_non_dict_body_is_refused(self):
        self.assertEqual('Invalid request', self._error(['name']))


class TestAssetFlagsCrud(_PatchedTestCase):

    def setUp(self):
        self._patch('asset_flags_db_find_by_name', return_value=None)
        self._patch('track_activity')
        self.count_assets = self._patch('asset_flags_db_count_assets', return_value=0)
        self.save = self._patch('asset_flags_db_save', return_value=True)
        self._patch('asset_flags_db_max_sort_order', return_value=6)

    def test_list_carries_in_use_counts(self):
        self._patch('asset_flags_db_list', return_value=[_flag(1, 'A'), _flag(2, 'B')])
        self._patch('asset_flags_db_in_use_counts', return_value={2: 5})
        result = asset_flags_list()
        self.assertEqual([0, 5], [entry['in_use_count'] for entry in result])
        self.assertEqual({'id', 'name', 'description', 'color', 'icon', 'kind', 'sort_order',
                          'requires_reason', 'requires_decision', 'in_use_count'}, set(result[0]))

    def test_create_appends_at_the_end(self):
        result = asset_flags_create({'name': 'Wiped', 'kind': 'done'})
        self.assertEqual(7, result['sort_order'])
        self.assertEqual('done', result['kind'])
        self.assertEqual(0, result['in_use_count'])

    def test_create_reports_a_lost_unique_race(self):
        self.save.return_value = False
        with self.assertRaises(BusinessProcessingError):
            asset_flags_create({'name': 'Wiped'})

    def test_update_unknown_flag_is_not_found(self):
        self._patch('asset_flags_db_get', return_value=None)
        with self.assertRaises(ObjectNotFoundError):
            asset_flags_update(42, {'name': 'X'})

    def test_update_changes_given_fields_only(self):
        flag = _flag(1, 'A', color='red')
        self._patch('asset_flags_db_get', return_value=flag)
        asset_flags_update(1, {'requires_reason': True})
        self.assertTrue(flag.requires_reason)
        self.assertEqual('red', flag.color)
        self.assertEqual('A', flag.name)

    def test_delete_flag_in_use_is_refused(self):
        self._patch('asset_flags_db_get', return_value=_flag(1, 'A'))
        delete = self._patch('asset_flags_db_delete', return_value=True)
        self.count_assets.return_value = 3
        with self.assertRaises(BusinessProcessingError) as raised:
            asset_flags_delete(1)
        self.assertEqual('Flag is set on 3 assets', raised.exception.get_message())
        delete.assert_not_called()

    def test_delete_unused_flag(self):
        flag = _flag(1, 'A')
        self._patch('asset_flags_db_get', return_value=flag)
        delete = self._patch('asset_flags_db_delete', return_value=True)
        asset_flags_delete(1)
        delete.assert_called_once_with(flag)


class TestAssetFlagsReorder(_PatchedTestCase):

    def setUp(self):
        self._patch('track_activity')
        self._patch('asset_flags_db_commit', return_value=True)
        self._patch('asset_flags_db_in_use_counts', return_value={})
        self.flags = [_flag(1, 'A'), _flag(2, 'B'), _flag(3, 'C')]
        self._patch('asset_flags_db_list', return_value=self.flags)

    def test_permutation_sets_the_sort_order(self):
        asset_flags_reorder([3, 1, 2])
        self.assertEqual({1: 1, 2: 2, 3: 0}, {flag.id: flag.sort_order for flag in self.flags})

    def test_incomplete_or_duplicate_lists_are_refused(self):
        for ids in ([1, 2], [1, 2, 3, 4], [1, 1, 2, 3], [1, 2, 2], 'abc', None, [1, 2, True]):
            with self.subTest(ids=ids):
                with self.assertRaises(BusinessProcessingError):
                    asset_flags_reorder(ids)


class TestAssetFlagsPresets(_PatchedTestCase):

    def setUp(self):
        self._patch('track_activity')
        self.counts = self._patch('asset_flags_db_in_use_counts', return_value={})
        self.replace = self._patch('asset_flags_db_replace_all', return_value=True)
        self._patch('asset_flags_db_list', return_value=[])

    def _applied(self, preset):
        asset_flags_apply_preset(preset)
        return self.replace.call_args[0][0]

    def test_incident_preset_matches_the_default_seed(self):
        flags = self._applied('incident')
        self.assertEqual(['Isolated', 'Credentials reset', 'Patched', 'Reimaged', 'Monitored', 'Restored',
                          "Can't be patched", 'Blocked'], [flag.name for flag in flags])
        self.assertEqual(list(range(8)), [flag.sort_order for flag in flags])
        self.assertEqual(["Can't be patched", 'Blocked'], [flag.name for flag in flags if flag.requires_reason])
        self.assertEqual(['Restored'], [flag.name for flag in flags if flag.kind == 'done'])

    def test_vulnerability_preset(self):
        flags = {flag.name: flag for flag in self._applied('vulnerability')}
        self.assertEqual(['Mitigated', 'Patched', 'Not affected', "Can't be patched", 'Accepted risk'], list(flags))
        self.assertEqual('done', flags['Not affected'].kind)
        self.assertEqual('exception', flags['Accepted risk'].kind)
        self.assertTrue(flags['Accepted risk'].requires_reason)

    def test_presets_use_valid_attributes(self):
        for preset in ('incident', 'vulnerability'):
            for flag in self._applied(preset):
                with self.subTest(preset=preset, flag=flag.name):
                    with patch(f'{_BUSINESS}.asset_flags_db_find_by_name', return_value=None):
                        _asset_flags_validate({'name': flag.name, 'color': flag.color, 'icon': flag.icon,
                                               'kind': flag.kind})

    def test_unknown_preset_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            asset_flags_apply_preset('chaos')
        self.replace.assert_not_called()

    def test_refused_while_a_flag_is_in_use(self):
        self.counts.return_value = {4: 2}
        with self.assertRaises(BusinessProcessingError):
            asset_flags_apply_preset('incident')
        self.replace.assert_not_called()

    def test_lost_race_is_reported(self):
        self.replace.return_value = False
        with self.assertRaises(BusinessProcessingError):
            asset_flags_apply_preset('incident')


class _AssetFlagChangeTestCase(_PatchedTestCase):

    def setUp(self):
        self.flags = {
            1: _flag(1, 'Isolated', color='blue'),
            2: _flag(2, "Can't be patched", color='amber', kind='exception', requires_reason=True),
            3: _flag(3, 'Accepted', kind='exception', requires_decision=True),
        }
        self._patch('asset_flags_db_get', side_effect=self.flags.get)
        self.decision = self._patch('decision_belongs_to_case_war_room',
                                    return_value=SimpleNamespace(decision_id=9, war_room_id=5))
        self.current = None
        self._patch('asset_flags_db_get_asset_flag', side_effect=lambda _asset_id, _flag_id: self.current)
        self.timeline = CaseTimeline(timeline_id=40, case_id=7, name=ASSET_FLAGS_TIMELINE_NAME)
        self.get_timeline = self._patch('asset_flags_db_get_timeline', return_value=self.timeline)
        self.existing_event = None
        self._patch('asset_flags_db_get_case_event', side_effect=lambda _event_id, _case_id: self.existing_event)
        self.added = []
        self._patch('asset_flags_db_add', side_effect=self._add)
        self.link = self._patch('asset_flags_db_link_event')
        self.remove = self._patch('asset_flags_db_remove')
        self.commit = self._patch('asset_flags_db_commit', return_value=True)
        self._patch('asset_flags_db_rollback')
        self.timeline_state = self._patch('update_timeline_state')
        self.obj_history = self._patch('add_obj_history_entry')
        self.track = self._patch('track_activity')
        self.hook = self._patch('call_modules_hook', side_effect=lambda _name, data, **_kwargs: data)
        self.asset = CaseAssets(asset_id=11, case_id=7, asset_name='srv-01')

    def _add(self, obj):
        if isinstance(obj, CasesEvent):
            obj.event_id = 100 + len(self._of(CasesEvent))
        if isinstance(obj, CaseTimeline):
            obj.timeline_id = 41
        self.added.append(obj)

    def _of(self, model):
        return [obj for obj in self.added if isinstance(obj, model)]

    def _history(self):
        entries = self._of(CaseAssetFlagHistory)
        self.assertEqual(1, len(entries))
        return entries[0]

    def _set_current(self, flag_id, reason=None, decision_id=None, event_id=None):
        self.current = CaseAssetFlag(asset_id=11, flag_id=flag_id, case_id=7, reason=reason,
                                     decision_id=decision_id, event_id=event_id)
        self.asset.flags.append(self.current)


class TestAssetFlagsSetForAsset(_AssetFlagChangeTestCase):

    def _set(self, flag_id, reason=None, decision_id=None, war_room_id=None, event_date=None):
        return asset_flags_set_for_asset(self.asset, flag_id, reason, decision_id, 3, war_room_id=war_room_id,
                                         event_date=event_date)

    def _error(self, *args, **kwargs):
        with self.assertRaises(BusinessProcessingError) as raised:
            self._set(*args, **kwargs)
        return raised.exception.get_message()

    def test_sets_the_flag_and_records_everything(self):
        result = self._set(1, reason='  EDR containment  ')
        self.assertIs(self.asset, result)
        self.assertEqual([1], [entry.flag_id for entry in self.asset.flags])
        flag = self.asset.flags[0]
        self.assertEqual(('EDR containment', 3, 100), (flag.reason, flag.set_by_id, flag.event_id))
        entry = self._history()
        self.assertEqual((11, 7, 1, 'Isolated', 'set', 'EDR containment', 3, 100),
                         (entry.asset_id, entry.case_id, entry.flag_id, entry.flag_name, entry.action,
                          entry.reason, entry.changed_by_id, entry.event_id))
        self.obj_history.assert_any_call(self.asset, 'flag "Isolated" set')
        self.timeline_state.assert_called_once_with(7, 3)
        self.commit.assert_called_once()
        self.hook.assert_called_once_with('on_postload_asset_update', self.asset, caseid=7)
        self.assertEqual(7, self.track.call_args.kwargs['caseid'])

    def test_writes_an_event_on_the_asset_status_timeline(self):
        self._set(2, reason='vendor EOL')
        [event] = self._of(CasesEvent)
        self.assertEqual("srv-01: Can't be patched", event.event_title)
        self.assertEqual('vendor EOL', event.event_content)
        self.assertEqual('#FFAD4699', event.event_color)
        self.assertEqual(('+00:00', 3, 7), (event.event_tz, event.user_id, event.case_id))
        self.link.assert_called_once_with(100, 7, 11, 40)

    def test_creates_the_timeline_when_missing(self):
        self.get_timeline.return_value = None
        self._set(1)
        [timeline] = self._of(CaseTimeline)
        self.assertEqual((7, ASSET_FLAGS_TIMELINE_NAME, False), (timeline.case_id, timeline.name, timeline.is_default))
        self.link.assert_called_once_with(100, 7, 11, 41)
        self.assertIn(f'created timeline "{ASSET_FLAGS_TIMELINE_NAME}"',
                      [call.args[0] for call in self.track.call_args_list])

    def test_event_date_is_converted_to_naive_utc(self):
        self._set(1, event_date='2024-06-02T11:15:00+02:00')
        [event] = self._of(CasesEvent)
        self.assertEqual(datetime.datetime(2024, 6, 2, 9, 15), event.event_date)

    def test_invalid_event_date_is_refused(self):
        self.assertIn('ISO 8601', self._error(1, event_date='yesterday'))
        self.assertIn('ISO 8601', self._error(1, event_date=12))

    def test_unknown_flag_is_refused(self):
        self.assertEqual('Unknown flag', self._error(42))
        self.commit.assert_not_called()

    def test_ids_must_be_integers(self):
        for args in (('1',), (True,), (1.0,), (None,), (1, None, '9'), (1, None, False)):
            with self.subTest(args=args):
                message = self._error(*args)
                self.assertTrue('must be an integer' in message or 'is required' in message, message)

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
        entry = self._history()
        self.assertEqual((9, 5), (entry.decision_id, entry.war_room_id))
        self.assertEqual(9, self.asset.flags[0].decision_id)

    def test_case_details_stay_out_of_the_war_room_feed(self):
        self._set(3, decision_id=9)
        case_call, room_call = self.track.call_args_list
        self.assertIn('srv-01', case_call.args[0])
        self.assertEqual({'caseid': 7}, case_call.kwargs)
        self.assertEqual(('changed the flags of 1 asset',), room_call.args)
        self.assertEqual({'war_room_id': 5}, room_call.kwargs)

    def test_explicit_war_room_wins(self):
        self._set(3, decision_id=9, war_room_id=8)
        self.assertEqual(8, self._history().war_room_id)
        # The scope endpoints log their own aggregate war-room activity.
        self.track.assert_called_once()
        self.assertEqual({'caseid': 7}, self.track.call_args.kwargs)

    def test_same_reason_and_decision_is_a_no_op(self):
        self._set_current(2, reason='legacy OS', event_id=100)
        self.assertIs(self.asset, self._set(2, reason='legacy OS'))
        self.assertEqual([], self.added)
        self.commit.assert_not_called()
        self.hook.assert_not_called()

    def test_changing_the_reason_updates_flag_and_event(self):
        self.existing_event = CasesEvent(event_id=100, case_id=7, event_title='old', event_content='legacy OS')
        self._set_current(2, reason='legacy OS', event_id=100)
        self._set(2, reason='vendor EOL')
        self.assertEqual('vendor EOL', self.current.reason)
        self.assertEqual('vendor EOL', self.existing_event.event_content)
        self.assertEqual([], self._of(CasesEvent))
        self.assertEqual(('updated', 100), (self._history().action, self._history().event_id))
        self.assertEqual(1, len(self.asset.flags))

    def test_deleted_event_is_written_again(self):
        self._set_current(2, reason='legacy OS', event_id=55)
        self._set(2, reason='vendor EOL')
        self.assertEqual(1, len(self._of(CasesEvent)))
        self.assertEqual(100, self.current.event_id)

    def test_failed_commit_is_reported(self):
        self.commit.return_value = False
        self.assertIn('Unable', self._error(1))
        self.hook.assert_not_called()

    def test_hook_returning_nothing_keeps_the_asset(self):
        self.hook.side_effect = None
        self.hook.return_value = None
        self.assertIs(self.asset, self._set(1))


class TestAssetFlagsClearForAsset(_AssetFlagChangeTestCase):

    def _clear(self, flag_id, reason=None, war_room_id=None):
        return asset_flags_clear_for_asset(self.asset, flag_id, reason, 3, war_room_id=war_room_id)

    def test_clearing_an_unset_flag_is_a_no_op(self):
        self.assertIs(self.asset, self._clear(1))
        self.assertEqual([], self.added)
        self.commit.assert_not_called()

    def test_clearing_removes_the_flag_and_writes_a_removed_event(self):
        self._set_current(1, reason='EDR containment', event_id=100)
        self._clear(1, reason='back online', war_room_id=8)
        self.assertEqual([], self.asset.flags)
        [event] = self._of(CasesEvent)
        self.assertEqual(('srv-01: Isolated removed', 'back online'), (event.event_title, event.event_content))
        entry = self._history()
        self.assertEqual(('cleared', 1, 'Isolated', 'back online', 8, 100),
                         (entry.action, entry.flag_id, entry.flag_name, entry.reason, entry.war_room_id,
                          entry.event_id))
        self.obj_history.assert_any_call(self.asset, 'flag "Isolated" removed')
        self.commit.assert_called_once()

    def test_unknown_flag_is_refused(self):
        with self.assertRaises(BusinessProcessingError):
            self._clear(42)


class TestAssetFlagsHelpers(_PatchedTestCase):

    def test_decision_war_room_id_rejects_non_integers(self):
        lookup = self._patch('asset_flags_db_decision_war_room_id', return_value=5)
        self.assertIsNone(asset_flags_decision_war_room_id('5'))
        self.assertIsNone(asset_flags_decision_war_room_id(True))
        lookup.assert_not_called()
        self.assertEqual(5, asset_flags_decision_war_room_id(9))

    def test_is_unchanged(self):
        asset = CaseAssets(asset_id=11, case_id=7)
        asset.flags.append(CaseAssetFlag(asset_id=11, flag_id=2, case_id=7, reason='legacy', decision_id=None))
        self.assertTrue(asset_flags_is_unchanged(asset, 2, 'set', ' legacy '))
        self.assertFalse(asset_flags_is_unchanged(asset, 2, 'set', 'other'))
        self.assertFalse(asset_flags_is_unchanged(asset, 2, 'set', 'legacy', 9))
        self.assertFalse(asset_flags_is_unchanged(asset, 1, 'set'))
        self.assertTrue(asset_flags_is_unchanged(asset, 1, 'clear'))
        self.assertFalse(asset_flags_is_unchanged(asset, 2, 'clear'))

    def test_history_serialisation(self):
        entry = MagicMock(id=1, asset_id=11, case_id=7, flag_id=2, flag_name="Can't be patched", action='set',
                          reason='legacy', decision_id=9, war_room_id=5, event_id=100, changed_by_id=3)
        entry.changed_at.isoformat.return_value = '2026-10-06T10:00:00'
        self._patch('asset_flags_db_history', return_value=[(entry, 'Alice', 'Crisis', 4)])
        result = asset_flags_history(CaseAssets(asset_id=11))
        self.assertEqual([{
            'id': 1, 'asset_id': 11, 'case_id': 7, 'flag_id': 2, 'flag_name': "Can't be patched", 'action': 'set',
            'reason': 'legacy', 'decision_id': 9, 'decision_number': 4, 'war_room_id': 5, 'war_room_name': 'Crisis',
            'event_id': 100, 'changed_by_id': 3, 'changed_by_name': 'Alice', 'changed_at': '2026-10-06T10:00:00',
        }], result)
