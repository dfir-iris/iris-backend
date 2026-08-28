#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helper functions in datamgmt/alerts/alerts_db.py."""

import datetime
from unittest import TestCase

from app.datamgmt.alerts.alerts_db import _parse_inclusive_date_range


class TestParseInclusiveDateRange(TestCase):

    def test_none_start_returns_none_none(self):
        start, end = _parse_inclusive_date_range(None, '2026-01-15')
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_none_end_returns_none_none(self):
        start, end = _parse_inclusive_date_range('2026-01-01', None)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_both_none_returns_none_none(self):
        start, end = _parse_inclusive_date_range(None, None)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_invalid_start_returns_none_none(self):
        start, end = _parse_inclusive_date_range('not-a-date', '2026-01-15')
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_invalid_end_returns_none_none(self):
        start, end = _parse_inclusive_date_range('2026-01-01', 'garbage')
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_date_only_end_extended_to_end_of_day(self):
        _, end = _parse_inclusive_date_range('2026-01-01', '2026-01-15')
        self.assertIsNotNone(end)
        self.assertEqual(23, end.hour)
        self.assertEqual(59, end.minute)
        self.assertEqual(59, end.second)
        self.assertEqual(999999, end.microsecond)

    def test_datetime_end_not_extended(self):
        _, end = _parse_inclusive_date_range('2026-01-01T00:00:00', '2026-01-15T12:30:00')
        self.assertIsNotNone(end)
        self.assertEqual(12, end.hour)
        self.assertEqual(30, end.minute)
        self.assertEqual(0, end.second)

    def test_valid_dates_returned_as_datetimes(self):
        start, end = _parse_inclusive_date_range('2026-01-01', '2026-03-31')
        self.assertIsInstance(start, datetime.datetime)
        self.assertIsInstance(end, datetime.datetime)

    def test_start_date_not_modified(self):
        start, _ = _parse_inclusive_date_range('2026-01-01', '2026-01-15')
        self.assertEqual(2026, start.year)
        self.assertEqual(1, start.month)
        self.assertEqual(1, start.day)

    def test_end_date_value_correct(self):
        _, end = _parse_inclusive_date_range('2026-01-01', '2026-03-15')
        self.assertEqual(2026, end.year)
        self.assertEqual(3, end.month)
        self.assertEqual(15, end.day)
