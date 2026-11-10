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

"""Unit tests for the war-room trackers (no database)."""

import itertools
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_trackers as trackers
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WarRoomTrackerRow
from app.models.war_rooms import WarRoomTrackerRowEvent
from app.models.war_rooms import WarRoomTrackerRowStep


_BIZ = 'app.business.war_room_trackers'


def _tracker(**kwargs):
    fields = {'id': 4, 'war_room_id': 7, 'name': 'Remediation', 'icon': None, 'description': None,
              'source': 'manual', 'step_ids': None, 'columns': [], 'show_asset_flags': False, 'sort_order': 0,
              'timeline_sync': None, 'created_by_id': 3, 'created_at': None, 'updated_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _step(step_id, name, sort_order=0):
    return SimpleNamespace(id=step_id, name=name, description=None, color='slate', icon=None,
                           sort_order=sort_order)


def _row(row_id, item_type=None, item_id=None, label=None, **kwargs):
    fields = {'id': row_id, 'tracker_id': 4, 'item_type': item_type, 'item_id': item_id, 'label': label,
              'note': None, 'values': {}, 'sort_order': 0, 'created_by_id': 3, 'created_at': None,
              'updated_by_id': None, 'updated_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _asset(asset_id, name, case_id=1):
    return {'asset_id': asset_id, 'asset_name': name, 'asset_type_name': 'Windows', 'case_id': case_id,
            'case_name': f'Case {case_id}', 'customer_id': 2, 'customer_name': 'ACME',
            'asset_compromise_status_id': 1, 'analysis_status_id': 2,
            'flags': [{'flag_id': 9, 'reason': 'r', 'decision_id': None, 'set_at': None}]}


class _Patches(TestCase):
    """Patches the persistence of the business module; `self.added`
    collects what was added, with fresh ids."""

    def setUp(self):
        self.added = []
        ids = itertools.count(100)

        def _add(obj):
            if getattr(obj, 'id', None) is None and not isinstance(obj, WarRoomTrackerRowStep):
                obj.id = next(ids)
            self.added.append(obj)
            return obj

        self.patches = {
            'war_room_trackers_db_add': MagicMock(side_effect=_add),
            'war_room_trackers_db_commit': MagicMock(return_value=True),
            'war_room_trackers_db_delete': MagicMock(),
            'war_room_trackers_db_steps': MagicMock(return_value=[]),
            'war_room_trackers_db_trackers': MagicMock(return_value=[]),
            'war_room_trackers_db_rows': MagicMock(return_value=[]),
            'war_room_trackers_db_row_steps': MagicMock(return_value=[]),
            'war_room_trackers_db_row_step_remove': MagicMock(),
            'war_room_trackers_db_user_names': MagicMock(return_value={3: 'Analyst'}),
            'war_room_trackers_db_existing_user_ids': MagicMock(side_effect=lambda ids: set(ids)),
            'war_room_trackers_db_asset_flags': MagicMock(return_value={9: ('Isolated', 'red', 'shield')}),
            'war_room_trackers_db_row_counts': MagicMock(return_value={}),
            'war_room_trackers_db_rows_max_sort': MagicMock(return_value=-1),
            'war_room_trackers_db_events': MagicMock(return_value=[]),
            'war_room_trackers_db_rows_keys': MagicMock(return_value={}),
            'war_room_trackers_db_tracker_lock': MagicMock(),
            'track_activity': MagicMock(),
            'war_room_board_widgets_prune': MagicMock(),
            'war_room_scope_list_assets': MagicMock(return_value={'data': [], 'truncated': False}),
            'war_room_scope_list_iocs': MagicMock(return_value={'data': [], 'truncated': False}),
            'war_room_scope_list_customers': MagicMock(return_value={'data': []}),
            'war_room_cases_list': MagicMock(return_value=[]),
            'war_room_managed_assets_list': MagicMock(return_value={'data': [], 'truncated': False}),
        }
        self._patchers = [patch(f'{_BIZ}.{name}', mock) for name, mock in self.patches.items()]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()

    def added_of(self, cls):
        return [obj for obj in self.added if isinstance(obj, cls)]


class TestColumns(TestCase):

    def test_keys_are_derived_from_labels_and_kept_when_given(self):
        columns = trackers.war_room_trackers_validate_columns([
            {'label': 'Ticket URL', 'type': 'url'},
            {'label': 'Ticket URL', 'type': 'text'},
            {'key': 'c_owner', 'label': 'Owner', 'type': 'user'},
        ])
        self.assertEqual([c['key'] for c in columns], ['c_ticket_url', 'c_ticket_url_2', 'c_owner'])

    def test_select_needs_options(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_validate_columns([{'label': 'State', 'type': 'select'}])
        columns = trackers.war_room_trackers_validate_columns(
            [{'label': 'State', 'type': 'select', 'options': ['a', 'b', 'a']}])
        self.assertEqual(columns[0]['options'], ['a', 'b'])

    def test_bad_columns_are_rejected(self):
        for columns in ([{'label': 'X', 'type': 'nope'}], [{'label': '', 'type': 'text'}],
                        [{'key': 'bad key', 'label': 'X', 'type': 'text'}],
                        [{'key': 'c_a', 'label': 'X', 'type': 'text'}, {'key': 'c_a', 'label': 'Y', 'type': 'text'}],
                        [{'label': f'C{i}', 'type': 'text'} for i in range(21)], 'nope'):
            with self.subTest(columns=columns):
                with self.assertRaises(BusinessProcessingError):
                    trackers.war_room_trackers_validate_columns(columns)


class TestValues(TestCase):

    def _check(self, column, good, bad):
        for value in good:
            with self.subTest(value=value):
                trackers.war_room_trackers_validate_value(column, value)
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(BusinessProcessingError):
                    trackers.war_room_trackers_validate_value(column, value)

    def test_each_type_is_validated(self):
        self._check({'label': 'N', 'type': 'number'}, [1, 2.5, -3], ['1', True, float('nan')])
        self._check({'label': 'D', 'type': 'date'}, ['2026-10-21'], ['2026-13-01', '21/10/2026', 1])
        self._check({'label': 'T', 'type': 'datetime'}, ['2026-10-21T10:00:00Z', '2026-10-21T10:00+02:00'],
                    ['yesterday', 5])
        self._check({'label': 'S', 'type': 'select', 'options': ['a']}, ['a'], ['b', ['a']])
        self._check({'label': 'M', 'type': 'multiselect', 'options': ['a', 'b']}, [['a', 'b'], []], [['c'], 'a'])
        self._check({'label': 'C', 'type': 'checkbox'}, [True, False], [1, 'yes'])
        self._check({'label': 'U', 'type': 'user'}, [3], [0, '3', True])
        self._check({'label': 'L', 'type': 'url'}, ['https://x.example/a'], ['ftp://x', 'javascript:alert(1)'])
        self._check({'label': 'X', 'type': 'text'}, ['a'], ['x' * 1025, 3])
        self._check({'label': 'X', 'type': 'longtext'}, ['x' * 5000], ['x' * 20001])

    def test_none_clears(self):
        self.assertIsNone(trackers.war_room_trackers_validate_value({'label': 'N', 'type': 'number'}, None))

    def test_unknown_columns_and_users_are_refused(self):
        columns = [{'key': 'c_owner', 'label': 'Owner', 'type': 'user'}]
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_validate_values(columns, {'c_other': 1})
        with patch(f'{_BIZ}.war_room_trackers_db_existing_user_ids', return_value=set()):
            with self.assertRaises(BusinessProcessingError):
                trackers.war_room_trackers_validate_values(columns, {'c_owner': 42})
        with patch(f'{_BIZ}.war_room_trackers_db_existing_user_ids', return_value={42}):
            self.assertEqual(trackers.war_room_trackers_validate_values(columns, {'c_owner': 42}), {'c_owner': 42})


class TestValidation(TestCase):

    def test_colors_and_icons(self):
        self.assertEqual(trackers.war_room_trackers_validate_color('red'), 'red')
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_validate_color('#ff0000')
        self.assertIsNone(trackers.war_room_trackers_validate_icon(''))
        self.assertEqual(trackers.war_room_trackers_validate_icon('shield-check'), 'shield-check')
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_validate_icon('<svg>')

    def test_source(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_validate_source('vulnerabilities')


class TestReorder(TestCase):

    def test_listed_ids_come_first(self):
        objects = [SimpleNamespace(id=i, sort_order=i) for i in (1, 2, 3, 4)]
        ordered = trackers.war_room_trackers_reorder(objects, [3, 1])
        self.assertEqual([o.id for o in ordered], [3, 1, 2, 4])
        self.assertEqual([o.sort_order for o in ordered], [0, 1, 2, 3])

    def test_unknown_ids_are_refused(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_reorder([SimpleNamespace(id=1, sort_order=0)], [2])


class TestIocGrouping(TestCase):

    def test_same_value_in_several_cases_is_one_row_keyed_by_the_lowest_id(self):
        items = trackers.war_room_trackers_group_iocs([
            {'ioc_id': 8, 'ioc_value': 'evil.com', 'ioc_type_name': 'domain', 'case_id': 2, 'case_name': 'B',
             'group_key': 'ioc:evil.com'},
            {'ioc_id': 5, 'ioc_value': 'Evil.com', 'ioc_type_name': 'domain', 'case_id': 1, 'case_name': 'A',
             'group_key': 'ioc:evil.com'},
            {'ioc_id': 6, 'ioc_value': '1.2.3.4', 'ioc_type_name': 'ip', 'case_id': 1, 'case_name': 'A',
             'group_key': 'ioc:1.2.3.4'},
        ])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['item_id'], 5)
        self.assertEqual(items[0]['member_ids'], (5, 8))
        self.assertEqual(items[0]['item']['case_ids'], [2, 1])

    def test_stored_state_of_any_member_is_found(self):
        item = {'item_type': 'ioc', 'item_id': 5, 'member_ids': (5, 8)}
        stored = _row(1, 'ioc', 8)
        self.assertIs(trackers.war_room_trackers_match_stored(item, {('ioc', 8): stored}), stored)
        self.assertIsNone(trackers.war_room_trackers_match_stored(item, {('ioc', 9): stored}))


class TestApplyState(TestCase):

    def test_steps_note_and_values_produce_one_event_per_change(self):
        row = _row(1, 'asset', 4, note='old', values={'c_n': 1})
        columns = [{'key': 'c_n', 'label': 'N', 'type': 'number'}, {'key': 'c_t', 'label': 'T', 'type': 'text'}]
        to_add, to_remove, events = trackers.war_room_trackers_apply_state(
            row, {1, 2}, {'add_step_ids': [2, 3], 'remove_step_ids': [1, 9], 'note': 'new',
                          'values': {'c_n': 1, 'c_t': 'x'}},
            columns, {1: 'A', 2: 'B', 3: 'C'})
        self.assertEqual(to_add, [3])
        self.assertEqual(to_remove, [1])
        self.assertEqual([e[0] for e in events], ['step_added', 'step_removed', 'note_changed', 'value_changed'])
        self.assertEqual(events[3][1], {'column_key': 'c_t', 'column_label': 'T', 'old': None, 'new': 'x'})
        self.assertEqual(row.values, {'c_n': 1, 'c_t': 'x'})
        self.assertEqual(row.note, 'new')

    def test_set_replaces_the_steps(self):
        row = _row(1, 'asset', 4)
        to_add, to_remove, _ = trackers.war_room_trackers_apply_state(row, {1, 2}, {'set_step_ids': [2, 3]}, [], {})
        self.assertEqual((to_add, to_remove), ([3], [1]))

    def test_clearing_a_value_removes_it(self):
        row = _row(1, 'asset', 4, values={'c_n': 1})
        _, _, events = trackers.war_room_trackers_apply_state(row, set(), {'values': {'c_n': None}},
                                                             [{'key': 'c_n', 'label': 'N', 'type': 'number'}], {})
        self.assertEqual(row.values, {})
        self.assertEqual(events[0][1]['old'], 1)

    def test_no_change_no_event(self):
        row = _row(1, None, None, label='x', note='n')
        _, _, events = trackers.war_room_trackers_apply_state(row, {1}, {'add_step_ids': [1], 'note': 'n',
                                                                         'label': 'x'}, [], {})
        self.assertEqual(events, [])

    def test_an_item_row_takes_a_custom_label_and_drops_it(self):
        row = _row(1, 'asset', 4)
        _, _, events = trackers.war_room_trackers_apply_state(row, set(), {'label': 'Before reimage'}, [], {})
        self.assertEqual((row.label, events), ('Before reimage', [('label_changed', {'old': None,
                                                                                   'new': 'Before reimage'})]))
        trackers.war_room_trackers_apply_state(row, set(), {'label': None}, [], {})
        self.assertIsNone(row.label)

    def test_a_free_text_row_keeps_a_label(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_apply_state(_row(1, label='Call CERT'), set(), {'label': None}, [], {})


class TestParseStateChanges(TestCase):

    def test_a_blank_label_drops_it(self):
        for value in (None, '', '  '):
            changes = trackers.war_room_trackers_parse_state_changes({'label': value}, _tracker(), [])
            self.assertEqual(changes, {'label': None})
        self.assertEqual(trackers.war_room_trackers_parse_state_changes({'label': ' x '}, _tracker(), []),
                         {'label': 'x'})

    def test_set_is_exclusive_and_steps_must_be_offered(self):
        tracker = _tracker(step_ids=[1])
        steps = [_step(1, 'A'), _step(2, 'B')]
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_parse_state_changes({'set_step_ids': [1], 'add_step_ids': [1]}, tracker,
                                                           steps)
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_parse_state_changes({'add_step_ids': [2]}, tracker, steps)
        changes = trackers.war_room_trackers_parse_state_changes({'add_step_ids': [1], 'note': '  '}, tracker,
                                                                 steps)
        self.assertEqual(changes, {'add_step_ids': [1], 'note': None})


class TestSteps(_Patches):

    def test_create_refuses_a_duplicated_name(self):
        with patch(f'{_BIZ}.war_room_trackers_db_step_find_by_name', return_value=_step(1, 'Isolated')):
            with self.assertRaises(BusinessProcessingError):
                trackers.war_room_trackers_step_create(7, {'name': 'isolated'})

    def test_create_appends_with_the_default_color(self):
        self.patches['war_room_trackers_db_steps'].return_value = [_step(1, 'A', sort_order=4)]
        with patch(f'{_BIZ}.war_room_trackers_db_step_find_by_name', return_value=None):
            step = trackers.war_room_trackers_step_create(7, {'name': ' Isolated '})
        self.assertEqual((step.name, step.color, step.sort_order), ('Isolated', 'slate', 5))

    def test_delete_prunes_the_trackers(self):
        tracker = _tracker(step_ids=[1, 2])
        other = _tracker(id=5, step_ids=None)
        self.patches['war_room_trackers_db_trackers'].return_value = [tracker, other]
        with patch(f'{_BIZ}.war_room_trackers_db_step_get', return_value=_step(1, 'A')):
            trackers.war_room_trackers_step_delete(7, 1)
        self.assertEqual(tracker.step_ids, [2])
        self.assertIsNone(other.step_ids)
        self.patches['war_room_trackers_db_delete'].assert_called_once()

    def test_delete_prunes_the_board(self):
        with patch(f'{_BIZ}.war_room_trackers_db_step_get', return_value=_step(1, 'A')):
            trackers.war_room_trackers_step_delete(7, 1)
        self.patches['war_room_board_widgets_prune'].assert_called_once_with(7, step_id=1)

    def test_unknown_step_is_not_found(self):
        with patch(f'{_BIZ}.war_room_trackers_db_step_get', return_value=None):
            with self.assertRaises(ObjectNotFoundError):
                trackers.war_room_trackers_step_update(7, 1, {'name': 'x'})


class TestTrackers(_Patches):

    def test_create_validates_the_steps(self):
        self.patches['war_room_trackers_db_steps'].return_value = [_step(1, 'A')]
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_create(7, {'name': 'T', 'source': 'assets', 'step_ids': [2]}, 3)
        tracker = trackers.war_room_trackers_create(7, {'name': 'T', 'source': 'assets', 'step_ids': [1]}, 3)
        self.assertEqual((tracker.source, tracker.step_ids, tracker.columns), ('assets', [1], []))

    def test_source_is_immutable(self):
        with patch(f'{_BIZ}.war_room_trackers_db_tracker_get', return_value=_tracker()):
            with self.assertRaises(BusinessProcessingError):
                trackers.war_room_trackers_update(7, 4, {'source': 'assets'})
            trackers.war_room_trackers_update(7, 4, {'source': 'manual', 'name': 'Renamed'})

    def test_columns_resent_without_key_keep_it(self):
        tracker = _tracker(columns=[{'key': 'c_x1', 'label': 'Owner', 'type': 'user'}])
        with patch(f'{_BIZ}.war_room_trackers_db_tracker_get', return_value=tracker):
            trackers.war_room_trackers_update(7, 4, {'columns': [{'label': 'owner', 'type': 'user'},
                                                                 {'label': 'Due', 'type': 'date'}]})
        self.assertEqual([c['key'] for c in tracker.columns], ['c_x1', 'c_due'])

    def test_delete_and_column_changes_prune_the_board(self):
        tracker = _tracker(columns=[{'key': 'c_x1', 'label': 'Owner', 'type': 'user'}])
        with patch(f'{_BIZ}.war_room_trackers_db_tracker_get', return_value=tracker):
            trackers.war_room_trackers_update(7, 4, {'name': 'Renamed'})
            self.patches['war_room_board_widgets_prune'].assert_not_called()
            trackers.war_room_trackers_update(7, 4, {'columns': []})
            self.patches['war_room_board_widgets_prune'].assert_called_once_with(7, tracker_columns=(4, []))
            trackers.war_room_trackers_delete(7, 4)
        self.patches['war_room_board_widgets_prune'].assert_called_with(7, tracker_id=4)

    def test_row_count_of_a_mirror_is_the_visible_items(self):
        self.patches['war_room_scope_list_assets'].return_value = {
            'data': [_asset(1, 'a'), _asset(2, 'b')], 'truncated': False}
        self.patches['war_room_trackers_db_row_counts'].return_value = {5: 3}
        out = trackers.war_room_trackers_serialize_many(
            7, [_tracker(source='assets'), _tracker(id=5), _tracker(id=6, source='assets')],
            trackers.WarRoomTrackerAccess([1]))
        self.assertEqual([t['row_count'] for t in out], [2, 3, 2])
        self.patches['war_room_scope_list_assets'].assert_called_once()
        self.assertEqual(out[0]['created_by'], {'id': 3, 'name': 'Analyst'})

    def test_no_readable_case_no_item(self):
        out = trackers.war_room_trackers_serialize_many(7, [_tracker(source='iocs')],
                                                        trackers.WarRoomTrackerAccess([]))
        self.assertEqual(out[0]['row_count'], 0)
        self.patches['war_room_scope_list_iocs'].assert_not_called()


def _case_item(case_id, name):
    return trackers._item('case', case_id, name, {'case_id': case_id},
                          timeline=trackers.war_room_tracker_timelines_item_links(case_links=[(case_id, (), ())]))


def _step_rows(*pairs):
    return [SimpleNamespace(row_id=row_id, step_id=step_id, set_by_id=3, set_at=None) for row_id, step_id in pairs]


def _widget(widget_id, widget_type, **config):
    return {'id': widget_id, 'type': widget_type, 'title': None, 'column': 'main', 'config': config}


class TestBoardRowFilter(TestCase):

    def test_single_and_multi_values(self):
        row_filter = {'column_key': 'c_sector', 'values': ['Bank', 'Health']}
        self.assertTrue(trackers.war_room_trackers_board_row_matches(_row(1, values={'c_sector': 'Bank'}),
                                                                     row_filter))
        self.assertFalse(trackers.war_room_trackers_board_row_matches(_row(1, values={'c_sector': 'Energy'}),
                                                                      row_filter))
        self.assertTrue(trackers.war_room_trackers_board_row_matches(
            _row(1, values={'c_sector': ['Energy', 'Health']}), row_filter))
        self.assertFalse(trackers.war_room_trackers_board_row_matches(_row(1), row_filter))
        # A mirrored item never touched has no value.
        self.assertFalse(trackers.war_room_trackers_board_row_matches(None, row_filter))
        self.assertTrue(trackers.war_room_trackers_board_row_matches(None, None))


class TestBoardData(_Patches):
    """Entities: a manual tracker of 4 cases (one the caller cannot read)
    and a free-text row; steps Contacted (1), Ack (2), Action required (3)."""

    def setUp(self):
        super().setUp()
        self.patches['war_room_trackers_db_steps'].return_value = [
            _step(1, 'Contacted'), _step(2, 'Ack'), _step(3, 'Action required')]
        columns = [{'key': 'c_sector', 'label': 'Sector', 'type': 'select', 'options': ['Bank', 'Health']},
                   {'key': 'c_ok', 'label': 'OK', 'type': 'checkbox'}]
        self.patches['war_room_trackers_db_trackers'].return_value = [
            _tracker(step_ids=[1, 2, 3], columns=columns), _tracker(id=5, name='Other')]
        self.patches['war_room_trackers_db_rows'].side_effect = lambda tracker_id, **_kwargs: [
            _row(10, 'case', 1, values={'c_sector': 'Bank', 'c_ok': True}),
            _row(11, 'case', 2, values={'c_sector': 'Health'}),
            _row(12, 'case', 9, values={'c_sector': 'Bank'}),
            _row(13, label='Call CERT'),
        ] if tracker_id == 4 else []
        self.patches['war_room_trackers_db_row_steps'].return_value = _step_rows(
            (10, 1), (10, 2), (11, 1), (11, 3), (12, 1), (12, 3))
        self.patches['war_room_cases_list'].return_value = [
            SimpleNamespace(case_id=case_id, case_name=f'Entity {case_id}', customer_id=2, customer_name='C',
                            state_name='Open', close_date=None) for case_id in (1, 2)]

    def _data(self, *widgets):
        return trackers.war_room_trackers_board_data(7, trackers.WarRoomTrackerAccess([1, 2]), list(widgets))

    def test_progress_counts_the_rows_per_step(self):
        out = self._data(_widget('w_p', 'tracker_progress', tracker_id=4, step_ids=None, filter=None))
        data = out['data']['w_p']
        self.assertEqual((data['rows_total'], data['rows_without_step'], data['truncated']), (4, 1, False))
        self.assertEqual(data['steps'], [{'step_id': 1, 'count': 3}, {'step_id': 2, 'count': 1},
                                         {'step_id': 3, 'count': 2}])
        self.assertEqual(data['tracker'], {'id': 4, 'name': 'Remediation', 'icon': None, 'source': 'manual'})
        self.assertEqual([step['id'] for step in out['steps']], [1, 2, 3])

    def test_progress_with_chosen_steps_and_a_filter(self):
        out = self._data(_widget('w_p', 'tracker_progress', tracker_id=4, step_ids=[3, 1],
                                 filter={'column_key': 'c_sector', 'values': ['Bank']}))
        data = out['data']['w_p']
        self.assertEqual(data['rows_total'], 2)
        self.assertEqual(data['steps'], [{'step_id': 3, 'count': 1}, {'step_id': 1, 'count': 2}])

    def test_count_of_total_and_of_all_rows(self):
        out = self._data(_widget('w_c', 'tracker_count', tracker_id=4, step_ids=[2, 3]),
                         _widget('w_all', 'tracker_count', tracker_id=4, step_ids=[]))
        self.assertEqual(out['data']['w_c'], {'count': 3, 'total': 4, 'truncated': False,
                                              'tracker': out['data']['w_c']['tracker']})
        self.assertEqual(out['data']['w_all']['count'], 4)

    def test_list_rows_carrying_the_steps(self):
        out = self._data(_widget('w_l', 'tracker_list', tracker_id=4, step_ids=[3]))
        data = out['data']['w_l']
        self.assertEqual(data['total'], 2)
        visible, restricted = data['rows']
        self.assertEqual((visible['key'], visible['label'], visible['case_ids'], visible['step_ids']),
                         ('row:11', 'Entity 2', [2], [1, 3]))
        self.assertTrue(restricted['restricted'])
        self.assertIsNone(restricted['label'])
        self.assertEqual(restricted['case_ids'], [])

    def test_list_is_capped(self):
        rows = [_row(row_id, label=f'Entity {row_id}') for row_id in range(1, 61)]
        self.patches['war_room_trackers_db_rows'].side_effect = None
        self.patches['war_room_trackers_db_rows'].return_value = rows
        self.patches['war_room_trackers_db_row_steps'].return_value = _step_rows(*((row.id, 3) for row in rows))
        data = self._data(_widget('w_l', 'tracker_list', tracker_id=4, step_ids=[3]))['data']['w_l']
        self.assertEqual(data['total'], 60)
        self.assertEqual(len(data['rows']), trackers.WAR_ROOM_BOARD_LIST_ROWS)

    def test_breakdown_per_option_and_step(self):
        data = self._data(_widget('w_b', 'tracker_breakdown', tracker_id=4, column_key='c_sector',
                                  step_ids=[1, 3]))['data']['w_b']
        self.assertEqual((data['column_label'], data['step_ids'], data['rows_total']), ('Sector', [1, 3], 4))
        self.assertEqual(data['groups'], [{'value': 'Bank', 'rows': 2, 'counts': [2, 1]},
                                          {'value': 'Health', 'rows': 1, 'counts': [1, 1]},
                                          {'value': None, 'rows': 1, 'counts': [0, 0]}])
        checkbox = self._data(_widget('w_b', 'tracker_breakdown', tracker_id=4, column_key='c_ok',
                                      step_ids=[2]))['data']['w_b']
        self.assertEqual(checkbox['groups'], [{'value': True, 'rows': 1, 'counts': [1]},
                                              {'value': False, 'rows': 3, 'counts': [0]}])
        gone = self._data(_widget('w_b', 'tracker_breakdown', tracker_id=4, column_key='c_gone', step_ids=None))
        self.assertEqual(gone['data']['w_b']['missing'], 'column')

    def test_deleted_tracker_is_reported_missing(self):
        out = self._data(_widget('w_p', 'tracker_progress', tracker_id=99, step_ids=None))
        self.assertEqual(out['data']['w_p'], {'missing': 'tracker'})

    def test_case_highlights_of_the_cases_widget(self):
        out = self._data(_widget('cases', 'cases', highlight_step_ids=[3, 2], tracker_ids=None))
        self.assertEqual(out['case_highlights'], {1: [{'step_id': 2, 'count': 1}],
                                                  2: [{'step_id': 3, 'count': 1}]})
        self.assertEqual(out['data'], {})
        only_other = self._data(_widget('cases', 'cases', highlight_step_ids=[3], tracker_ids=[5]))
        self.assertEqual(only_other['case_highlights'], {})

    def test_nothing_listed_without_tracker_widget(self):
        out = self._data(_widget('cases', 'cases', highlight_step_ids=[], tracker_ids=None),
                         _widget('w_n', 'note', text='x'))
        self.assertEqual((out['data'], out['case_highlights']), ({}, {}))
        self.patches['war_room_trackers_db_rows'].assert_not_called()


class TestRowsList(_Patches):

    def test_a_mirror_merges_the_items_with_their_state(self):
        self.patches['war_room_scope_list_assets'].return_value = {
            'data': [_asset(1, 'dc01'), _asset(2, 'ws02')], 'truncated': False}
        self.patches['war_room_trackers_db_rows'].return_value = [_row(10, 'asset', 2, note='done'),
                                                                  _row(11, 'asset', 99, note='gone')]
        self.patches['war_room_trackers_db_row_steps'].return_value = [
            SimpleNamespace(row_id=10, step_id=1, set_by_id=3, set_at=None)]
        result = trackers.war_room_trackers_rows_list(7, _tracker(source='assets'), trackers.WarRoomTrackerAccess([1]))
        rows = result['data']
        self.assertEqual([r['key'] for r in rows], ['asset:1', 'asset:2'])
        self.assertIsNone(rows[0]['row_id'])
        self.assertEqual(rows[1]['row_id'], 10)
        self.assertEqual(rows[1]['note'], 'done')
        self.assertEqual(rows[1]['steps'], [{'step_id': 1, 'set_by': {'id': 3, 'name': 'Analyst'}, 'set_at': None}])
        self.assertEqual(rows[0]['item']['flags'][0]['name'], 'Isolated')
        self.assertFalse(result['truncated'])

    def test_a_manual_row_on_an_unreadable_item_is_restricted(self):
        self.patches['war_room_trackers_db_rows'].return_value = [_row(10, 'asset', 2), _row(11, label='Call CERT')]
        rows = trackers.war_room_trackers_rows_list(7, _tracker(), trackers.WarRoomTrackerAccess([]))['data']
        self.assertTrue(rows[0]['restricted'])
        self.assertIsNone(rows[0]['item'])
        self.assertIsNone(rows[0]['label'])
        self.assertEqual((rows[1]['key'], rows[1]['label'], rows[1]['restricted']), ('row:11', 'Call CERT', False))

    def test_registry_assets_need_the_registry_scope(self):
        self.patches['war_room_managed_assets_list'].return_value = {'data': [
            {'managed_asset_id': 3, 'asset_name': 'srv', 'case_id': 1, 'case_name': 'A'},
            {'managed_asset_id': 3, 'asset_name': 'srv', 'case_id': 2, 'case_name': 'B'},
        ], 'truncated': False}
        tracker = _tracker(source='registry_assets')
        self.assertEqual(trackers.war_room_trackers_rows_list(7, tracker, trackers.WarRoomTrackerAccess([1]))['data'],
                         [])
        rows = trackers.war_room_trackers_rows_list(7, tracker, trackers.WarRoomTrackerAccess([1], 'scope'))['data']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['item']['case_ids'], [1, 2])

    def test_cases_are_limited_to_the_readable_ones(self):
        self.patches['war_room_cases_list'].return_value = [
            SimpleNamespace(case_id=1, case_name='A', customer_id=2, customer_name='C', state_name='Open',
                            close_date=None),
            SimpleNamespace(case_id=2, case_name='B', customer_id=2, customer_name='C', state_name='Open',
                            close_date=None)]
        rows = trackers.war_room_trackers_rows_list(7, _tracker(source='cases'),
                                                    trackers.WarRoomTrackerAccess([2]))['data']
        self.assertEqual([r['item_id'] for r in rows], [2])


class TestRowsState(_Patches):

    def setUp(self):
        super().setUp()
        self.patches['war_room_scope_list_assets'].return_value = {'data': [_asset(1, 'dc01')], 'truncated': False}
        self.patches['war_room_trackers_db_steps'].return_value = [_step(1, 'Isolated')]

    def test_a_mirrored_item_gets_its_row_on_its_first_change(self):
        result, changes = trackers.war_room_trackers_rows_update_state(
            7, _tracker(source='assets'), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'add_step_ids': [1]},
            trackers.WarRoomTrackerAccess([1]), 3)
        rows = self.added_of(WarRoomTrackerRow)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].item_type, rows[0].item_id, rows[0].updated_by_id), ('asset', 1, 3))
        self.assertEqual(len(self.added_of(WarRoomTrackerRowStep)), 1)
        events = self.added_of(WarRoomTrackerRowEvent)
        self.assertEqual([(e.action, e.details) for e in events],
                         [('step_added', {'step_id': 1, 'step_name': 'Isolated'})])
        self.assertEqual(result['data'][0]['row_id'], rows[0].id)
        self.assertEqual(trackers.war_room_trackers_describe_changes(changes), 'set Isolated on 1 asset')

    def test_no_change_creates_nothing(self):
        result, changes = trackers.war_room_trackers_rows_update_state(
            7, _tracker(source='assets'), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'remove_step_ids': [1]},
            trackers.WarRoomTrackerAccess([1]), 3)
        self.assertEqual(self.added, [])
        self.assertIsNone(result['data'][0]['row_id'])
        self.assertEqual(changes, [])

    def test_an_item_out_of_the_source_is_refused(self):
        for ref in ({'item_type': 'asset', 'item_id': 2}, {'item_type': 'ioc', 'item_id': 1}, {'row_id': 5}):
            with self.subTest(ref=ref):
                with self.assertRaises(BusinessProcessingError):
                    trackers.war_room_trackers_rows_update_state(
                        7, _tracker(source='assets'), {'rows': [ref], 'note': 'x'},
                        trackers.WarRoomTrackerAccess([1]), 3)

    def test_a_manual_row_is_updated_in_place(self):
        row = _row(10, label='Call CERT')
        self.patches['war_room_trackers_db_rows'].return_value = [row]
        self.patches['war_room_trackers_db_row_steps'].return_value = [
            SimpleNamespace(row_id=10, step_id=1, set_by_id=3, set_at=None)]
        trackers.war_room_trackers_rows_update_state(
            7, _tracker(), {'rows': [{'row_id': 10}], 'set_step_ids': [], 'label': 'Call the CERT'},
            trackers.WarRoomTrackerAccess([1]), 3)
        self.patches['war_room_trackers_db_row_step_remove'].assert_called_once_with(10, 1)
        self.assertEqual(row.label, 'Call the CERT')
        self.assertEqual([e.action for e in self.added_of(WarRoomTrackerRowEvent)],
                         ['step_removed', 'label_changed'])

    def test_a_mirrored_item_gets_a_custom_label_under_the_tracker_lock(self):
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(source='assets'), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'label': 'Patient zero'},
            trackers.WarRoomTrackerAccess([1]), 3)
        self.patches['war_room_trackers_db_tracker_lock'].assert_called_once_with(4)
        row = result['data'][0]
        self.assertEqual((row['key'], row['label'], row['custom_label']), ('asset:1', 'dc01', 'Patient zero'))

    def test_an_item_ref_targets_every_row_of_the_item(self):
        rows = [_row(10, 'asset', 1), _row(11, 'asset', 1), _row(12, 'asset', 2)]
        self.patches['war_room_trackers_db_rows'].return_value = rows
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 1}, {'row_id': 10}], 'note': 'n'},
            trackers.WarRoomTrackerAccess([1]), 3)
        self.assertEqual([r['key'] for r in result['data']], ['row:10', 'row:11'])
        self.assertEqual([row.note for row in rows], ['n', 'n', None])
        self.patches['war_room_trackers_db_tracker_lock'].assert_not_called()

    def test_rows_are_required(self):
        for body in ({}, {'rows': []}, {'rows': [{'item_type': 'nope', 'item_id': 1}]},
                     {'rows': [{'row_id': 1}] * 501}):
            with self.assertRaises(BusinessProcessingError):
                trackers.war_room_trackers_rows_update_state(7, _tracker(), body, trackers.WarRoomTrackerAccess([]),
                                                             3)


