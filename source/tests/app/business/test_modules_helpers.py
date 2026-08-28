#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _module_row_projection in business/modules.py.

Pure dict transform — no DB, no Flask context needed.
"""

import datetime
from unittest import TestCase

from app.business.modules import _module_row_projection


def _row(**kwargs):
    defaults = {
        'id': 1,
        'module_human_name': 'Test Module',
        'has_pipeline': False,
        'module_version': '1.0.0',
        'interface_version': '2',
        'date_added': None,
        'name': 'Admin User',
        'is_active': True,
        'configured': True,
    }
    defaults.update(kwargs)
    return defaults


class TestModuleRowProjection(TestCase):

    def test_id_mapped(self):
        result = _module_row_projection(_row(id=7))
        self.assertEqual(7, result['id'])

    def test_module_human_name_mapped(self):
        result = _module_row_projection(_row(module_human_name='My Plugin'))
        self.assertEqual('My Plugin', result['module_human_name'])

    def test_has_pipeline_mapped(self):
        result = _module_row_projection(_row(has_pipeline=True))
        self.assertTrue(result['has_pipeline'])

    def test_module_version_mapped(self):
        result = _module_row_projection(_row(module_version='2.3.1'))
        self.assertEqual('2.3.1', result['module_version'])

    def test_interface_version_mapped(self):
        result = _module_row_projection(_row(interface_version='1'))
        self.assertEqual('1', result['interface_version'])

    def test_date_added_none_gives_none(self):
        result = _module_row_projection(_row(date_added=None))
        self.assertIsNone(result['date_added'])

    def test_date_added_datetime_gives_isoformat(self):
        dt = datetime.datetime(2026, 1, 15, 10, 30, 0)
        result = _module_row_projection(_row(date_added=dt))
        self.assertEqual(dt.isoformat(), result['date_added'])

    def test_added_by_comes_from_name(self):
        result = _module_row_projection(_row(name='Alice'))
        self.assertEqual('Alice', result['added_by'])

    def test_is_active_false(self):
        result = _module_row_projection(_row(is_active=False))
        self.assertFalse(result['is_active'])

    def test_configured_false(self):
        result = _module_row_projection(_row(configured=False))
        self.assertFalse(result['configured'])

    def test_result_is_dict(self):
        result = _module_row_projection(_row())
        self.assertIsInstance(result, dict)

    def test_expected_keys_present(self):
        result = _module_row_projection(_row())
        expected_keys = {
            'id', 'module_human_name', 'has_pipeline', 'module_version',
            'interface_version', 'date_added', 'added_by', 'is_active', 'configured'
        }
        self.assertEqual(expected_keys, set(result.keys()))
