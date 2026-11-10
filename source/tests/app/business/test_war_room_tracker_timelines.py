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

"""Unit tests for the timeline events written by war-room trackers (no
database): settings, dates, texts, where the events go and who may read
them."""

import datetime
import itertools
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_tracker_templates as templates
from app.business import war_room_tracker_timelines as timelines
from app.business import war_room_trackers as trackers
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.errors import BusinessProcessingError
from app.models.war_rooms import WarRoomTimeline
from app.models.war_rooms import WarRoomTimelineEvent
from app.models.war_rooms import WarRoomTrackerRowEvent
from app.models.war_rooms import WarRoomTrackerRowTimelineEvent


_TL = 'app.business.war_room_tracker_timelines'
_BIZ = 'app.business.war_room_trackers'
_NOW = datetime.datetime(2026, 10, 23, 12, 0, 0)
_COLUMNS = [{'key': 'c_owner', 'label': 'Owner', 'type': 'user'},
            {'key': 'c_due', 'label': 'Due', 'type': 'date'},
            {'key': 'c_done_at', 'label': 'Done at', 'type': 'datetime'},
            {'key': 'c_ticket', 'label': 'Ticket', 'type': 'text'}]


def _sync(mode='changes', target='cases', date='changed_at', war_room_timeline_id=None):
    return {'mode': mode, 'target': target, 'date': date, 'war_room_timeline_id': war_room_timeline_id}


