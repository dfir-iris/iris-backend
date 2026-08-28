#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in case_timelines.py and war_room_datastore.py.

No DB, no Flask context needed.
"""

from unittest import TestCase

from app.business.case_timelines import (
    _validate_color,
    _validate_name,
)
from app.business.war_room_datastore import _sanitize_filename
from app.models.errors import BusinessProcessingError


class TestCaseTimelineValidateName(TestCase):

    def test_valid_name_returned_stripped(self):
        self.assertEqual('Main', _validate_name('  Main  '))

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name(0)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('')

    def test_whitespace_only_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('   ')

    def test_name_at_limit_accepted(self):
        result = _validate_name('a' * 128)
        self.assertEqual(128, len(result))

    def test_name_over_limit_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_name('a' * 129)


class TestCaseTimelineValidateColor(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_validate_color(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_validate_color(''))

    def test_valid_hex_accepted(self):
        self.assertEqual('#ff0000', _validate_color('#ff0000'))

    def test_mixed_case_hex_accepted(self):
        self.assertEqual('#aAbBcC', _validate_color('#aAbBcC'))

    def test_invalid_color_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color('blue')

    def test_non_string_non_none_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _validate_color(0xFFFFFF)


class TestSanitizeFilename(TestCase):

    def test_valid_filename_returned(self):
        self.assertEqual('report.pdf', _sanitize_filename('report.pdf'))

    def test_strips_leading_trailing_whitespace(self):
        self.assertEqual('file.txt', _sanitize_filename('  file.txt  '))

    def test_path_traversal_basename_extracted(self):
        result = _sanitize_filename('../etc/passwd')
        self.assertEqual('passwd', result)

    def test_non_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_filename(42)

    def test_empty_string_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_filename('')

    def test_whitespace_only_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_filename('   ')

    def test_dot_alone_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_filename('.')

    def test_double_dot_raises(self):
        with self.assertRaises(BusinessProcessingError):
            _sanitize_filename('..')

    def test_long_filename_truncated(self):
        long_name = 'a' * 600 + '.txt'
        result = _sanitize_filename(long_name)
        self.assertLessEqual(len(result), 512)
