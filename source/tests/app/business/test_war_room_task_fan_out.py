#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Unit tests for business/war_room_task_fan_out.py (no DB)."""

import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.business import war_room_task_fan_out as fan_out
from app.business.war_room_task_fan_out import _build_description
from app.business.war_room_task_fan_out import _build_tags
from app.business.war_room_task_fan_out import _resolve_default_status_id
from app.business.war_room_task_fan_out import war_room_task_fan_out_create
from app.business.war_room_task_fan_out import war_room_task_fan_out_is_done_status
from app.business.war_room_task_fan_out import war_room_task_fan_out_status
from app.business.war_room_task_fan_out import war_room_task_fan_out_summarize
from app.business.war_room_task_fan_out import war_room_task_fan_out_unlink
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_MODULE = 'app.business.war_room_task_fan_out'


def _task(task_id=7, title='Rotate krbtgt', description='Do it twice', tags='ad,AD,urgent'):
    return SimpleNamespace(task_id=task_id, title=title, description=description, tags=tags)


class TestDoneStatus(TestCase):

    def test_done_names(self):
        for name in ('Done', 'closed', 'CANCELED', ' Cancelled '):
            self.assertTrue(war_room_task_fan_out_is_done_status(name), name)

    def test_open_names(self):
        for name in ('To do', 'In progress', 'On hold', '', None, 3):
            self.assertFalse(war_room_task_fan_out_is_done_status(name), name)


class TestDefaultStatus(TestCase):

    def test_to_do_by_name_case_insensitive(self):
        self.assertEqual(4, _resolve_default_status_id([(1, 'In progress'), (4, 'TO DO')]))

    def test_falls_back_to_lowest_id(self):
        self.assertEqual(2, _resolve_default_status_id([(5, 'Open'), (2, 'Started')]))

    def test_empty(self):
        self.assertIsNone(_resolve_default_status_id([]))

    def test_unknown_status_id_rejected(self):
        with patch(f'{_MODULE}.fan_out_db_task_statuses', return_value=[(1, 'To do')]):
            with self.assertRaises(BusinessProcessingError):
                fan_out._resolve_status_id(99)
            self.assertEqual(1, fan_out._resolve_status_id(None))
            self.assertEqual(1, fan_out._resolve_status_id(1))


class TestTagsAndDescription(TestCase):

    def test_tags_start_with_war_room_and_dedup(self):
        self.assertEqual(['war-room', 'ad', 'urgent'], _build_tags('ad,AD, urgent,,war-room'))

    def test_tags_none(self):
        self.assertEqual(['war-room'], _build_tags(None))

    def test_description_back_reference(self):
        out = _build_description('Ransomware wave', _task())
        self.assertTrue(out.startswith('Do it twice'))
        self.assertIn('From war room Ransomware wave (task #7)', out)

    def test_description_only_back_reference(self):
        out = _build_description('WR', _task(description=None))
        self.assertEqual('From war room WR (task #7)', out)


class TestSummarize(TestCase):

    def test_counts_and_redaction(self):
        rows = [(1, 10, 'Done'), (1, 11, 'To do'), (1, 12, 'Closed'), (2, 12, 'Done')]
        summary = war_room_task_fan_out_summarize(rows, {10, 11})
        self.assertEqual({'total': 3, 'done': 1, 'accessible_total': 2}, summary['1'])
        # Case 12 is unreadable: its done status must not be counted.
        self.assertEqual({'total': 1, 'done': 0, 'accessible_total': 0}, summary['2'])


