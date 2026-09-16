#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helper functions in business/asynchronous_tasks.py.

No DB, no Celery, no Flask context needed.
"""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

import datetime
import json
import pickle

from app.business.asynchronous_tasks import (
    _get_engine_name,
    _get_success,
    _project_row,
    dim_tasks_get,
)
from iris_interface.IrisInterfaceStatus import IIStatus


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


_DETONATIONS = []


def _detonate():
    """Stand-in for an attacker's payload (``os.system`` in the real thing).

    Records the call instead of doing damage so the test can assert on it.
    """
    _DETONATIONS.append(True)
    return 'detonated'


class _MaliciousResult:
    """Pickles to a reduce-instruction pointing at :func:`_detonate`."""

    def __reduce__(self):
        return (_detonate, ())


class _ForeignPayload:
    """Stand-in for the SQLAlchemy ORM instances real modules hand back.

    ``task_hook_wrapper`` returns whatever the module returned, and the
    bundled modules put the merged ORM objects straight into
    ``IIStatus.data`` — so the result blob routinely references classes
    that have nothing to do with reading ``is_success()``.
    """

    def __init__(self, value):
        self.value = value


class TestProjectRowPickleSafety(TestCase):

    def setUp(self):
        _DETONATIONS.clear()

    def test_malicious_result_pickle_does_not_execute_code(self):
        row = _make_row(result=pickle.dumps(_MaliciousResult()))

        _project_row(row)

        self.assertEqual([], _DETONATIONS)

    def test_iistatus_carrying_foreign_objects_still_reports_success(self):
        status = IIStatus(code=0x1, message='ok', data=[_ForeignPayload(1)], logs=['a'])
        row = _make_row(result=pickle.dumps(status), status='SUCCESS')

        self.assertEqual('success', _project_row(row)['state'])

    def test_iistatus_failure_code_falls_back_to_row_status(self):
        status = IIStatus(code=0xFF01, message='boom', logs=['x'])
        row = _make_row(result=pickle.dumps(status), status='FAILURE')

        self.assertEqual('FAILURE', _project_row(row)['state'])


# Protocol 2, assembled by hand: GLOBAL <gone module>.IIStatus, EMPTY_TUPLE,
# NEWOBJ, STOP. Hand-written because the whole point is to name a class that
# cannot be imported, and `pickle.dumps` can only reference real ones. This
# is what a task stored by an IRIS version that predates the current
# `iris_interface` package looks like on disk.
_LEGACY_IISTATUS_BLOB = b'\x80\x02capp.iris_engine.module_handler\nIIStatus\n)\x81.'


def _patch_lookup(row):
    return patch('app.business.asynchronous_tasks.get_asynchronous_task_by_id', return_value=row)


class TestDimTasksGet(TestCase):

    def setUp(self):
        _DETONATIONS.clear()

    def test_malicious_result_pickle_does_not_execute_code(self):
        row = _make_row(result=pickle.dumps(_MaliciousResult()))

        with _patch_lookup(row):
            dim_tasks_get('abc-123')

        self.assertEqual([], _DETONATIONS)

    def test_iistatus_result_reports_success_and_logs(self):
        status = IIStatus(code=0x1, message='ok', logs=['line one'])
        row = _make_row(result=pickle.dumps(status))

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('Success', details['Success'])
        self.assertEqual(['line one'], details['Logs'])

    def test_failing_iistatus_reports_failure(self):
        status = IIStatus(code=0xFF01, message='boom', logs=['bad'])
        row = _make_row(result=pickle.dumps(status), status='FAILURE')

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('Failure', details['Success'])

    def test_iistatus_carrying_foreign_objects_still_reports_success(self):
        status = IIStatus(code=0x1, message='ok', data=[_ForeignPayload(1)], logs=['a'])
        row = _make_row(result=pickle.dumps(status))

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('Success', details['Success'])

    def test_hook_wrapper_kwargs_populate_module_hook_user_and_case(self):
        kwargs = json.dumps({
            'module_name': 'IrisVT',
            'hook_name': 'on_postload_ioc_create',
            'init_user': 'alice',
            'caseid': 7,
        }).encode('utf-8')
        row = _make_row(
            name='app.iris_engine.module_handler.task_hook_wrapper',
            kwargs=kwargs,
            result=pickle.dumps(IIStatus(code=0x1, message='ok', logs=['done'])),
        )

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('IrisVT', details['Module name'])
        self.assertEqual('on_postload_ioc_create', details['Hook name'])
        self.assertEqual('alice', details['User'])
        self.assertEqual(7, details['Case ID'])

    def test_hook_wrapper_without_a_readable_status_falls_back_to_shadow_iris(self):
        # The kwargs name a user, but a task whose result is not an
        # IIStatus is attributed to Shadow Iris regardless — the detail
        # view has always overwritten the user in that branch.
        kwargs = json.dumps({'module_name': 'IrisVT', 'init_user': 'alice'}).encode('utf-8')
        row = _make_row(name='app.iris_engine.module_handler.task_hook_wrapper', kwargs=kwargs)

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('IrisVT', details['Module name'])
        self.assertEqual('Shadow Iris', details['User'])

    def test_non_hook_task_leaves_module_and_hook_unset(self):
        row = _make_row(name='app.tasks.some_task')

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertIsNone(details['Module name'])
        self.assertIsNone(details['Hook name'])

    def test_unknown_task_reports_pending(self):
        with _patch_lookup(None):
            details = dim_tasks_get('no-such-task')

        self.assertEqual('pending', details['Task state'])

    def test_row_fields_are_surfaced_verbatim(self):
        date_done = datetime.datetime(2026, 1, 15, 10, 30, 0)
        row = _make_row(date_done=date_done, traceback='Traceback: boom', name='app.tasks.x')

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('abc-123', details['Task ID'])
        self.assertEqual(date_done, details['Task finished on'])
        self.assertEqual('Traceback: boom', details['Traceback'])
        self.assertEqual('app.tasks.x', details['Engine'])
        self.assertEqual('success', details['Task state'])

    def test_task_without_name_reports_shadow_failure_engine(self):
        row = _make_row(name=None)

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertIn('No engine', details['Engine'])

    def test_result_that_is_not_an_iistatus_reports_shadow_iris(self):
        row = _make_row(result=None)

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertEqual('Failure', details['Success'])
        self.assertEqual('Shadow Iris', details['User'])

    def test_legacy_pickled_status_reports_legacy_message(self):
        row = _make_row(result=_LEGACY_IISTATUS_BLOB)

        with _patch_lookup(row):
            details = dim_tasks_get('abc-123')

        self.assertIn('Danger', details)
