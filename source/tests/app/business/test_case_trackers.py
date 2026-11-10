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

"""Unit tests for the case trackers (no database)."""

import itertools
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import case_tracker_timelines as timelines
from app.business import case_trackers as trackers
from app.business import war_room_tracker_templates as templates
from app.models.case_trackers import CaseTracker
from app.models.case_trackers import CaseTrackerRow
from app.models.case_trackers import CaseTrackerRowEvent
from app.models.errors import BusinessProcessingError


_BIZ = 'app.business.case_trackers'
_TEMPLATES = 'app.business.war_room_tracker_templates'


def _step(step_id, name, sort_order=0):
    return SimpleNamespace(id=step_id, name=name, description=None, color='red', icon=None, sort_order=sort_order)


def _tracker(source='assets', **kwargs):
    fields = {'id': 7, 'case_id': 1, 'name': 'Hosts', 'icon': None, 'description': None, 'source': source,
              'step_ids': None, 'columns': [], 'show_asset_flags': False, 'timeline_sync': None, 'sort_order': 0,
              'created_by_id': 3, 'created_at': None, 'updated_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _asset_item(asset_id, name):
    return trackers._item('asset', asset_id, name, {'asset_id': asset_id, 'asset_name': name})


class _Patches(TestCase):

    def setUp(self):
        self.added = []
        ids = itertools.count(100)

        def _add(obj):
            if getattr(obj, 'id', None) is None:
                obj.id = next(ids)
            self.added.append(obj)
            return obj

        self.patches = {
            'case_trackers_db_add': MagicMock(side_effect=_add),
            'case_trackers_db_commit': MagicMock(return_value=True),
            'case_trackers_db_rows': MagicMock(return_value=[]),
            'case_trackers_db_row_steps': MagicMock(return_value=[]),
            'case_trackers_db_steps': MagicMock(return_value=[_step(1, 'THOR received'), _step(2, 'Waiting')]),
            'case_trackers_db_tracker_lock': MagicMock(),
            'war_room_trackers_db_user_names': MagicMock(return_value={}),
            'track_activity': MagicMock(),
        }
        self._patchers = [patch(f'{_BIZ}.{name}', mock) for name, mock in self.patches.items()]
        self._patchers.append(patch.dict(trackers._SOURCE_LISTERS, {
            'assets': lambda _case_id: ([_asset_item(10, 'dc01'), _asset_item(11, 'ws02')], False),
            'tasks': lambda _case_id: ([], False),
        }))
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()


class TestValidation(TestCase):

    def test_source_should_be_a_case_source(self):
        for source in ('manual', 'assets', 'iocs', 'tasks', 'evidences'):
            self.assertEqual(source, trackers.case_trackers_validate_source(source))
        for source in ('cases', 'customers', None):
            with self.assertRaises(BusinessProcessingError):
                trackers.case_trackers_validate_source(source)

    def test_build_tracker_should_reject_an_unknown_step(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.case_trackers_build_tracker(1, {'name': 'T', 'source': 'manual', 'step_ids': [9]}, {1}, 0, 3)

    def test_build_tracker_should_validate_the_columns(self):
        tracker = trackers.case_trackers_build_tracker(
            1, {'name': ' T ', 'source': 'tasks', 'columns': [{'label': 'Ticket', 'type': 'url'}]}, set(), 0, 3)
        self.assertIsInstance(tracker, CaseTracker)
        self.assertEqual(('T', 'tasks', 'c_ticket'), (tracker.name, tracker.source, tracker.columns[0]['key']))
        self.assertIsNone(tracker.timeline_sync)

    def test_timeline_sync_should_ignore_the_war_room_target(self):
        sync = timelines.case_tracker_timelines_validate({'mode': 'changes', 'target': 'war_room'}, [])
        self.assertEqual({'mode': 'changes', 'date': 'changed_at', 'case_timeline_id': None}, sync)
        self.assertIsNone(timelines.case_tracker_timelines_validate(None, []))
        with self.assertRaises(BusinessProcessingError):
            timelines.case_tracker_timelines_validate({'mode': 'nope'}, [])

    def test_timeline_sync_should_need_a_timeline_of_the_case(self):
        with patch('app.business.case_tracker_timelines.case_trackers_db_case_timeline', return_value=None):
            with self.assertRaises(BusinessProcessingError):
                timelines.case_tracker_timelines_validate({'mode': 'state', 'case_timeline_id': 4}, [], 1)
        with patch('app.business.case_tracker_timelines.case_trackers_db_case_timeline', return_value=object()):
            sync = timelines.case_tracker_timelines_validate({'mode': 'state', 'case_timeline_id': 4}, [], 1)
        self.assertEqual(4, sync['case_timeline_id'])
        self.assertIsNone(timelines.case_tracker_timelines_validate(
            {'mode': 'state', 'case_timeline_id': 4}, [])['case_timeline_id'])


class TestRows(_Patches):

    def test_a_mirror_should_list_the_items_merged_with_their_state(self):
        stored = SimpleNamespace(id=50, item_type='asset', item_id=11, label=None, note='Waiting for THOR',
                                 values={}, updated_at=None, updated_by_id=None)
        self.patches['case_trackers_db_rows'].return_value = [stored]
        rows = trackers.case_trackers_rows_list(1, _tracker())['data']
        self.assertEqual([('asset:10', None, None), ('asset:11', 50, 'Waiting for THOR')],
                         [(row['key'], row['row_id'], row['note']) for row in rows])

    def test_state_should_create_the_row_of_a_mirrored_item_on_its_first_change(self):
        result = trackers.case_trackers_rows_update_state(
            1, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 10}], 'add_step_ids': [1],
                            'note': 'Received'}, 3)
        rows = [obj for obj in self.added if isinstance(obj, CaseTrackerRow)]
        self.assertEqual([('asset', 10)], [(row.item_type, row.item_id) for row in rows])
        events = {obj.action for obj in self.added if isinstance(obj, CaseTrackerRowEvent)}
        self.assertEqual({'step_added', 'note_changed'}, events)
        self.assertEqual(rows[0].id, result['data'][0]['row_id'])
        self.assertNotIn('timeline', result)
        self.patches['track_activity'].assert_called_once()

    def test_state_should_reject_an_item_out_of_the_case(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.case_trackers_rows_update_state(
                1, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 99}], 'note': 'x'}, 3)

    def test_state_should_reject_an_item_of_another_type(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.case_trackers_rows_update_state(
                1, _tracker(), {'rows': [{'item_type': 'task', 'item_id': 10}], 'note': 'x'}, 3)

    def test_state_without_change_should_store_nothing(self):
        trackers.case_trackers_rows_update_state(
            1, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 10}], 'note': None}, 3)
        self.assertEqual([], self.added)
        self.patches['track_activity'].assert_not_called()

    def test_rows_should_only_be_added_to_a_manual_tracker(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.case_trackers_rows_add(1, _tracker('tasks'), {'rows': [{'label': 'x'}]}, 3)

    def test_manual_rows_should_skip_the_items_already_present(self):
        self.patches['case_trackers_db_rows'].return_value = [
            SimpleNamespace(id=50, item_type='asset', item_id=10, label=None)]
        with patch(f'{_BIZ}.case_trackers_db_rows_max_sort', return_value=0):
            result = trackers.case_trackers_rows_add(1, _tracker('manual'), {'rows': [
                {'item_type': 'asset', 'item_id': 10}, {'item_type': 'asset', 'item_id': 11},
                {'label': 'Call the CERT'}]}, 3)
        self.assertEqual([f'row:{row["row_id"]}' for row in result['data']], [row['key'] for row in result['data']])
        self.assertEqual([('asset', 11), (None, None)], [(row['item_type'], row['item_id']) for row in result['data']])

    def test_manual_rows_should_repeat_an_item_when_duplicates_are_allowed(self):
        self.patches['case_trackers_db_rows'].return_value = [
            SimpleNamespace(id=50, item_type='asset', item_id=10, label=None)]
        with patch(f'{_BIZ}.case_trackers_db_rows_max_sort', return_value=0):
            result = trackers.case_trackers_rows_add(1, _tracker('manual'), {'rows': [
                {'item_type': 'asset', 'item_id': 10, 'label': 'After reimage'}], 'allow_duplicates': True}, 3)
        self.assertEqual([('asset', 10, 'dc01', 'After reimage')],
                         [(row['item_type'], row['item_id'], row['label'], row['custom_label'])
                          for row in result['data']])

    def test_state_should_give_a_custom_label_to_every_row_of_an_item(self):
        rows = [SimpleNamespace(id=50, item_type='asset', item_id=10, label=None, note=None, values={},
                                updated_at=None, updated_by_id=None) for _ in range(2)]
        rows[1].id = 51
        self.patches['case_trackers_db_rows'].return_value = rows
        result = trackers.case_trackers_rows_update_state(
            1, _tracker('manual'), {'rows': [{'item_type': 'asset', 'item_id': 10}], 'label': 'Patient zero'}, 3)
        self.assertEqual(['Patient zero', 'Patient zero'], [row.label for row in rows])
        self.assertEqual([('row:50', 'dc01', 'Patient zero'), ('row:51', 'dc01', 'Patient zero')],
                         [(row['key'], row['label'], row['custom_label']) for row in result['data']])
        self.patches['case_trackers_db_tracker_lock'].assert_not_called()

    def test_state_should_lock_a_mirror_before_creating_a_row(self):
        trackers.case_trackers_rows_update_state(
            1, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 10}], 'label': 'DC'}, 3)
        self.patches['case_trackers_db_tracker_lock'].assert_called_once_with(7)
        rows = [obj for obj in self.added if isinstance(obj, CaseTrackerRow)]
        self.assertEqual([('asset', 10, 'DC')], [(row.item_type, row.item_id, row.label) for row in rows])

    def test_mirrored_rows_cannot_be_reordered(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.case_trackers_rows_reorder(_tracker(), [1])


class TestTemplates(TestCase):

    def test_apply_to_case_should_skip_war_room_sources_and_merge_steps(self):
        template = SimpleNamespace(
            id=1, name='Collection',
            steps=[{'name': 'THOR received', 'color': 'green', 'icon': None, 'description': None},
                   {'name': 'Waiting', 'color': 'slate', 'icon': None, 'description': None}],
            trackers=[{'name': 'Hosts', 'icon': None, 'description': None, 'source': 'assets',
                       'step_names': ['Waiting'], 'columns': [], 'show_asset_flags': True,
                       'timeline_sync': {'mode': 'changes', 'date': 'changed_at', 'target': 'cases'},
                       'board_widgets': [{'type': 'tracker_count', 'config': {}}]},
                      {'name': 'Customers', 'icon': None, 'description': None, 'source': 'customers',
                       'step_names': None, 'columns': [], 'show_asset_flags': False}])
        existing = [_step(5, 'waiting')]
        ids = itertools.count(100)
        added = []

        def _add(obj):
            if getattr(obj, 'id', None) is None:
                obj.id = next(ids)
            added.append(obj)
            return obj

        with patch(f'{_TEMPLATES}.war_room_trackers_db_template_get', return_value=template), \
                patch(f'{_TEMPLATES}.war_room_trackers_db_add', side_effect=_add), \
                patch(f'{_TEMPLATES}.war_room_trackers_db_commit', return_value=True), \
                patch(f'{_TEMPLATES}.case_trackers_db_steps', side_effect=[existing, existing]), \
                patch(f'{_TEMPLATES}.case_trackers_db_trackers', return_value=[]), \
                patch(f'{_TEMPLATES}.track_activity'):
            _, created, skipped = templates.tracker_templates_apply_to_case(1, 1, 3)
        self.assertEqual(['Customers'], skipped)
        self.assertEqual(['Hosts'], [tracker.name for tracker in created])
        self.assertEqual([5], created[0].step_ids)
        self.assertEqual({'mode': 'changes', 'date': 'changed_at', 'case_timeline_id': None},
                         created[0].timeline_sync)
        self.assertEqual(['THOR received'], [obj.name for obj in added if not isinstance(obj, CaseTracker)])