def _tracker(**kwargs):
    fields = {'id': 4, 'war_room_id': 7, 'name': 'Remediation', 'icon': None, 'description': None,
              'source': 'assets', 'step_ids': None, 'columns': _COLUMNS, 'show_asset_flags': False, 'sort_order': 0,
              'timeline_sync': _sync(), 'created_by_id': 3, 'created_at': None, 'updated_at': None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _row(row_id, item_type='asset', item_id=1, label=None, **kwargs):
    fields = {'id': row_id, 'tracker_id': 4, 'item_type': item_type, 'item_id': item_id, 'label': label,
              'note': None, 'values': {}}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _asset_item(asset_id=1, case_id=1, label='dc01'):
    return {'item_type': 'asset', 'item_id': asset_id, 'label': label, 'item': {},
            'timeline': timelines.war_room_tracker_timelines_item_links(
                case_links=[(case_id, (asset_id,), ())], visibility_case_ids=[case_id])}


def _ioc_item(case_iocs, label='evil.com'):
    return {'item_type': 'ioc', 'item_id': min(i for ids in case_iocs.values() for i in ids), 'label': label,
            'item': {}, 'timeline': timelines.war_room_tracker_timelines_item_links(
                case_links=[(case_id, (), ids) for case_id, ids in case_iocs.items()],
                visibility_case_ids=list(case_iocs))}


def _managed_item(managed_asset_id=3, case_ids=(1, 2)):
    return {'item_type': 'managed_asset', 'item_id': managed_asset_id, 'label': 'srv', 'item': {},
            'timeline': timelines.war_room_tracker_timelines_item_links(
                case_links=[(case_id, (), ()) for case_id in case_ids], managed_asset_id=managed_asset_id)}


class _Db(TestCase):
    """Patches the persistence of the timeline module; `self.added`
    collects what was flushed, with fresh ids."""

    def setUp(self):
        self.added = []
        self.case_events = {}
        self.room_events = {}
        self.room_timelines = {}
        ids = itertools.count(500)

        def _add(obj):
            if isinstance(obj, CasesEvent):
                obj.event_id = next(ids)
                self.case_events[obj.event_id] = obj
            elif getattr(obj, 'id', None) is None and not isinstance(obj, (CaseTimeline, WarRoomTimeline)):
                obj.id = next(ids)
                if isinstance(obj, WarRoomTimelineEvent):
                    self.room_events[obj.id] = obj
            self.added.append(obj)
            return obj

        def _get_or_create(_owner_id, _name, build):
            timeline = build()
            timeline.timeline_id = next(ids)
            self.added.append(timeline)
            return timeline, True

        self.patches = {
            'war_room_tracker_timelines_db_add': MagicMock(side_effect=_add),
            'war_room_tracker_timelines_db_case_timeline': MagicMock(side_effect=_get_or_create),
            'war_room_tracker_timelines_db_war_room_timeline_by_name': MagicMock(side_effect=_get_or_create),
            'war_room_tracker_timelines_db_war_room_timeline': MagicMock(
                side_effect=lambda _war_room_id, timeline_id: self.room_timelines.get(timeline_id)),
            'war_room_tracker_timelines_db_link_case_event': MagicMock(),
            'war_room_tracker_timelines_db_case_event': MagicMock(
                side_effect=lambda _case_id, event_id: self.case_events.get(event_id)),
            'war_room_tracker_timelines_db_war_room_event': MagicMock(
                side_effect=lambda _war_room_id, event_id: self.room_events.get(event_id)),
            'war_room_tracker_timelines_db_state_links': MagicMock(return_value={}),
            'war_room_trackers_db_user_names': MagicMock(side_effect=lambda ids: {i: f'User {i}' for i in ids}),
            'update_timeline_state': MagicMock(),
            'track_activity': MagicMock(),
            'add_obj_history_entry': MagicMock(),
        }
        self._patchers = [patch(f'{_TL}.{name}', mock) for name, mock in self.patches.items()]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()

    def added_of(self, cls):
        return [obj for obj in self.added if isinstance(obj, cls)]

    def writer(self, tracker=None, writable=(1,), step_names=None):
        return timelines.WarRoomTrackerTimelineWriter(7, tracker or _tracker(), writable, 3, _NOW,
                                                      step_names or {1: 'Isolated', 2: 'Restored'})


# ----------------------------------------------------------- Validation --

class TestValidate(TestCase):

    def test_off(self):
        self.assertIsNone(timelines.war_room_tracker_timelines_validate(None, _COLUMNS, 7))

    def test_defaults_to_the_change_time(self):
        self.assertEqual(timelines.war_room_tracker_timelines_validate({'mode': 'state', 'target': 'war_room'},
                                                                       _COLUMNS, None),
                         _sync('state', 'war_room'))

    def test_mode_target_and_shape_are_checked(self):
        for value in ('x', [], {'mode': 'all', 'target': 'cases'}, {'mode': 'state', 'target': 'everywhere'},
                      {'mode': 'state'}):
            with self.subTest(value=value):
                with self.assertRaises(BusinessProcessingError):
                    timelines.war_room_tracker_timelines_validate(value, _COLUMNS, None)

    def test_the_date_is_a_date_or_datetime_column(self):
        for key in ('c_due', 'c_done_at'):
            self.assertEqual(timelines.war_room_tracker_timelines_validate(
                {'mode': 'changes', 'target': 'cases', 'date': key}, _COLUMNS, None)['date'], key)
        for key in ('c_ticket', 'c_owner', 'c_nope', 3):
            with self.subTest(key=key):
                with self.assertRaises(BusinessProcessingError):
                    timelines.war_room_tracker_timelines_validate(
                        {'mode': 'changes', 'target': 'cases', 'date': key}, _COLUMNS, None)

    def test_the_war_room_timeline_must_be_of_the_room(self):
        with patch(f'{_TL}.war_room_tracker_timelines_db_war_room_timeline',
                   side_effect=lambda _war_room_id, timeline_id: object() if timeline_id == 5 else None) as lookup:
            value = {'mode': 'state', 'target': 'war_room', 'war_room_timeline_id': 5}
            self.assertEqual(timelines.war_room_tracker_timelines_validate(value, [], 7)['war_room_timeline_id'], 5)
            lookup.assert_called_with(7, 5)
            for timeline_id in (6, '5', True, -1):
                with self.subTest(timeline_id=timeline_id):
                    with self.assertRaises(BusinessProcessingError):
                        timelines.war_room_tracker_timelines_validate({**value, 'war_room_timeline_id': timeline_id},
                                                                      [], 7)

    def test_without_war_room_the_timeline_is_dropped(self):
        value = {'mode': 'state', 'target': 'war_room', 'war_room_timeline_id': 5}
        self.assertIsNone(timelines.war_room_tracker_timelines_validate(value, [], None)['war_room_timeline_id'])

    def test_the_dating_column_cannot_go(self):
        sync = _sync(date='c_due')
        timelines.war_room_tracker_timelines_check_columns(sync, _COLUMNS)
        timelines.war_room_tracker_timelines_check_columns(_sync(), [])
        with self.assertRaises(BusinessProcessingError):
            timelines.war_room_tracker_timelines_check_columns(sync, _COLUMNS[2:])
        with self.assertRaises(BusinessProcessingError):
            timelines.war_room_tracker_timelines_check_columns(sync, [{'key': 'c_due', 'label': 'Due',
                                                                       'type': 'text'}])


class TestTrackerSettings(TestCase):

    def test_create_validates_against_the_new_columns(self):
        data = {'name': 'T', 'source': 'assets', 'columns': [{'label': 'Due', 'type': 'date'}],
                'timeline_sync': {'mode': 'changes', 'target': 'cases', 'date': 'c_due'}}
        tracker = trackers.war_room_trackers_build_tracker(7, data, set(), 0, 3)
        self.assertEqual(tracker.timeline_sync, _sync(date='c_due'))
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_build_tracker(7, {**data, 'columns': []}, set(), 0, 3)

    def test_update_refuses_to_drop_the_dating_column(self):
        tracker = _tracker(columns=[{'key': 'c_due', 'label': 'Due', 'type': 'date'}], timeline_sync=_sync(date='c_due'))
        with patch(f'{_BIZ}.war_room_trackers_db_tracker_get', return_value=tracker), \
                patch(f'{_BIZ}.war_room_board_widgets_prune'), \
                patch(f'{_BIZ}.war_room_trackers_db_commit') as commit:
            with self.assertRaises(BusinessProcessingError):
                trackers.war_room_trackers_update(7, 4, {'columns': []})
            commit.assert_not_called()
            trackers.war_room_trackers_update(7, 4, {'columns': [], 'timeline_sync': None})
        self.assertIsNone(tracker.timeline_sync)

    def test_serialized(self):
        out = trackers.war_room_trackers_serialize(_tracker(), 0, {})
        self.assertEqual(out['timeline_sync'], _sync())
        self.assertEqual(out['timeline_backfill'], 0)
        self.assertEqual(trackers.war_room_trackers_serialize(_tracker(), 0, {}, 12)['timeline_backfill'], 12)


class TestTemplates(TestCase):

    def test_the_date_column_may_be_named_by_label_and_the_room_timeline_is_dropped(self):
        _, definitions = templates.tracker_templates_validate_content({'trackers': [{
            'name': 'T', 'source': 'assets', 'columns': [{'label': 'Notified on', 'type': 'date'}],
            'timeline_sync': {'mode': 'state', 'target': 'war_room', 'date': 'notified on',
                              'war_room_timeline_id': 5}}]})
        self.assertEqual(definitions[0]['timeline_sync'], _sync('state', 'war_room', 'c_notified_on'))

    def test_absent_means_off(self):
        _, definitions = templates.tracker_templates_validate_content({'trackers': [{'name': 'T',
                                                                                     'source': 'manual'}]})
        self.assertIsNone(definitions[0]['timeline_sync'])

    def test_invalid_settings_are_refused(self):
        with self.assertRaises(BusinessProcessingError):
            templates.tracker_templates_validate_content({'trackers': [{
                'name': 'T', 'source': 'manual', 'timeline_sync': {'mode': 'state', 'target': 'mars'}}]})


# ----------------------------------------------------------------- Text --

class TestEventDate(TestCase):

    def test_the_change_time_by_default(self):
        self.assertEqual(timelines.war_room_tracker_timelines_event_date(_sync(), _COLUMNS, {'c_due': '2026-01-02'},
                                                                         _NOW), _NOW)

    def test_a_date_column_dates_at_midnight_utc(self):
        self.assertEqual(timelines.war_room_tracker_timelines_event_date(
            _sync(date='c_due'), _COLUMNS, {'c_due': '2026-01-02'}, _NOW), datetime.datetime(2026, 1, 2))

    def test_a_datetime_column_is_converted_to_utc(self):
        for raw, expected in (('2026-01-02T10:00:00+02:00', datetime.datetime(2026, 1, 2, 8)),
                              ('2026-01-02T10:00:00Z', datetime.datetime(2026, 1, 2, 10)),
                              ('2026-01-02T10:00', datetime.datetime(2026, 1, 2, 10))):
            with self.subTest(raw=raw):
                self.assertEqual(timelines.war_room_tracker_timelines_event_date(
                    _sync(date='c_done_at'), _COLUMNS, {'c_done_at': raw}, _NOW), expected)

    def test_a_missing_or_invalid_value_falls_back_to_the_change_time(self):
        for values in ({}, {'c_due': 'not a date'}, {'c_due': 3}):
            with self.subTest(values=values):
                self.assertEqual(timelines.war_room_tracker_timelines_event_date(
                    _sync(date='c_due'), _COLUMNS, values, _NOW), _NOW)


class TestTexts(TestCase):

    def test_changes(self):
        title, content = timelines.war_room_tracker_timelines_changes_text('Remediation', 'dc01', [
            ('step_added', {'step_id': 1, 'step_name': 'Isolated'}),
            ('step_removed', {'step_id': 2, 'step_name': None}),
            ('value_changed', {'column_key': 'c_owner', 'column_label': 'Owner', 'old': None, 'new': 5}),
            ('value_changed', {'column_key': 'c_ticket', 'column_label': 'Ticket', 'old': 'T-1', 'new': None}),
            ('note_changed', {'old': None, 'new': 'line 1\nline 2'}),
        ], _COLUMNS, {5: 'bob'})
        self.assertEqual(title, 'Remediation · dc01: Isolated set; step #2 cleared; Owner: bob; Ticket cleared; '
                                'note updated')
        self.assertIn('- **Owner**: — → bob', content)
        self.assertIn('- **Ticket**: T-1 → —', content)
        self.assertIn('> line 1\n> line 2', content)
        self.assertNotIn('Changed by', content)

    def test_changes_name_their_author(self):
        _, content = timelines.war_room_tracker_timelines_changes_text(
            'R', 'x', [('note_changed', {'old': None, 'new': 'y'})], [], {}, author='alice')
        self.assertTrue(content.endswith('_Changed by alice_'))

    def test_state(self):
        title, content = timelines.war_room_tracker_timelines_state_text(
            'Remediation', 'dc01', ['Isolated', 'Restored'], _COLUMNS, {'c_owner': 5, 'c_ticket': 'T-1'}, 'ok',
            {5: 'bob'})
        self.assertEqual(title, 'Remediation · dc01: Isolated, Restored')
        self.assertIn('**Owner:** bob', content)
        self.assertIn('**Ticket:** T-1', content)
        self.assertIn('ok', content)
        self.assertNotIn('Last changed by', content)
        _, content = timelines.war_room_tracker_timelines_state_text('R', 'x', [], [], {}, None, {},
                                                                     updated_by='alice')
        self.assertIn('**Last changed by:** alice', content)
        self.assertEqual(timelines.war_room_tracker_timelines_state_text('R', 'x', [], [], {}, None, {})[0],
                         'R · x: no step')

    def test_long_titles_are_cut(self):
        title, _ = timelines.war_room_tracker_timelines_state_text('R', 'x' * 400, [], [], {}, None, {})
        self.assertEqual(len(title), 255)


# ------------------------------------------------------------ Visibility --

class TestEventVisibility(TestCase):

    def test_an_ungated_event_is_visible_to_every_member(self):
        event = SimpleNamespace(visibility_case_ids=None, visibility_managed_asset_id=None)
        self.assertTrue(timelines.war_room_tracker_timelines_event_visible(event, [], lambda _: False))

    def test_a_case_gated_event_needs_one_of_its_cases(self):
        event = SimpleNamespace(visibility_case_ids=[1, 2], visibility_managed_asset_id=None)
        self.assertTrue(timelines.war_room_tracker_timelines_event_visible(event, [2], lambda _: False))
        self.assertFalse(timelines.war_room_tracker_timelines_event_visible(event, [3], lambda _: True))
        self.assertFalse(timelines.war_room_tracker_timelines_event_visible(
            SimpleNamespace(visibility_case_ids=[], visibility_managed_asset_id=None), [1], lambda _: True))

    def test_a_registry_asset_event_needs_the_asset(self):
        event = SimpleNamespace(visibility_case_ids=None, visibility_managed_asset_id=3)
        self.assertTrue(timelines.war_room_tracker_timelines_event_visible(event, [], lambda asset_id: asset_id == 3))
        self.assertFalse(timelines.war_room_tracker_timelines_event_visible(event, [1, 2], lambda _: False))

    def test_gated_case_ids(self):
        events = [SimpleNamespace(visibility_case_ids=[2, 1]), SimpleNamespace(visibility_case_ids=None),
                  SimpleNamespace(visibility_case_ids=[2, 3])]
        self.assertEqual(timelines.war_room_tracker_timelines_gated_case_ids(events), [1, 2, 3])


# --------------------------------------------------------------- Writer --

class TestCaseTarget(_Db):

    def test_off_writes_nothing(self):
        writer = self.writer(_tracker(timeline_sync=None))
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(self.added, [])
        self.assertIsNone(writer.summary())

    def test_a_change_goes_to_the_case_of_the_asset(self):
        writer = self.writer()
        writer.row_changed(_row(10), _asset_item(), [('step_added', {'step_id': 1, 'step_name': 'Isolated'})], {1})
        event, = self.added_of(CasesEvent)
        self.assertEqual((event.case_id, event.event_title, event.event_date, event.event_tz, event.user_id),
                         (1, 'Remediation · dc01: Isolated set', _NOW, '+00:00', 3))
        self.assertEqual(event.event_tags, 'war-room-tracker')
        timeline, = self.added_of(CaseTimeline)
        self.assertEqual((timeline.case_id, timeline.name), (1, 'War-room trackers'))
        self.patches['war_room_tracker_timelines_db_link_case_event'].assert_called_once_with(
            event.event_id, 1, timeline.timeline_id, (1,), ())
        self.patches['update_timeline_state'].assert_called_once_with(1, 3)
        self.assertEqual(writer.summary(), {'events': 1, 'skipped_case_ids': [], 'restricted_rows': 0,
                                            'war_room_rows': 0})
        self.patches['track_activity'].assert_not_called()
        writer.finish()
        self.assertEqual([c.kwargs['caseid'] for c in self.patches['track_activity'].call_args_list], [1, 1])

    def test_the_war_room_is_never_named_in_a_case(self):
        writer = self.writer()
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        writer.finish()
        event, = self.added_of(CasesEvent)
        for text in (event.event_title, event.event_content, event.event_source,
                     *[str(c) for c in self.patches['track_activity'].call_args_list]):
            self.assertNotIn('war room #7', text.lower())
            self.assertNotIn('war_room_id', text)

    def test_a_case_without_full_access_is_skipped(self):
        writer = self.writer(writable=())
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(self.added, [])
        self.patches['update_timeline_state'].assert_not_called()
        self.assertEqual(writer.summary(), {'events': 0, 'skipped_case_ids': [1], 'restricted_rows': 0,
                                            'war_room_rows': 0})

    def test_an_ioc_goes_to_each_writable_case_with_its_iocs(self):
        writer = self.writer(writable={1})
        writer.row_changed(_row(10, 'ioc', 11), _ioc_item({1: (11, 12), 2: (13,)}),
                           [('note_changed', {'old': None, 'new': 'x'})], set())
        event, = self.added_of(CasesEvent)
        self.assertEqual(event.case_id, 1)
        self.patches['war_room_tracker_timelines_db_link_case_event'].assert_called_once_with(
            event.event_id, 1, self.added_of(CaseTimeline)[0].timeline_id, (), (11, 12))
        self.assertEqual(writer.summary()['skipped_case_ids'], [2])

    def test_a_registry_asset_goes_to_the_cases_it_is_seen_in(self):
        writer = self.writer(writable={1, 2})
        writer.row_changed(_row(10, 'managed_asset', 3), _managed_item(),
                           [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(sorted(e.case_id for e in self.added_of(CasesEvent)), [1, 2])

    def test_a_restricted_row_is_never_written(self):
        writer = self.writer()
        writer.row_changed(_row(10), None, [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(self.added, [])
        self.assertEqual(writer.summary()['restricted_rows'], 1)

    def test_rows_without_case_go_to_the_war_room_timeline(self):
        writer = self.writer(_tracker(source='manual'))
        writer.row_changed(_row(10, None, None, label='Call CERT'), None, [('created', {'label': 'Call CERT'})],
                           set())
        self.assertEqual(self.added_of(CasesEvent), [])
        event, = self.added_of(WarRoomTimelineEvent)
        self.assertEqual(event.title, 'Remediation · Call CERT: added to the tracker')
        self.assertIsNone(event.visibility_case_ids)
        self.assertIsNone(event.visibility_managed_asset_id)
        self.assertEqual(writer.summary()['skipped_case_ids'], [])
        self.assertEqual(writer.summary()['war_room_rows'], 1)

    def test_a_customer_goes_to_each_of_its_writable_cases(self):
        item = {'item_type': 'customer', 'item_id': 2, 'label': 'ACME', 'item': {},
                'timeline': timelines.war_room_tracker_timelines_item_links(
                    case_links=[(1, (), ()), (2, (), ())], visibility_case_ids=[1, 2])}
        writer = self.writer(_tracker(source='customers'), writable=[1])
        writer.row_changed(_row(10, 'customer', 2), item, [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual([e.case_id for e in self.added_of(CasesEvent)], [1])
        self.assertEqual(self.added_of(WarRoomTimelineEvent), [])
        self.assertEqual(writer.summary(), {'events': 1, 'skipped_case_ids': [2], 'restricted_rows': 0,
                                            'war_room_rows': 0})

    def test_a_case_less_customer_goes_to_the_war_room(self):
        item = {'item_type': 'customer', 'item_id': 2, 'label': 'ACME', 'item': {},
                'timeline': timelines.war_room_tracker_timelines_item_links()}
        writer = self.writer(_tracker(source='customers'), writable=())
        writer.row_changed(_row(10, 'customer', 2), item, [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(len(self.added_of(WarRoomTimelineEvent)), 1)
        self.assertEqual(writer.summary()['war_room_rows'], 1)

    def test_the_date_column_dates_the_event(self):
        writer = self.writer(_tracker(timeline_sync=_sync(date='c_due')))
        writer.row_changed(_row(10, values={'c_due': '2026-01-02'}), _asset_item(),
                           [('value_changed', {'column_key': 'c_due', 'column_label': 'Due', 'old': None,
                                               'new': '2026-01-02'})], set())
        self.assertEqual(self.added_of(CasesEvent)[0].event_date, datetime.datetime(2026, 1, 2))

    def test_user_columns_are_named(self):
        writer = self.writer()
        writer.row_changed(_row(10, values={'c_owner': 5}), _asset_item(),
                           [('value_changed', {'column_key': 'c_owner', 'column_label': 'Owner', 'old': 4,
                                               'new': 5})], set())
        self.assertIn('User 4 → User 5', self.added_of(CasesEvent)[0].event_content)

    def test_a_live_change_names_the_user_making_it(self):
        writer = self.writer()
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertIn('_Changed by User 3_', self.added_of(CasesEvent)[0].event_content)


class TestWarRoomTarget(_Db):

    def test_an_asset_event_is_gated_by_its_case(self):
        writer = self.writer(_tracker(timeline_sync=_sync(target='war_room')), writable=())
        writer.row_changed(_row(10), _asset_item(case_id=2), [('note_changed', {'old': None, 'new': 'x'})], set())
        event, = self.added_of(WarRoomTimelineEvent)
        self.assertEqual((event.visibility_case_ids, event.tracker_id, event.created_by_id), ([2], 4, 3))
        timeline, = self.added_of(WarRoomTimeline)
        self.assertEqual((timeline.war_room_id, timeline.name, event.timeline_id), (7, 'Trackers',
                                                                                    timeline.timeline_id))
        self.assertEqual(self.added_of(CasesEvent), [])
        self.assertEqual(writer.summary(), {'events': 1, 'skipped_case_ids': [], 'restricted_rows': 0,
                                            'war_room_rows': 0})

    def test_a_registry_asset_event_is_gated_by_the_asset(self):
        writer = self.writer(_tracker(timeline_sync=_sync(target='war_room')))
        writer.row_changed(_row(10, 'managed_asset', 3), _managed_item(),
                           [('note_changed', {'old': None, 'new': 'x'})], set())
        event, = self.added_of(WarRoomTimelineEvent)
        self.assertEqual((event.visibility_case_ids, event.visibility_managed_asset_id), (None, 3))

    def test_the_chosen_timeline_is_used_and_a_deleted_one_replaced(self):
        self.room_timelines[5] = SimpleNamespace(timeline_id=5)
        writer = self.writer(_tracker(timeline_sync=_sync(target='war_room', war_room_timeline_id=5)))
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(self.added_of(WarRoomTimelineEvent)[0].timeline_id, 5)
        del self.room_timelines[5]
        writer = self.writer(_tracker(timeline_sync=_sync(target='war_room', war_room_timeline_id=5)))
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        self.assertEqual(self.added_of(WarRoomTimeline)[0].name, 'Trackers')


class TestStateMode(_Db):

    def test_the_state_event_is_updated_in_place(self):
        tracker = _tracker(timeline_sync=_sync(mode='state'))
        row = _row(10)
        writer = self.writer(tracker)
        writer.row_changed(row, _asset_item(), [('step_added', {'step_id': 1, 'step_name': 'Isolated'})], {1})
        event, = self.added_of(CasesEvent)
        link, = self.added_of(WarRoomTrackerRowTimelineEvent)
        self.assertEqual((link.row_id, link.case_id, link.case_event_id), (10, 1, event.event_id))
        self.assertEqual(event.event_title, 'Remediation · dc01: Isolated')

        self.patches['war_room_tracker_timelines_db_state_links'].return_value = {10: [link]}
        writer = self.writer(tracker)
        writer.prefetch([10])
        row.note = 'contained'
        writer.row_changed(row, _asset_item(), [('step_added', {'step_id': 2, 'step_name': 'Restored'})], {1, 2})
        self.assertEqual(len(self.added_of(CasesEvent)), 1)
        self.assertEqual(len(self.added_of(WarRoomTrackerRowTimelineEvent)), 1)
        self.assertEqual(event.event_title, 'Remediation · dc01: Isolated, Restored')
        self.assertIn('contained', event.event_content)
        self.patches['add_obj_history_entry'].assert_called_with(event, 'updated')

    def test_a_deleted_state_event_is_written_again(self):
        link = WarRoomTrackerRowTimelineEvent(row_id=10, case_id=1, case_event_id=42)
        self.patches['war_room_tracker_timelines_db_state_links'].return_value = {10: [link]}
        writer = self.writer(_tracker(timeline_sync=_sync(mode='state')))
        writer.row_changed(_row(10), _asset_item(), [('note_changed', {'old': None, 'new': 'x'})], set())
        event, = self.added_of(CasesEvent)
        self.assertEqual(link.case_event_id, event.event_id)
        self.assertEqual(self.added_of(WarRoomTrackerRowTimelineEvent), [])

    def test_war_room_state_event_refreshes_its_gate(self):
        tracker = _tracker(timeline_sync=_sync(mode='state', target='war_room'))
        writer = self.writer(tracker)
        writer.row_changed(_row(10), _asset_item(case_id=1), [('note_changed', {'old': None, 'new': 'x'})], set())
        event, = self.added_of(WarRoomTimelineEvent)
        link, = self.added_of(WarRoomTrackerRowTimelineEvent)
        self.assertEqual((link.case_id, link.war_room_event_id), (None, event.id))
        self.patches['war_room_tracker_timelines_db_state_links'].return_value = {10: [link]}
        writer = self.writer(tracker)
        writer.row_state(_row(10), _asset_item(case_id=2), set())
        self.assertEqual(len(self.added_of(WarRoomTimelineEvent)), 1)
        self.assertEqual(event.visibility_case_ids, [2])

    def test_the_state_names_the_last_user_who_changed_the_row(self):
        writer = self.writer(_tracker(timeline_sync=_sync(mode='state')))
        writer.row_state(_row(10, updated_by_id=6, created_by_id=5), _asset_item(), set())
        writer.row_state(_row(11, created_by_id=5), _asset_item(), set())
        writer.row_state(_row(12), _asset_item(), set())
        first, second, third = self.added_of(CasesEvent)
        self.assertEqual(first.user_id, 3)
        self.assertIn('**Last changed by:** User 6', first.event_content)
        self.assertIn('**Last changed by:** User 5', second.event_content)
        self.assertNotIn('Last changed by', third.event_content)

    def test_row_state_only_in_state_mode(self):
        writer = self.writer()
        writer.row_state(_row(10), _asset_item(), set())
        self.assertEqual(self.added, [])


# ---------------------------------------------------- Through the tracker --

def _asset(asset_id, name, case_id=1):
    return {'asset_id': asset_id, 'asset_name': name, 'asset_type_name': 'Windows', 'case_id': case_id,
            'case_name': f'Case {case_id}', 'customer_id': 2, 'customer_name': 'ACME',
            'asset_compromise_status_id': 1, 'analysis_status_id': 2, 'flags': []}


class TestThroughTheTracker(_Db):

    def setUp(self):
        super().setUp()
        ids = itertools.count(100)

        def _add(obj):
            if getattr(obj, 'id', None) is None:
                obj.id = next(ids)
            return obj

        biz = {
            'war_room_trackers_db_add': MagicMock(side_effect=_add),
            'war_room_trackers_db_commit': MagicMock(return_value=True),
            'war_room_trackers_db_steps': MagicMock(return_value=[SimpleNamespace(id=1, name='Isolated')]),
            'war_room_trackers_db_rows': MagicMock(return_value=[]),
            'war_room_trackers_db_row_steps': MagicMock(return_value=[]),
            'war_room_trackers_db_row_step_remove': MagicMock(),
            'war_room_trackers_db_user_names': MagicMock(return_value={}),
            'war_room_trackers_db_existing_user_ids': MagicMock(side_effect=lambda ids: set(ids)),
            'war_room_trackers_db_asset_flags': MagicMock(return_value={}),
            'war_room_trackers_db_rows_max_sort': MagicMock(return_value=-1),
            'war_room_trackers_db_tracker_lock': MagicMock(),
            'war_room_scope_list_assets': MagicMock(return_value={
                'data': [_asset(1, 'dc01', 1), _asset(2, 'ws02', 2)], 'truncated': False}),
            'war_room_scope_list_customers': MagicMock(return_value={'data': [
                {'customer_id': 2, 'customer_name': 'ACME',
                 'cases': [{'case_id': 1, 'case_name': 'A'}, {'case_id': 2, 'case_name': 'B'}]}]}),
        }
        self.biz = biz
        for name, mock in biz.items():
            patcher = patch(f'{_BIZ}.{name}', mock)
            patcher.start()
            self._patchers.append(patcher)

    def test_rows_state_writes_to_the_writable_cases_and_reports_the_others(self):
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 1}, {'item_type': 'asset', 'item_id': 2}],
                            'add_step_ids': [1]},
            trackers.WarRoomTrackerAccess([1, 2], writable_case_ids=[1]), 3)
        self.assertEqual([e.case_id for e in self.added_of(CasesEvent)], [1])
        self.assertEqual(result['timeline'], {'events': 1, 'skipped_case_ids': [2], 'restricted_rows': 0,
                                              'war_room_rows': 0})
        self.biz['war_room_trackers_db_commit'].assert_called_once()
        self.assertEqual([c.kwargs['caseid'] for c in self.patches['track_activity'].call_args_list], [1, 1])

    def test_without_sync_the_response_is_unchanged(self):
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(timeline_sync=None), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'note': 'x'},
            trackers.WarRoomTrackerAccess([1], writable_case_ids=[1]), 3)
        self.assertNotIn('timeline', result)
        self.assertEqual(self.added, [])

    def test_the_access_defaults_to_no_writable_case(self):
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'note': 'x'},
            trackers.WarRoomTrackerAccess([1]), 3)
        self.assertEqual(self.added_of(CasesEvent), [])
        self.assertEqual(result['timeline']['skipped_case_ids'], [1])

    def test_no_actual_change_writes_nothing(self):
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'remove_step_ids': [1]},
            trackers.WarRoomTrackerAccess([1], writable_case_ids=[1]), 3)
        self.assertEqual(self.added, [])
        self.assertEqual(result['timeline']['events'], 0)

    def test_added_rows_are_written(self):
        result = trackers.war_room_trackers_rows_add(
            7, _tracker(source='manual'), {'rows': [{'item_type': 'asset', 'item_id': 2}, {'label': 'Call CERT'}],
                                           'step_ids': [1]},
            trackers.WarRoomTrackerAccess([1, 2], writable_case_ids=[2]), 3)
        self.assertEqual([e.case_id for e in self.added_of(CasesEvent)], [2])
        self.assertEqual(self.added_of(CasesEvent)[0].event_title, 'Remediation · ws02: added to the tracker; '
                                                                   'Isolated set')
        self.assertEqual([e.title for e in self.added_of(WarRoomTimelineEvent)],
                         ['Remediation · Call CERT: added to the tracker; Isolated set'])
        self.assertEqual((result['timeline']['events'], result['timeline']['war_room_rows']), (2, 1))

    def test_a_customer_row_is_written_to_its_cases(self):
        result, _ = trackers.war_room_trackers_rows_update_state(
            7, _tracker(source='customers'), {'rows': [{'item_type': 'customer', 'item_id': 2}], 'note': 'x'},
            trackers.WarRoomTrackerAccess([1, 2], writable_case_ids=[1, 2]), 3)
        self.assertEqual([e.case_id for e in self.added_of(CasesEvent)], [1, 2])
        self.assertEqual(self.added_of(WarRoomTimelineEvent), [])
        self.assertEqual(result['timeline']['events'], 2)

    def test_sync_writes_the_state_of_the_visible_stored_rows(self):
        tracker = _tracker(timeline_sync=_sync(mode='state'))
        self.biz['war_room_trackers_db_rows'].return_value = [
            SimpleNamespace(id=10, tracker_id=4, item_type='asset', item_id=1, label=None, note='n', values={}),
            SimpleNamespace(id=11, tracker_id=4, item_type='asset', item_id=99, label=None, note=None, values={})]
        self.biz['war_room_trackers_db_row_steps'].return_value = [SimpleNamespace(row_id=10, step_id=1)]
        summary = trackers.war_room_trackers_timeline_sync(
            7, tracker, trackers.WarRoomTrackerAccess([1, 2], writable_case_ids=[1, 2]), 3)
        event, = self.added_of(CasesEvent)
        self.assertEqual((event.case_id, event.event_title), (1, 'Remediation · dc01: Isolated'))
        self.assertEqual(summary, {'events': 1, 'skipped_case_ids': [], 'restricted_rows': 0, 'war_room_rows': 0})

    def test_sync_restricted_manual_rows_are_counted(self):
        tracker = _tracker(source='manual', timeline_sync=_sync(mode='state'))
        self.biz['war_room_scope_list_assets'].return_value = {'data': [_asset(1, 'dc01', 1)], 'truncated': False}
        self.biz['war_room_trackers_db_rows'].return_value = [
            SimpleNamespace(id=10, tracker_id=4, item_type='asset', item_id=2, label=None, note=None, values={})]
        summary = trackers.war_room_trackers_timeline_sync(7, tracker, trackers.WarRoomTrackerAccess([1]), 3)
        self.assertEqual(summary, {'events': 0, 'skipped_case_ids': [], 'restricted_rows': 1, 'war_room_rows': 0})

    def test_sync_needs_a_timeline_setting(self):
        with self.assertRaises(BusinessProcessingError):
            trackers.war_room_trackers_timeline_sync(7, _tracker(timeline_sync=None),
                                                     trackers.WarRoomTrackerAccess([1]), 3)

    def test_live_changes_are_marked_in_the_timeline_in_changes_mode_only(self):
        for sync, expected in ((_sync(), True), (_sync(mode='state'), False), (None, False)):
            with self.subTest(sync=sync):
                self.biz['war_room_trackers_db_add'].reset_mock()
                trackers.war_room_trackers_rows_update_state(
                    7, _tracker(timeline_sync=sync), {'rows': [{'item_type': 'asset', 'item_id': 1}], 'note': 'x'},
                    trackers.WarRoomTrackerAccess([1], writable_case_ids=[1]), 3)
                recorded = [c.args[0] for c in self.biz['war_room_trackers_db_add'].call_args_list
                            if isinstance(c.args[0], WarRoomTrackerRowEvent)]
                self.assertEqual([e.in_timeline for e in recorded], [expected])