class TestRowsAdd(_Patches):

    def test_only_on_manual_trackers(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_rows_add(7, _tracker(source='assets'), {'rows': [{'label': 'x'}]},
                                                trackers.WarRoomTrackerAccess([1]), 3)

    def test_items_must_be_in_the_room_and_duplicates_are_skipped(self):
        self.patches['war_room_scope_list_assets'].return_value = {'data': [_asset(1, 'dc01')], 'truncated': False}
        self.patches['war_room_trackers_db_rows'].return_value = [_row(10, 'asset', 1)]
        access = trackers.WarRoomTrackerAccess([1])
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_rows_add(7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 2}]},
                                                access, 3)
        result = trackers.war_room_trackers_rows_add(
            7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 1}, {'label': 'Call CERT'}], 'note': 'n'},
            access, 3)
        self.assertEqual([r['key'] for r in result['data']], [f'row:{self.added_of(WarRoomTrackerRow)[0].id}'])
        self.assertEqual(result['data'][0]['note'], 'n')
        self.assertEqual([e.action for e in self.added_of(WarRoomTrackerRowEvent)], ['created'])

    def test_an_item_is_added_again_when_duplicates_are_allowed(self):
        self.patches['war_room_scope_list_assets'].return_value = {'data': [_asset(1, 'dc01')], 'truncated': False}
        self.patches['war_room_trackers_db_rows'].return_value = [_row(10, 'asset', 1)]
        result = trackers.war_room_trackers_rows_add(
            7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 1},
                                     {'item_type': 'asset', 'item_id': 1, 'label': 'Second pass'}],
                            'allow_duplicates': True}, trackers.WarRoomTrackerAccess([1]), 3)
        self.assertEqual([(r['item_id'], r['label'], r['custom_label']) for r in result['data']],
                         [(1, 'dc01', None), (1, 'dc01', 'Second pass')])
        self.assertEqual(len({r['key'] for r in result['data']}), 2)
        self.assertEqual([e.details for e in self.added_of(WarRoomTrackerRowEvent)], [{}, {'label': 'Second pass'}])

    def test_allow_duplicates_is_a_boolean(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_rows_add(7, _tracker(), {'rows': [{'label': 'x'}], 'allow_duplicates': 'yes'},
                                                trackers.WarRoomTrackerAccess([1]), 3)

    def test_mirror_rows_cannot_be_deleted(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_rows_delete(_tracker(source='assets'), 1)


class TestStreamLines(TestCase):

    def test_free_text_rows_are_named_then_counted(self):
        self.assertEqual(trackers.war_room_trackers_describe_rows([(None, 'a'), (None, 'b'), (None, 'a'),
                                                                   (None, None)]), 'a, b, a row')
        self.assertEqual(trackers.war_room_trackers_describe_rows([(None, x) for x in 'abcd']), '4 rows')

    def test_items_are_only_counted_by_type(self):
        refs = [('asset', None), ('asset', None), ('ioc', None), ('managed_asset', None), (None, 'Call CERT')]
        self.assertEqual(trackers.war_room_trackers_describe_rows(refs),
                         'Call CERT, 2 assets, 1 IOC, 1 registry asset')

    def test_changes_are_grouped_by_step_and_field(self):
        step = {'step_id': 1, 'step_name': 'Sent email'}
        changes = [
            (None, 'ACME', [('step_added', step), ('note_changed', {'old': None, 'new': 'x'})]),
            ('customer', None, [('step_added', step), ('step_removed', {'step_id': 2, 'step_name': 'Pending'})]),
            (None, 'Initech', [('value_changed', {'column_key': 'c_owner', 'column_label': 'Owner', 'old': None,
                                                  'new': 'bob'})]),
        ]
        self.assertEqual(trackers.war_room_trackers_describe_changes(changes),
                         'set Sent email on ACME, 1 customer; cleared Pending from 1 customer; '
                         'edited the note of ACME; edited Owner of Initech')

    def test_nothing_changed_gives_no_line(self):
        self.assertIsNone(trackers.war_room_trackers_describe_changes([]))


class TestHistory(_Patches):

    def test_a_mirror_hides_the_history_of_items_out_of_sight(self):
        self.patches['war_room_scope_list_assets'].return_value = {'data': [_asset(1, 'dc01')], 'truncated': False}
        self.patches['war_room_trackers_db_rows'].return_value = [_row(10, 'asset', 1), _row(11, 'asset', 2)]
        self.patches['war_room_trackers_db_events'].return_value = [
            SimpleNamespace(id=1, row_id=11, user_id=3, created_at=None, action='note_changed', details={}),
            SimpleNamespace(id=2, row_id=10, user_id=None, created_at=None, action='step_added', details={}),
        ]
        self.patches['war_room_trackers_db_rows_keys'].return_value = {10: ('asset', 1)}
        events = trackers.war_room_trackers_history(7, _tracker(source='assets'), trackers.WarRoomTrackerAccess([1]))
        self.assertEqual([(e['id'], e['row_key'], e['user']) for e in events], [(2, 'asset:1', None)])

        trackers.war_room_trackers_history(7, _tracker(source='assets'), trackers.WarRoomTrackerAccess([1]), row_id=11)
        self.assertEqual(self.patches['war_room_trackers_db_events'].call_args.kwargs['row_ids'], [])

        trackers.war_room_trackers_history(7, _tracker(source='assets'), trackers.WarRoomTrackerAccess([1]),
                                           item_type='asset', item_id=1)
        self.assertEqual(self.patches['war_room_trackers_db_events'].call_args.kwargs['row_ids'], [10])

    def test_a_manual_row_is_keyed_by_its_id(self):
        self.patches['war_room_trackers_db_events'].return_value = [
            SimpleNamespace(id=1, row_id=10, user_id=None, created_at=None, action='created', details={})]
        self.patches['war_room_trackers_db_rows_keys'].return_value = {10: ('asset', 1)}
        events = trackers.war_room_trackers_history(7, _tracker(), trackers.WarRoomTrackerAccess([1]))
        self.assertEqual(events[0]['row_key'], 'row:10')