class TestFanOutCreate(TestCase):

    def _patches(self, attached, linked, owners, create_side_effect=None, link_result=True):
        created_ids = iter(range(500, 600))

        def _tasks_create(case_task, assignees):
            if create_side_effect:
                raise create_side_effect
            case_task.id = next(created_ids)
            case_task.assignees_passed = assignees
            self.created.append(case_task)
            return case_task

        self.created = []
        targets = {
            'war_room_task_get': MagicMock(return_value=_task()),
            'war_room_get': MagicMock(return_value=SimpleNamespace(name='WR')),
            'fan_out_db_task_statuses': MagicMock(return_value=[(1, 'To do'), (2, 'Done')]),
            'fan_out_db_attached_case_ids': MagicMock(return_value=set(attached)),
            'fan_out_db_linked_case_ids': MagicMock(return_value=set(linked)),
            'fan_out_db_case_owner_ids': MagicMock(return_value=owners),
            'fan_out_db_create_link': MagicMock(return_value=MagicMock() if link_result else None),
            'add_db_tag': MagicMock(),
            'tasks_create': MagicMock(side_effect=_tasks_create),
            'tasks_delete': MagicMock(),
            'track_activity': MagicMock(),
            'db': MagicMock(),
        }
        patchers = [patch(f'{_MODULE}.{name}', mock) for name, mock in targets.items()]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return targets

    def test_results_in_order_with_denied_exists_created(self):
        mocks = self._patches(attached={10, 11}, linked={11}, owners={10: 42})
        results = war_room_task_fan_out_create(1, 7, [10, 11, 99], user_id=3)
        self.assertEqual(['created', 'exists', 'denied'], [r['status'] for r in results])
        self.assertEqual(500, results[0]['case_task_id'])
        case_task = self.created[0]
        self.assertEqual('Rotate krbtgt', case_task.task_title)
        self.assertEqual('war-room,ad,urgent', case_task.task_tags)
        self.assertEqual(1, case_task.task_status_id)
        self.assertEqual(10, case_task.task_case_id)
        self.assertEqual([42], case_task.assignees_passed)
        self.assertIn('(task #7)', case_task.task_description)
        mocks['fan_out_db_create_link'].assert_called_once_with(7, 10, 500, 3)
        mocks['track_activity'].assert_called_once()

    def test_no_owner_assignment_when_flag_off(self):
        mocks = self._patches(attached={10}, linked=set(), owners={10: 42})
        war_room_task_fan_out_create(1, 7, [10], user_id=3, assign_to_case_owner=False)
        self.assertEqual([], self.created[0].assignees_passed)
        mocks['fan_out_db_case_owner_ids'].assert_not_called()

    def test_explicit_status(self):
        self._patches(attached={10}, linked=set(), owners={})
        war_room_task_fan_out_create(1, 7, [10], user_id=3, status_id=2)
        self.assertEqual(2, self.created[0].task_status_id)

    def test_business_error_reported_per_case(self):
        mocks = self._patches(attached={10}, linked=set(), owners={},
                              create_side_effect=BusinessProcessingError('boom'))
        results = war_room_task_fan_out_create(1, 7, [10], user_id=3)
        self.assertEqual([{'case_id': 10, 'status': 'error', 'message': 'boom'}], results)
        mocks['db'].session.rollback.assert_called()
        mocks['track_activity'].assert_not_called()

    def test_unexpected_error_does_not_leak_details(self):
        self._patches(attached={10}, linked=set(), owners={},
                      create_side_effect=RuntimeError('secret detail'))
        results = war_room_task_fan_out_create(1, 7, [10], user_id=3)
        self.assertEqual('error', results[0]['status'])
        self.assertNotIn('secret', results[0]['message'])

    def test_link_race_drops_duplicate_case_task(self):
        mocks = self._patches(attached={10}, linked=set(), owners={}, link_result=False)
        results = war_room_task_fan_out_create(1, 7, [10], user_id=3)
        self.assertEqual([{'case_id': 10, 'status': 'exists'}], results)
        mocks['tasks_delete'].assert_called_once_with(self.created[0])

    def test_unknown_task_raises_not_found(self):
        mocks = self._patches(attached={10}, linked=set(), owners={})
        mocks['war_room_task_get'].side_effect = ObjectNotFoundError()
        with self.assertRaises(ObjectNotFoundError):
            war_room_task_fan_out_create(1, 7, [10], user_id=3)


class TestFanOutStatus(TestCase):

    def test_unreadable_cases_are_redacted(self):
        now = datetime.datetime(2026, 10, 1, 12, 0)
        rows = [
            SimpleNamespace(case_id=10, case_task_id=500, created_at=now, case_name='Case A',
                            task_title='T', status_id=2, status_name='Done'),
            SimpleNamespace(case_id=11, case_task_id=501, created_at=now, case_name='Secret',
                            task_title='T', status_id=1, status_name='To do'),
        ]
        with patch(f'{_MODULE}.war_room_task_get'), \
                patch(f'{_MODULE}.fan_out_db_link_rows', return_value=rows), \
                patch(f'{_MODULE}.fan_out_db_assignees',
                      return_value={500: [{'id': 1, 'name': 'Ann'}]}) as assignees:
            out = war_room_task_fan_out_status(1, 7, {10})
        assignees.assert_called_once_with([500])
        self.assertTrue(out[0]['accessible'])
        self.assertTrue(out[0]['done'])
        self.assertEqual([{'id': 1, 'name': 'Ann'}], out[0]['assignees'])
        self.assertEqual({'case_id': 11, 'accessible': False, 'created_at': now.isoformat()}, out[1])


class TestUnlink(TestCase):

    def test_missing_link_raises_not_found(self):
        with patch(f'{_MODULE}.war_room_task_get', return_value=_task()), \
                patch(f'{_MODULE}.fan_out_db_delete_link', return_value=0), \
                patch(f'{_MODULE}.track_activity'):
            with self.assertRaises(ObjectNotFoundError):
                war_room_task_fan_out_unlink(1, 7, 10)

    def test_unlink(self):
        with patch(f'{_MODULE}.war_room_task_get', return_value=_task()), \
                patch(f'{_MODULE}.fan_out_db_delete_link', return_value=1) as delete, \
                patch(f'{_MODULE}.track_activity') as track:
            war_room_task_fan_out_unlink(1, 7, 10)
        delete.assert_called_once_with(7, 10)
        track.assert_called_once()
