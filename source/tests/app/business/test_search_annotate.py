#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for _annotate in business/search.py.

Pure function — mutates row dicts in-place, no DB needed.
"""

from unittest import TestCase

from app.business.search import _annotate, SUPPORTED_SEARCH_TYPES


class TestAnnotate(TestCase):

    def test_type_field_added(self):
        rows = [{'ioc_id': 1, 'ioc_value': 'evil.com'}]
        result = _annotate(rows, 'ioc')
        self.assertEqual('ioc', result[0]['type'])

    def test_result_id_mapped_from_ioc_id(self):
        rows = [{'ioc_id': 42}]
        _annotate(rows, 'ioc')
        self.assertEqual(42, rows[0]['result_id'])

    def test_result_id_mapped_from_note_id(self):
        rows = [{'note_id': 7}]
        _annotate(rows, 'notes')
        self.assertEqual(7, rows[0]['result_id'])

    def test_result_id_mapped_from_comment_id(self):
        rows = [{'comment_id': 3}]
        _annotate(rows, 'comments')
        self.assertEqual(3, rows[0]['result_id'])

    def test_result_id_mapped_from_asset_id(self):
        rows = [{'asset_id': 5}]
        _annotate(rows, 'assets')
        self.assertEqual(5, rows[0]['result_id'])

    def test_result_id_mapped_from_event_id(self):
        rows = [{'event_id': 10}]
        _annotate(rows, 'events')
        self.assertEqual(10, rows[0]['result_id'])

    def test_result_id_mapped_from_task_id(self):
        rows = [{'task_id': 99}]
        _annotate(rows, 'tasks')
        self.assertEqual(99, rows[0]['result_id'])

    def test_result_id_mapped_from_evidence_id(self):
        rows = [{'evidence_id': 8}]
        _annotate(rows, 'evidences')
        self.assertEqual(8, rows[0]['result_id'])

    def test_result_id_mapped_from_case_id_for_summaries(self):
        rows = [{'case_id': 55}]
        _annotate(rows, 'summaries')
        self.assertEqual(55, rows[0]['result_id'])

    def test_missing_id_field_gives_none_result_id(self):
        rows = [{}]
        _annotate(rows, 'ioc')
        self.assertIsNone(rows[0]['result_id'])

    def test_multiple_rows_all_annotated(self):
        rows = [{'ioc_id': 1}, {'ioc_id': 2}]
        result = _annotate(rows, 'ioc')
        self.assertEqual(2, len(result))
        for row in result:
            self.assertEqual('ioc', row['type'])

    def test_empty_rows_returns_empty(self):
        result = _annotate([], 'ioc')
        self.assertEqual([], result)

    def test_returns_same_list(self):
        rows = [{'ioc_id': 1}]
        result = _annotate(rows, 'ioc')
        self.assertIs(rows, result)

    def test_supported_search_types_is_tuple(self):
        self.assertIsInstance(SUPPORTED_SEARCH_TYPES, tuple)

    def test_supported_search_types_contains_ioc(self):
        self.assertIn('ioc', SUPPORTED_SEARCH_TYPES)

    def test_supported_search_types_contains_all_known(self):
        expected = {'ioc', 'notes', 'comments', 'assets', 'events', 'tasks', 'evidences', 'summaries'}
        self.assertEqual(expected, set(SUPPORTED_SEARCH_TYPES))