def _event(event_id, row_id, action, details=None, minute=0, user_id=5):
    return SimpleNamespace(id=event_id, tracker_id=4, row_id=row_id, action=action, details=details or {},
                           created_at=datetime.datetime(2026, 10, 1, 9, minute), in_timeline=False, user_id=user_id)


class TestBackfill(TestThroughTheTracker):

    def setUp(self):
        super().setUp()
        self.events = []
        self.later = []
        self.rows = [SimpleNamespace(id=10, tracker_id=4, item_type=None, item_id=None, label='Call CERT',
                                     note=None, values={'c_due': '2026-10-20'})]
        self.biz['war_room_trackers_db_rows'].return_value = self.rows

        def _pending(row_ids):
            return [e for e in self.events if e.row_id in row_ids and not e.in_timeline]

        backfill = {
            'war_room_trackers_db_events_not_in_timeline': MagicMock(
                side_effect=lambda _tracker_id, row_ids, limit: _pending(row_ids)[:limit]),
            'war_room_trackers_db_events_not_in_timeline_count': MagicMock(
                side_effect=lambda _tracker_id, row_ids: len(_pending(row_ids))),
            'war_room_trackers_db_value_events_after': MagicMock(
                side_effect=lambda row_ids, after_id: [e for e in self.later if e.row_id in row_ids
                                                       and e.id > after_id]),
            'war_room_trackers_db_events_not_in_timeline_counts': MagicMock(
                side_effect=lambda ids: {4: sum(not e.in_timeline for e in self.events)} if ids else {}),
            'war_room_trackers_db_row_counts': MagicMock(return_value={}),
        }
        self.biz.update(backfill)
        for name, mock in backfill.items():
            patcher = patch(f'{_BIZ}.{name}', mock)
            patcher.start()
            self._patchers.append(patcher)

    def backfill(self, sync=None, source='manual'):
        return trackers.war_room_trackers_timeline_sync(
            7, _tracker(source=source, timeline_sync=sync or _sync(target='war_room')),
            trackers.WarRoomTrackerAccess([1, 2], writable_case_ids=[1, 2]), 3)

    def test_writes_one_event_per_past_update_dated_at_it(self):
        self.events = [_event(1, 10, 'created', {'label': 'Call CERT'}),
                       _event(2, 10, 'step_added', {'step_id': 1, 'step_name': 'Isolated'}),
                       _event(3, 10, 'note_changed', {'old': None, 'new': 'done'}, minute=5)]
        summary = self.backfill()
        first, second = self.added_of(WarRoomTimelineEvent)
        self.assertEqual(first.title, 'Remediation · Call CERT: added to the tracker; Isolated set')
        self.assertEqual(first.event_date, datetime.datetime(2026, 10, 1, 9, 0))
        self.assertEqual((second.title, second.event_date),
                         ('Remediation · Call CERT: note updated', datetime.datetime(2026, 10, 1, 9, 5)))
        self.assertTrue(all(e.in_timeline for e in self.events))
        self.assertEqual(summary, {'events': 2, 'skipped_case_ids': [], 'restricted_rows': 0, 'war_room_rows': 0,
                                   'remaining': 0, 'remaining_restricted': 0})
        self.biz['war_room_trackers_db_commit'].assert_called_once()

    def test_the_date_column_is_taken_as_it_was_then(self):
        self.events = [_event(1, 10, 'created', {'label': 'Call CERT'})]
        self.later = [_event(2, 10, 'value_changed', {'column_key': 'c_due', 'old': '2026-10-02',
                                                      'new': '2026-10-20'}, minute=9)]
        self.backfill(_sync(target='war_room', date='c_due'))
        event, = self.added_of(WarRoomTimelineEvent)
        self.assertEqual(event.event_date, datetime.datetime(2026, 10, 2))

    def test_the_values_before_later_changes(self):
        later = [_event(2, 10, 'value_changed', {'column_key': 'a', 'old': None, 'new': 'x'}),
                 _event(3, 10, 'value_changed', {'column_key': 'b', 'old': 'w', 'new': 'y'}),
                 _event(4, 10, 'value_changed', {'column_key': 'b', 'old': 'y', 'new': 'z'})]
        self.assertEqual(trackers.war_room_trackers_values_before({'a': 'x', 'b': 'z', 'c': 1}, later),
                         {'b': 'w', 'c': 1})

    def test_a_cut_batch_leaves_its_last_update_for_the_next_call(self):
        self.events = [_event(1, 10, 'created', {'label': 'Call CERT'}),
                       _event(2, 10, 'note_changed', {'old': None, 'new': 'a'}, minute=1),
                       _event(3, 10, 'note_changed', {'old': 'a', 'new': 'b'}, minute=2)]
        with patch(f'{_BIZ}.WAR_ROOM_TRACKERS_TIMELINE_BACKFILL_LIMIT', 2):
            summary = self.backfill()
        self.assertEqual(len(self.added_of(WarRoomTimelineEvent)), 1)
        self.assertEqual([e.in_timeline for e in self.events], [True, False, False])
        self.assertEqual(summary['remaining'], 2)

    def test_past_changes_name_their_author_not_the_caller(self):
        self.events = [_event(1, 10, 'created', {'label': 'Call CERT'}, user_id=6)]
        self.backfill()
        event, = self.added_of(WarRoomTimelineEvent)
        self.assertEqual(event.created_by_id, 3)
        self.assertIn('_Changed by User 6_', event.content)

    def test_changes_of_rows_out_of_sight_stay_pending_for_others(self):
        # A free-text row, a stored row of an asset the caller cannot see,
        # and a mirrored item gone from the war room.
        self.rows.append(SimpleNamespace(id=11, tracker_id=4, item_type='asset', item_id=99, label=None, note=None,
                                         values={}))
        self.events = [_event(1, 10, 'note_changed', {'old': None, 'new': 'x'}),
                       _event(2, 11, 'note_changed', {'old': None, 'new': 'y'}),
                       _event(3, 12, 'note_changed', {'old': None, 'new': 'z'})]
        summary = self.backfill()
        self.assertEqual(len(self.added_of(WarRoomTimelineEvent)), 1)
        self.assertEqual([e.in_timeline for e in self.events], [True, False, False])
        self.assertEqual((summary['remaining'], summary['remaining_restricted'], summary['restricted_rows']),
                         (0, 2, 0))
        self.assertEqual(self.biz['war_room_trackers_db_events_not_in_timeline'].call_args.args[1], [10])

    def test_the_backfill_badge_counts_the_rows_the_caller_sees(self):
        self.rows.append(SimpleNamespace(id=11, tracker_id=4, item_type='asset', item_id=99, label=None, note=None,
                                         values={}))
        self.events = [_event(1, 10, 'note_changed', {'old': None, 'new': 'x'}),
                       _event(2, 11, 'note_changed', {'old': None, 'new': 'y'})]
        access = trackers.WarRoomTrackerAccess([1, 2], writable_case_ids=[1, 2])
        out, = trackers.war_room_trackers_serialize_many(7, [_tracker(source='manual')], access)
        self.assertEqual(out['timeline_backfill'], 1)
        self.events = [_event(2, 11, 'note_changed', {'old': None, 'new': 'y'})]
        out, = trackers.war_room_trackers_serialize_many(7, [_tracker(source='manual')], access)
        self.assertEqual(out['timeline_backfill'], 0)

    def test_a_mirrored_row_goes_to_its_case(self):
        self.biz['war_room_trackers_db_rows'].return_value = [
            SimpleNamespace(id=10, tracker_id=4, item_type='asset', item_id=1, label=None, note=None, values={})]
        self.events = [_event(1, 10, 'step_added', {'step_id': 1, 'step_name': 'Isolated'})]
        self.backfill(_sync(), source='assets')
        event, = self.added_of(CasesEvent)
        self.assertEqual((event.case_id, event.event_title, event.event_date),
                         (1, 'Remediation · dc01: Isolated set', datetime.datetime(2026, 10, 1, 9, 0)))

    def test_nothing_left(self):
        self.assertEqual(self.backfill(), {'events': 0, 'skipped_case_ids': [], 'restricted_rows': 0,
                                           'war_room_rows': 0, 'remaining': 0, 'remaining_restricted': 0})


class TestItemLinks(TestCase):

    def test_iocs_link_each_case_to_its_own_iocs(self):
        items = trackers.war_room_trackers_group_iocs([
            {'group_key': 'k', 'ioc_id': 12, 'ioc_value': 'evil.com', 'case_id': 1, 'case_name': 'A'},
            {'group_key': 'k', 'ioc_id': 11, 'ioc_value': 'evil.com', 'case_id': 2, 'case_name': 'B'},
            {'group_key': 'k', 'ioc_id': 13, 'ioc_value': 'evil.com', 'case_id': 1, 'case_name': 'A'},
        ])
        self.assertEqual(items[0]['timeline']['case_links'], [(1, (), (12, 13)), (2, (), (11,))])
        self.assertEqual(items[0]['timeline']['visibility_case_ids'], [1, 2])
        self.assertNotIn('case_iocs', items[0]['item'])
