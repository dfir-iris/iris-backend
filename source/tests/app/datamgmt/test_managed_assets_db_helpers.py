#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in datamgmt/manage/manage_managed_assets_db.py.

_escape_like, _min_datetime, _max_datetime, and _jsonable are all pure
functions — no DB or Flask context needed.
"""

import datetime
from unittest import TestCase

from app.datamgmt.manage.manage_managed_assets_db import (
    _escape_like,
    _jsonable,
    _max_datetime,
    _min_datetime,
)


class TestEscapeLike(TestCase):

    def test_plain_string_unchanged(self):
        self.assertEqual('hello', _escape_like('hello'))

    def test_percent_escaped(self):
        self.assertEqual('100\\%', _escape_like('100%'))

    def test_underscore_escaped(self):
        self.assertEqual('a\\_b', _escape_like('a_b'))

    def test_backslash_escaped(self):
        self.assertEqual('path\\\\file', _escape_like('path\\file'))

    def test_all_metacharacters_escaped(self):
        result = _escape_like('%_\\')
        self.assertEqual('\\%\\_\\\\', result)

    def test_empty_string_unchanged(self):
        self.assertEqual('', _escape_like(''))

    def test_multiple_percent_signs(self):
        result = _escape_like('100% done 50%')
        self.assertNotIn('%', result.replace('\\%', ''))

    def test_unicode_preserved(self):
        result = _escape_like('café_test')
        self.assertIn('café', result)


class TestMinDatetime(TestCase):

    _D1 = datetime.datetime(2024, 1, 1)
    _D2 = datetime.datetime(2024, 6, 1)

    def test_left_none_returns_right(self):
        self.assertEqual(self._D2, _min_datetime(None, self._D2))

    def test_right_none_returns_left(self):
        self.assertEqual(self._D1, _min_datetime(self._D1, None))

    def test_both_none_returns_none(self):
        self.assertIsNone(_min_datetime(None, None))

    def test_returns_earlier(self):
        self.assertEqual(self._D1, _min_datetime(self._D1, self._D2))

    def test_returns_earlier_reversed(self):
        self.assertEqual(self._D1, _min_datetime(self._D2, self._D1))

    def test_equal_datetimes_returns_same(self):
        self.assertEqual(self._D1, _min_datetime(self._D1, self._D1))


class TestMaxDatetime(TestCase):

    _D1 = datetime.datetime(2024, 1, 1)
    _D2 = datetime.datetime(2024, 6, 1)

    def test_left_none_returns_right(self):
        self.assertEqual(self._D2, _max_datetime(None, self._D2))

    def test_right_none_returns_left(self):
        self.assertEqual(self._D1, _max_datetime(self._D1, None))

    def test_both_none_returns_none(self):
        self.assertIsNone(_max_datetime(None, None))

    def test_returns_later(self):
        self.assertEqual(self._D2, _max_datetime(self._D1, self._D2))

    def test_returns_later_reversed(self):
        self.assertEqual(self._D2, _max_datetime(self._D2, self._D1))

    def test_equal_datetimes_returns_same(self):
        self.assertEqual(self._D1, _max_datetime(self._D1, self._D1))


class TestJsonable(TestCase):

    def test_none_stays_none(self):
        self.assertIsNone(_jsonable(None))

    def test_string_unchanged(self):
        self.assertEqual('hello', _jsonable('hello'))

    def test_int_unchanged(self):
        self.assertEqual(42, _jsonable(42))

    def test_float_unchanged(self):
        self.assertAlmostEqual(3.14, _jsonable(3.14))

    def test_bool_unchanged(self):
        self.assertTrue(_jsonable(True))

    def test_dict_unchanged(self):
        d = {'a': 1}
        self.assertEqual(d, _jsonable(d))

    def test_list_unchanged(self):
        lst = [1, 2, 3]
        self.assertEqual(lst, _jsonable(lst))

    def test_datetime_converted_to_string(self):
        dt = datetime.datetime(2024, 1, 1)
        result = _jsonable(dt)
        self.assertIsInstance(result, str)
        self.assertIn('2024', result)

    def test_arbitrary_object_converted_to_string(self):
        class Foo:
            def __str__(self):
                return 'foo_repr'
        self.assertEqual('foo_repr', _jsonable(Foo()))
