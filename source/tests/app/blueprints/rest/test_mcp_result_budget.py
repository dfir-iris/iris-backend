#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the MCP tool-result budget guard.

`apply_result_budget` is pure — it takes a JSON-serialisable value and
returns a smaller one — so none of this needs Flask or a DB.
"""

import json
from unittest import TestCase

from app.blueprints.rest.v2.mcp.result_budget import apply_result_budget


def _size(value):
    return len(json.dumps(value, default=str))


def _rows(count, filler=200):
    return [{'id': i, 'blob': 'x' * filler} for i in range(count)]


class TestResultBudgetPassthrough(TestCase):

    def test_small_result_returned_untouched(self):
        result = {'data': _rows(2), 'total': 2}
        out, report = apply_result_budget(result, max_bytes=100_000)
        self.assertIsNone(report)
        self.assertEqual(result, out)

    def test_small_result_carries_no_truncation_key(self):
        out, _ = apply_result_budget({'data': _rows(1)}, max_bytes=100_000)
        self.assertNotIn('_truncated', out)


class TestResultBudgetRowDropping(TestCase):

    def test_oversized_page_is_shrunk_within_budget(self):
        result = {'data': _rows(100), 'total': 100, 'current_page': 1}
        out, report = apply_result_budget(result, max_bytes=5_000)
        self.assertIsNotNone(report)
        self.assertLessEqual(_size(out), 5_000)

    def test_dropped_rows_are_reported_with_the_true_total(self):
        result = {'data': _rows(100), 'total': 100}
        out, _ = apply_result_budget(result, max_bytes=5_000)
        truncated = out['_truncated']
        self.assertEqual(100, truncated['rows_total'])
        self.assertLess(truncated['rows_shown'], 100)
        self.assertEqual(truncated['rows_shown'], len(out['data']))

    def test_pagination_envelope_survives_truncation(self):
        result = {'data': _rows(100), 'total': 100, 'current_page': 1,
                  'last_page': 4, 'next_page': 2}
        out, _ = apply_result_budget(result, max_bytes=5_000)
        # The caller must still be able to page: dropping rows should not
        # look like the end of the result set.
        self.assertEqual(100, out['total'])
        self.assertEqual(2, out['next_page'])

    def test_at_least_one_row_is_kept(self):
        # An empty list reads as "nothing matched" — a different and far
        # more misleading answer than "here is the first of many".
        result = {'data': _rows(20, filler=9_000)}
        out, _ = apply_result_budget(result, max_bytes=2_000)
        self.assertEqual(1, len(out['data']))

    def test_single_oversized_row_is_clipped_rather_than_overflowing(self):
        # Dropping rows bottoms out at one; the remaining row still has
        # to be brought inside the ceiling by clipping its text.
        result = {'data': _rows(20, filler=9_000)}
        out, _ = apply_result_budget(result, max_bytes=2_000)
        self.assertEqual(1, len(out['data']))
        self.assertLessEqual(_size(out), 2_000)

    def test_rows_survive_the_elision_pass(self):
        # Elision must not replace the row list that row-dropping just
        # trimmed — that would swap real results for a stub.
        result = {'data': _rows(50), 'context': {str(i): i for i in range(5_000)}}
        out, _ = apply_result_budget(result, max_bytes=8_000)
        self.assertIsInstance(out['data'], list)
        self.assertGreaterEqual(len(out['data']), 1)

    def test_non_standard_row_key_is_found(self):
        result = {'notes': _rows(100)}
        out, _ = apply_result_budget(result, max_bytes=5_000)
        self.assertLess(len(out['notes']), 100)
        self.assertEqual('notes', out['_truncated']['rows_key'])


class TestResultBudgetTextClipping(TestCase):

    def test_long_string_is_clipped_before_rows_are_dropped(self):
        result = {'data': [{'id': 1, 'body': 'x' * 60_000}]}
        out, _ = apply_result_budget(result, max_bytes=20_000)
        self.assertEqual(1, len(out['data']))
        self.assertIn('clipped', out['data'][0]['body'])

    def test_clipped_value_states_the_original_length(self):
        result = {'body': 'x' * 60_000}
        out, _ = apply_result_budget(result, max_bytes=20_000)
        self.assertIn('60000 chars total', out['body'])

    def test_clipped_field_names_are_reported(self):
        result = {'body': 'x' * 60_000}
        out, _ = apply_result_budget(result, max_bytes=20_000)
        self.assertIn('body', out['_truncated']['fields_clipped'])


class TestResultBudgetElision(TestCase):

    def test_structured_blob_is_elided_when_clipping_is_not_enough(self):
        # Bulk in the structure, not in any one string: clipping cannot
        # shrink this, so the whole value has to go.
        result = {'alert_id': 1,
                  'alert_context': {str(i): i for i in range(20_000)}}
        out, _ = apply_result_budget(result, max_bytes=10_000)
        self.assertLessEqual(_size(out), 10_000)
        self.assertIn('alert_context', out['_truncated']['fields_elided'])

    def test_elision_keeps_the_identifying_scalars(self):
        result = {'alert_id': 1,
                  'alert_context': {str(i): i for i in range(20_000)}}
        out, _ = apply_result_budget(result, max_bytes=10_000)
        # Without the id the caller cannot even refetch the record.
        self.assertEqual(1, out['alert_id'])


class TestResultBudgetReporting(TestCase):

    def test_truncation_always_carries_a_recovery_hint(self):
        out, report = apply_result_budget({'data': _rows(100)}, max_bytes=5_000)
        self.assertIn('hint', report)
        self.assertEqual(report, out['_truncated'])

    def test_non_dict_result_is_wrapped_so_the_warning_survives(self):
        out, report = apply_result_budget(_rows(100), max_bytes=5_000)
        self.assertIsNotNone(report)
        self.assertIn('_truncated', out)
        self.assertIn('data', out)

    def test_result_stays_json_serialisable(self):
        out, _ = apply_result_budget({'data': _rows(100)}, max_bytes=5_000)
        json.dumps(out)  # must not raise
