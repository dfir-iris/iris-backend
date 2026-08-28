#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Extended unit tests for the REST parsing helpers.

All helpers tested here are pure Python — no DB, no request context.
"""

from unittest import TestCase

from app.blueprints.rest.parsing import (
    parse_boolean,
    parse_comma_separated_identifiers,
)


class TestParseCommaSeparatedIdentifiers(TestCase):

    def test_two_integers(self):
        self.assertEqual([1, 2], parse_comma_separated_identifiers('1,2'))

    def test_single_integer(self):
        self.assertEqual([42], parse_comma_separated_identifiers('42'))

    def test_three_integers(self):
        self.assertEqual([10, 20, 30], parse_comma_separated_identifiers('10,20,30'))

    def test_negative_integer_parsed(self):
        self.assertEqual([-1], parse_comma_separated_identifiers('-1'))

    def test_whitespace_in_value_raises(self):
        # split(',') on '1, 2' produces ['1', ' 2']; int(' 2') succeeds
        # (int() strips whitespace), so this is actually fine — document it.
        result = parse_comma_separated_identifiers('1, 2')
        self.assertEqual([1, 2], result)

    def test_non_numeric_value_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_comma_separated_identifiers('a,b')

    def test_empty_string_raises(self):
        # int('') raises ValueError
        with self.assertRaises(ValueError):
            parse_comma_separated_identifiers('')

    def test_trailing_comma_raises(self):
        # split(',') produces ['1', ''] — int('') raises
        with self.assertRaises(ValueError):
            parse_comma_separated_identifiers('1,')


class TestParseBoolean(TestCase):

    def test_true_string_returns_true(self):
        self.assertIs(True, parse_boolean('true'))

    def test_false_string_returns_false(self):
        self.assertIs(False, parse_boolean('false'))

    def test_mixed_case_raises(self):
        with self.assertRaises(ValueError):
            parse_boolean('True')

    def test_uppercase_raises(self):
        with self.assertRaises(ValueError):
            parse_boolean('FALSE')

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_boolean('')

    def test_numeric_raises(self):
        with self.assertRaises(ValueError):
            parse_boolean('1')

    def test_none_raises(self):
        with self.assertRaises((ValueError, AttributeError)):
            parse_boolean(None)
