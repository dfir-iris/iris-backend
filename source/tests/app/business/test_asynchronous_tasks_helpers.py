#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helper functions in business/asynchronous_tasks.py.

No DB, no Celery, no Flask context needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock

import datetime
import json

from app.business.asynchronous_tasks import (
    _dim_tasks_is_legacy,
    _get_engine_name,
    _get_success,
    _project_row,
)


class TestGetEngineName(TestCase):

    def test_with_task_name_returns_name(self):
        task = MagicMock()
        task.name = 'my_engine'
        self.assertEqual('my_engine', _get_engine_name(task))

    def test_without_task_name_returns_fallback(self):
        task = MagicMock()
        task.name = None
        result = _get_engine_name(task)
        self.assertIn('No engine', result)

    def test_empty_name_returns_fallback(self):
        task = MagicMock()
        task.name = ''
        result = _get_engine_name(task)
        self.assertIn('No engine', result)


class TestGetSuccess(TestCase):

    def test_success_status_returns_success(self):
        task_result = MagicMock()
        task_result.is_success.return_value = True
        self.assertEqual('Success', _get_success(task_result))

    def test_failure_status_returns_failure(self):
        task_result = MagicMock()
        task_result.is_success.return_value = False
        self.assertEqual('Failure', _get_success(task_result))


class TestDimTasksIsLegacy(TestCase):

    def test_task_with_date_done_is_not_legacy(self):
        task = MagicMock(spec=['date_done'])
        task.date_done = '2026-01-01'
        self.assertFalse(_dim_tasks_is_legacy(task))

    def test_task_without_date_done_is_legacy(self):
        # Simulate a task object with no date_done attribute
        class OldTask:
            pass
        self.assertTrue(_dim_tasks_is_legacy(OldTask()))


def _make_row(**kwargs):
    """Build a minimal mock CeleryTaskMeta row."""
    defaults = dict(
        task_id='abc-123',
        status='SUCCESS',
        name='app.tasks.some_task',
        date_done=None,
        kwargs=b'{}',
        result=None,
        traceback=None,
    )
    defaults.update(kwargs)
    row = MagicMock()
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


class TestProjectRow(TestCase):

    def test_returns_dict_with_task_id(self):
        row = _make_row(task_id='xyz-789')
        result = _project_row(row)
        self.assertEqual('xyz-789', result['task_id'])

    def test_date_done_none_gives_none(self):
        row = _make_row(date_done=None)
        result = _project_row(row)
        self.assertIsNone(result['date_done'])

    def test_date_done_datetime_gives_isoformat(self):
        dt = datetime.datetime(2026, 1, 15, 10, 30, 0)
        row = _make_row(date_done=dt)
        result = _project_row(row)
        self.assertEqual(dt.isoformat(), result['date_done'])

    def test_no_result_attribute_returns_legacy_row(self):
        class LegacyRow:
            task_id = 'leg-1'
            status = 'SUCCESS'
            name = 'old_task'
            date_done = None
            kwargs = b'{}'
            # No `result` attribute
        result = _project_row(LegacyRow())
        self.assertEqual('leg-1', result['task_id'])

    def test_kwargs_with_user_and_case(self):
        kwargs = json.dumps({'init_user': 'alice', 'caseid': 7}).encode('utf-8')
        row = _make_row(kwargs=kwargs)
        result = _project_row(row)
        self.assertEqual('alice', result['user'])
        self.assertEqual(7, result['case_id'])
        self.assertIn('7', result['case'])

    def test_empty_kwargs_uses_shadow_user(self):
        row = _make_row(kwargs=b'{}')
        result = _project_row(row)
        self.assertEqual('Shadow Iris', result['user'])

    def test_invalid_kwargs_json_gracefully_handled(self):
        row = _make_row(kwargs=b'not-json')
        result = _project_row(row)
        self.assertIsNotNone(result)

    def test_corrupt_result_pickle_gives_none_success(self):
        row = _make_row(result=b'corrupt_bytes')
        result = _project_row(row)
        self.assertIsNotNone(result)
