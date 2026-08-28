#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in app.iris_engine.utils.common.

`parse_bf_date_format` and `IrisJinjaEnv.is_safe_attribute` are tested
here — no DB, no Flask context needed.
"""

import datetime
from unittest import TestCase

from app.iris_engine.utils.common import IrisJinjaEnv, parse_bf_date_format


class TestParseBfDateFormat(TestCase):

    # --- Unix timestamp (10 digits) ---

    def test_unix_timestamp_10_digits(self):
        result = parse_bf_date_format('1700000000')
        self.assertIsInstance(result, datetime.datetime)

    def test_unix_timestamp_year_round_trips(self):
        result = parse_bf_date_format('1700000000')
        self.assertEqual(2023, result.year)

    # --- Millisecond timestamp (13 digits) ---

    def test_millisecond_timestamp_13_digits(self):
        result = parse_bf_date_format('1700000000000')
        self.assertIsInstance(result, datetime.datetime)

    def test_millisecond_timestamp_year(self):
        result = parse_bf_date_format('1700000000000')
        self.assertEqual(2023, result.year)

    # --- ISO date only ---

    def test_iso_date_only(self):
        result = parse_bf_date_format('2024-03-15')
        self.assertEqual(datetime.datetime(2024, 3, 15), result)

    # --- ISO datetime (with time) ---

    def test_iso_datetime_no_seconds(self):
        result = parse_bf_date_format('2024-03-15 10:30')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30), result)

    def test_iso_datetime_with_seconds(self):
        result = parse_bf_date_format('2024-03-15 10:30:45')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30, 45), result)

    def test_iso_datetime_with_microseconds(self):
        result = parse_bf_date_format('2024-03-15 10:30:45.123456')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30, 45, 123456), result)

    # --- ISO-T format ---

    def test_iso_t_datetime(self):
        result = parse_bf_date_format('2024-03-15T10:30:45')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30, 45), result)

    def test_iso_t_datetime_no_seconds(self):
        result = parse_bf_date_format('2024-03-15T10:30')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30), result)

    # --- Slash-separated date ---

    def test_slash_date_with_time(self):
        result = parse_bf_date_format('15/03/2024 10:30')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30), result)

    def test_slash_date_with_seconds(self):
        result = parse_bf_date_format('15/03/2024 10:30:00')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30, 0), result)

    # --- Dot-separated date ---

    def test_dot_date_with_time(self):
        result = parse_bf_date_format('15.03.2024 10:30')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30), result)

    # --- Abbreviated month name ---

    def test_abbreviated_month_name_format(self):
        result = parse_bf_date_format('15 Mar 2024 10:30:00')
        self.assertEqual(datetime.datetime(2024, 3, 15, 10, 30, 0), result)

    # --- Full month name ---

    def test_full_date_long_month_name(self):
        result = parse_bf_date_format('15 March 2024')
        self.assertIsInstance(result, datetime.datetime)
        self.assertEqual(2024, result.year)
        self.assertEqual(3, result.month)
        self.assertEqual(15, result.day)

    # --- Leading/trailing whitespace ---

    def test_whitespace_is_stripped(self):
        result = parse_bf_date_format('  2024-03-15  ')
        self.assertIsInstance(result, datetime.datetime)

    # --- Unrecognised format returns None ---

    def test_garbage_string_returns_none(self):
        self.assertIsNone(parse_bf_date_format('not-a-date'))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_bf_date_format(''))

    def test_only_whitespace_returns_none(self):
        self.assertIsNone(parse_bf_date_format('   '))


class TestIrisJinjaEnvIsSafeAttribute(TestCase):

    def setUp(self):
        self._env = IrisJinjaEnv()

    def test_safe_regular_attribute_is_allowed(self):
        self.assertTrue(self._env.is_safe_attribute(object(), 'title', 'some value'))

    def test_os_attribute_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), 'os', None))

    def test_subprocess_attribute_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), 'subprocess', None))

    def test_eval_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), 'eval', None))

    def test_exec_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), 'exec', None))

    def test_open_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), 'open', None))

    def test_dunder_class_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), '__class__', None))

    def test_dunder_globals_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), '__globals__', None))

    def test_arbitrary_dunder_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), '__any_magic__', None))

    def test_import_is_blocked(self):
        self.assertFalse(self._env.is_safe_attribute(object(), '__import__', None))
