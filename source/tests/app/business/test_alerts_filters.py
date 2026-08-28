#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helpers in business/alerts_filters.py."""

from unittest import TestCase
from unittest.mock import MagicMock

from app.business.alerts_filters import _filter_label


class TestFilterLabel(TestCase):

    def test_returns_filter_name_when_set(self):
        saved_filter = MagicMock()
        saved_filter.filter_name = 'My Filter'
        self.assertEqual('My Filter', _filter_label(saved_filter))

    def test_falls_back_to_id_when_name_is_none(self):
        saved_filter = MagicMock()
        saved_filter.filter_name = None
        saved_filter.id = 42
        result = _filter_label(saved_filter)
        self.assertIn('42', result)
        self.assertTrue(result.startswith('#'))

    def test_falls_back_to_id_when_name_is_empty_string(self):
        saved_filter = MagicMock()
        saved_filter.filter_name = ''
        saved_filter.id = 7
        result = _filter_label(saved_filter)
        self.assertIn('7', result)

    def test_missing_id_uses_question_mark(self):
        class MinimalFilter:
            pass
        result = _filter_label(MinimalFilter())
        self.assertIn('?', result)

    def test_missing_name_uses_question_mark_when_no_id(self):
        class MinimalFilter:
            pass
        result = _filter_label(MinimalFilter())
        self.assertTrue(result.startswith('#'))
