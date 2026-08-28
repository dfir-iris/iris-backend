#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure helper functions in managed_assets_transfer/importer.py.

No DB, no Flask app context needed (except _max_rows/_max_bytes which are mocked).
"""

import json
from unittest import TestCase
from unittest.mock import patch

from app.business.managed_assets_transfer.importer import (
    _assert_json_depth,
    _clean,
    _parse_bool,
    _parse_csv,
    _parse_custom_attributes,
    _parse_json,
    _summarize,
)
from app.models.errors import BusinessProcessingError


# ──────────────────────────────────────────────────────────────────────────────
# _clean
# ──────────────────────────────────────────────────────────────────────────────

class TestClean(TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(_clean(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_clean(''))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(_clean('   '))

    def test_strips_whitespace(self):
        self.assertEqual('hello', _clean('  hello  '))

    def test_truncates_to_max_length(self):
        result = _clean('x' * 5000, max_length=100)
        self.assertEqual(100, len(result))

    def test_removes_nul_characters(self):
        result = _clean('foo\x00bar')
        self.assertNotIn('\x00', result)
        self.assertEqual('foobar', result)

    def test_integer_coerced_to_string(self):
        self.assertEqual('42', _clean(42))

    def test_nul_only_string_returns_none(self):
        self.assertIsNone(_clean('\x00'))


# ──────────────────────────────────────────────────────────────────────────────
# _parse_bool
# ──────────────────────────────────────────────────────────────────────────────

class TestParseBool(TestCase):

    def _errors(self):
        return []

    def test_none_returns_true(self):
        e = self._errors()
        self.assertTrue(_parse_bool(None, e))
        self.assertEqual([], e)

    def test_bool_true_returned_as_is(self):
        e = self._errors()
        self.assertTrue(_parse_bool(True, e))

    def test_bool_false_returned_as_is(self):
        e = self._errors()
        self.assertFalse(_parse_bool(False, e))

    def test_empty_string_defaults_true(self):
        e = self._errors()
        self.assertTrue(_parse_bool('', e))

    def test_true_string_accepted(self):
        for value in ('true', 'True', 'TRUE', 't', 'yes', 'y', '1'):
            with self.subTest(value=value):
                e = self._errors()
                self.assertTrue(_parse_bool(value, e))

    def test_false_string_accepted(self):
        for value in ('false', 'False', 'FALSE', 'f', 'no', 'n', '0'):
            with self.subTest(value=value):
                e = self._errors()
                self.assertFalse(_parse_bool(value, e))

    def test_invalid_appends_error_and_returns_true(self):
        e = self._errors()
        result = _parse_bool('maybe', e)
        self.assertTrue(result)
        self.assertEqual(1, len(e))
        self.assertIn('is_active', e[0])


# ──────────────────────────────────────────────────────────────────────────────
# _parse_custom_attributes
# ──────────────────────────────────────────────────────────────────────────────

class TestParseCustomAttributes(TestCase):

    def test_none_returns_none(self):
        e = []
        self.assertIsNone(_parse_custom_attributes(None, e))
        self.assertEqual([], e)

    def test_empty_string_returns_none(self):
        e = []
        self.assertIsNone(_parse_custom_attributes('', e))

    def test_dict_returned_as_is(self):
        e = []
        payload = {'key': 'value'}
        self.assertEqual(payload, _parse_custom_attributes(payload, e))

    def test_valid_json_string_parsed(self):
        e = []
        result = _parse_custom_attributes('{"a": 1}', e)
        self.assertEqual({'a': 1}, result)

    def test_invalid_json_appends_error(self):
        e = []
        result = _parse_custom_attributes('not json', e)
        self.assertIsNone(result)
        self.assertEqual(1, len(e))

    def test_json_list_is_not_object_appends_error(self):
        e = []
        result = _parse_custom_attributes('[1, 2]', e)
        self.assertIsNone(result)
        self.assertEqual(1, len(e))
        self.assertIn('object', e[0])


# ──────────────────────────────────────────────────────────────────────────────
# _assert_json_depth
# ──────────────────────────────────────────────────────────────────────────────

class TestAssertJsonDepth(TestCase):

    def test_flat_object_accepted(self):
        _assert_json_depth('{"a": 1}')

    def test_flat_array_accepted(self):
        _assert_json_depth('[1, 2, 3]')

    def test_nested_within_limit_accepted(self):
        nested = '{"a": {"b": {"c": {"d": 1}}}}'
        _assert_json_depth(nested)

    def test_too_deeply_nested_raises(self):
        too_deep = '{"a": {"b": {"c": {"d": {"e": 1}}}}}'
        with self.assertRaises(BusinessProcessingError):
            _assert_json_depth(too_deep)

    def test_strings_with_brackets_not_counted(self):
        # Brackets inside strings must not affect depth count
        _assert_json_depth('{"a": "{{{{{{{{{{{{{"}')

    def test_escaped_quote_does_not_break_string_tracking(self):
        _assert_json_depth(r'{"a": "he said \"hi\""}')


# ──────────────────────────────────────────────────────────────────────────────
# _parse_json (needs _max_rows mocked)
# ──────────────────────────────────────────────────────────────────────────────

class TestParseJson(TestCase):

    def _parse(self, data, max_rows=1000):
        with patch('app.business.managed_assets_transfer.importer._max_rows', return_value=max_rows):
            return _parse_json(data)

    def test_list_of_dicts_returned(self):
        result = self._parse('[{"name": "host1"}]')
        self.assertEqual([{'name': 'host1'}], result)

    def test_assets_key_unwrapped(self):
        result = self._parse('{"assets": [{"name": "srv"}]}')
        self.assertEqual([{'name': 'srv'}], result)

    def test_invalid_json_raises(self):
        with self.assertRaises(BusinessProcessingError):
            self._parse('not json at all')

    def test_non_list_value_raises(self):
        with self.assertRaises(BusinessProcessingError):
            self._parse('{"name": "host"}')

    def test_non_dict_row_raises(self):
        with self.assertRaises(BusinessProcessingError):
            self._parse('["not a dict"]')

    def test_too_many_rows_raises(self):
        rows = json.dumps([{'name': f'h{i}'} for i in range(5)])
        with self.assertRaises(BusinessProcessingError):
            self._parse(rows, max_rows=4)

    def test_too_deep_nesting_raises(self):
        too_deep = '{"assets": {"a": {"b": {"c": {"d": 1}}}}}'
        with self.assertRaises(BusinessProcessingError):
            self._parse(too_deep)


# ──────────────────────────────────────────────────────────────────────────────
# _summarize
# ──────────────────────────────────────────────────────────────────────────────

class TestSummarize(TestCase):

    def test_empty_returns_zeros(self):
        result = _summarize([])
        self.assertEqual(0, result.get('create', 0))
        self.assertEqual(0, result.get('update', 0))
        self.assertEqual(0, result.get('error', 0))

    def test_counts_create(self):
        entries = [{'action': 'create'}, {'action': 'create'}]
        result = _summarize(entries)
        self.assertEqual(2, result['create'])

    def test_counts_update(self):
        entries = [{'action': 'update'}]
        result = _summarize(entries)
        self.assertEqual(1, result['update'])

    def test_counts_error(self):
        entries = [{'action': 'error'}, {'action': 'error'}, {'action': 'error'}]
        result = _summarize(entries)
        self.assertEqual(3, result['error'])

    def test_mixed_counts(self):
        entries = [
            {'action': 'create'},
            {'action': 'update'},
            {'action': 'error'},
            {'action': 'create'},
        ]
        result = _summarize(entries)
        self.assertEqual(2, result['create'])
        self.assertEqual(1, result['update'])
        self.assertEqual(1, result['error'])


# ──────────────────────────────────────────────────────────────────────────────
# _parse_csv (needs _max_rows mocked)
# ──────────────────────────────────────────────────────────────────────────────

class TestParseCsv(TestCase):

    def _parse(self, payload, max_rows=1000):
        with patch('app.business.managed_assets_transfer.importer._max_rows', return_value=max_rows):
            return _parse_csv(payload)

    def test_basic_csv_parsed(self):
        result = self._parse('name,asset_type\nserver01,Linux Server')
        self.assertEqual(1, len(result))
        self.assertEqual('server01', result[0]['name'])

    def test_header_lowercased_and_stripped(self):
        result = self._parse('  Name  ,Asset_Type\nhost,Windows')
        self.assertIn('name', result[0])
        self.assertIn('asset_type', result[0])

    def test_empty_file_raises(self):
        with self.assertRaises(Exception):
            self._parse('')

    def test_missing_name_column_raises(self):
        with self.assertRaises(Exception):
            self._parse('asset_type,description\nLinux,desc')

    def test_blank_rows_skipped(self):
        result = self._parse('name,asset_type\nhost,Linux\n,\n   ,   ')
        self.assertEqual(1, len(result))

    def test_too_many_rows_raises(self):
        rows = 'name,asset_type\n' + '\n'.join(f'host{i},Linux' for i in range(5))
        with self.assertRaises(Exception):
            self._parse(rows, max_rows=4)

    def test_multiple_rows_parsed(self):
        payload = 'name,asset_type\nhost1,Linux\nhost2,Windows'
        result = self._parse(payload)
        self.assertEqual(2, len(result))
        self.assertEqual('host1', result[0]['name'])
        self.assertEqual('host2', result[1]['name'])
