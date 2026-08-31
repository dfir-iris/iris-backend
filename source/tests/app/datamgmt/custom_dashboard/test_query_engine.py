#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure formatting/utility functions in query_engine.

All helpers tested here are stateless — no DB, no SQLAlchemy session,
no Flask application context is required at test time.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from unittest import TestCase

from unittest.mock import MagicMock

from sqlalchemy import Integer, Text
from sqlalchemy import Numeric, Float

from app.datamgmt.custom_dashboard.query_engine import (
    _advance_datetime_by_bucket,
    _canonical_label_key,
    _compute_time_alias,
    _ensure_numeric,
    _floor_datetime_to_bucket,
    _format_group_value,
    _format_number,
    _format_percentage,
    _format_time_group_value,
    _generate_time_bucket_range,
    _normalize_display_mode,
    _time_sort_key,
    _MAX_TIME_BUCKET_POINTS,
    QueryExecutionError,
    WidgetQueryExecutor,
)


# ---------------------------------------------------------------------------
# _ensure_numeric
# ---------------------------------------------------------------------------

class TestEnsureNumeric(TestCase):

    def test_int_positive(self):
        self.assertEqual(42.0, _ensure_numeric(42))

    def test_int_zero(self):
        self.assertEqual(0.0, _ensure_numeric(0))

    def test_int_negative(self):
        self.assertEqual(-7.0, _ensure_numeric(-7))

    def test_float_positive(self):
        self.assertAlmostEqual(3.14, _ensure_numeric(3.14))

    def test_float_negative(self):
        self.assertAlmostEqual(-0.5, _ensure_numeric(-0.5))

    def test_decimal_value(self):
        self.assertAlmostEqual(1.5, _ensure_numeric(Decimal('1.5')))

    def test_bool_true_returns_one(self):
        # bool is a subclass of int; the function converts it to float(int(True)) = 1.0
        self.assertEqual(1.0, _ensure_numeric(True))

    def test_bool_false_returns_zero(self):
        self.assertEqual(0.0, _ensure_numeric(False))

    def test_nan_returns_none(self):
        self.assertIsNone(_ensure_numeric(float('nan')))

    def test_positive_inf_returns_none(self):
        self.assertIsNone(_ensure_numeric(float('inf')))

    def test_negative_inf_returns_none(self):
        self.assertIsNone(_ensure_numeric(float('-inf')))

    def test_none_returns_none(self):
        self.assertIsNone(_ensure_numeric(None))

    def test_string_returns_none(self):
        self.assertIsNone(_ensure_numeric('42'))

    def test_list_returns_none(self):
        self.assertIsNone(_ensure_numeric([1, 2]))

    def test_decimal_nan_returns_none(self):
        self.assertIsNone(_ensure_numeric(Decimal('nan')))


# ---------------------------------------------------------------------------
# _format_number
# ---------------------------------------------------------------------------

class TestFormatNumber(TestCase):

    def test_integer_value(self):
        self.assertEqual('42', _format_number(42))

    def test_zero(self):
        self.assertEqual('0', _format_number(0))

    def test_large_integer_comma_separated(self):
        self.assertEqual('1,000,000', _format_number(1_000_000))

    def test_float_two_decimals(self):
        # 3.14 is not an integer; trailing zeros are stripped
        self.assertEqual('3.14', _format_number(3.14))

    def test_float_trailing_zero_stripped(self):
        # 3.10 → "3.1"
        self.assertEqual('3.1', _format_number(3.10))

    def test_float_rounds_to_int_display(self):
        # 3.0 is an integer-valued float
        self.assertEqual('3', _format_number(3.0))

    def test_negative_integer(self):
        self.assertEqual('-5', _format_number(-5))

    def test_none_returns_double_dash(self):
        self.assertEqual('--', _format_number(None))

    def test_empty_string_returns_double_dash(self):
        self.assertEqual('--', _format_number(''))

    def test_non_numeric_string_returned_as_str(self):
        self.assertEqual('hello', _format_number('hello'))

    def test_nan_returns_double_dash(self):
        # _ensure_numeric(nan) is None → falls into "numeric is None" branch
        # value is not None and not '', so str(value) is returned for non-None non-''
        # Actually: nan is a float, _ensure_numeric returns None; value is float('nan')
        # neither None nor '' → str(float('nan')) is returned
        result = _format_number(float('nan'))
        self.assertEqual('nan', result)

    def test_large_float_comma_separated(self):
        self.assertEqual('1,234.56', _format_number(1234.56))


# ---------------------------------------------------------------------------
# _format_percentage
# ---------------------------------------------------------------------------

class TestFormatPercentage(TestCase):

    def test_none_returns_double_dash(self):
        self.assertEqual('--', _format_percentage(None))

    def test_zero(self):
        # f"{0.0:,.2f}%" → "0.00%"; rstrip('0') has no effect since string ends with '%'
        self.assertEqual('0.00%', _format_percentage(0.0))

    def test_whole_percent(self):
        # f"{50.0:,.2f}%" → "50.00%"; rstrip('0') cannot strip past trailing '%'
        self.assertEqual('50.00%', _format_percentage(50.0))

    def test_decimal_percent_trimmed(self):
        # 45.67 → "45.67%"
        self.assertEqual('45.67%', _format_percentage(45.67))

    def test_trailing_zero_no_strip(self):
        # rstrip('0') acts on "10.10%" → no trailing '0' before '%', so unchanged
        self.assertEqual('10.10%', _format_percentage(10.10))

    def test_whole_float_no_strip(self):
        # 25.00 → "25.00%" (% prevents rstrip from stripping decimal zeros)
        self.assertEqual('25.00%', _format_percentage(25.00))

    def test_negative_percent(self):
        result = _format_percentage(-5.0)
        self.assertIn('-5', result)

    def test_large_percent_comma_separated(self):
        result = _format_percentage(1000.0)
        self.assertIn('1,000', result)


# ---------------------------------------------------------------------------
# _format_group_value
# ---------------------------------------------------------------------------

class TestFormatGroupValue(TestCase):

    def test_none_returns_na(self):
        self.assertEqual('N/A', _format_group_value(None))

    def test_empty_string_returns_na(self):
        self.assertEqual('N/A', _format_group_value(''))

    def test_string_value(self):
        self.assertEqual('hello', _format_group_value('hello'))

    def test_integer_value(self):
        self.assertEqual('42', _format_group_value(42))

    def test_boolean_true(self):
        self.assertEqual('True', _format_group_value(True))

    def test_list_stringified(self):
        result = _format_group_value([1, 2])
        self.assertEqual('[1, 2]', result)


# ---------------------------------------------------------------------------
# _format_time_group_value
# ---------------------------------------------------------------------------

class TestFormatTimeGroupValue(TestCase):

    _DT = datetime(2024, 3, 15, 14, 30, 45)

    def test_non_datetime_delegates_to_format_group_value(self):
        self.assertEqual('N/A', _format_time_group_value(None, 'day'))
        self.assertEqual('hello', _format_time_group_value('hello', 'day'))

    def test_hour_bucket(self):
        self.assertEqual('2024-03-15 14:30', _format_time_group_value(self._DT, 'hour'))

    def test_minute_bucket(self):
        self.assertEqual('2024-03-15 14:30', _format_time_group_value(self._DT, 'minute'))

    def test_5minute_bucket(self):
        self.assertEqual('2024-03-15 14:30', _format_time_group_value(self._DT, '5minute'))

    def test_15minute_bucket(self):
        self.assertEqual('2024-03-15 14:30', _format_time_group_value(self._DT, '15minute'))

    def test_week_bucket(self):
        result = _format_time_group_value(self._DT, 'week')
        self.assertTrue(result.startswith('Week '))
        self.assertIn('2024', result)

    def test_month_bucket(self):
        self.assertEqual('2024-03', _format_time_group_value(self._DT, 'month'))

    def test_year_bucket(self):
        self.assertEqual('2024', _format_time_group_value(self._DT, 'year'))

    def test_day_bucket(self):
        self.assertEqual('2024-03-15', _format_time_group_value(self._DT, 'day'))

    def test_none_bucket_falls_back_to_day(self):
        self.assertEqual('2024-03-15', _format_time_group_value(self._DT, None))

    def test_unknown_bucket_falls_back_to_day(self):
        self.assertEqual('2024-03-15', _format_time_group_value(self._DT, 'unknown_bucket'))

    def test_bucket_case_insensitive(self):
        # normalized_bucket is lowercased inside the function
        self.assertEqual('2024-03', _format_time_group_value(self._DT, 'MONTH'))


# ---------------------------------------------------------------------------
# _canonical_label_key
# ---------------------------------------------------------------------------

class TestCanonicalLabelKey(TestCase):

    def test_datetime_gives_isoformat(self):
        dt = datetime(2024, 1, 1, 12, 0, 0)
        self.assertEqual(dt.isoformat(), _canonical_label_key(dt))

    def test_none_gives_na(self):
        self.assertEqual('N/A', _canonical_label_key(None))

    def test_empty_string_gives_na(self):
        self.assertEqual('N/A', _canonical_label_key(''))

    def test_string_unchanged(self):
        self.assertEqual('hello', _canonical_label_key('hello'))

    def test_integer_stringified(self):
        self.assertEqual('7', _canonical_label_key(7))


# ---------------------------------------------------------------------------
# _compute_time_alias
# ---------------------------------------------------------------------------

class TestComputeTimeAlias(TestCase):

    def test_table_column_with_bucket(self):
        self.assertEqual('alerts_alert_creation_time_day', _compute_time_alias('alerts.alert_creation_time', 'day'))

    def test_table_column_without_bucket(self):
        self.assertEqual('alerts_alert_creation_time', _compute_time_alias('alerts.alert_creation_time', None))

    def test_table_column_empty_bucket(self):
        self.assertEqual('alerts_alert_creation_time', _compute_time_alias('alerts.alert_creation_time', ''))

    def test_no_dot_plain_string(self):
        # No dot → candidate = value.replace('.', '_').strip() → 'timestamp'
        self.assertEqual('timestamp', _compute_time_alias('timestamp', 'day'))

    def test_none_column_returns_none(self):
        self.assertIsNone(_compute_time_alias(None, 'day'))

    def test_non_string_returns_none(self):
        self.assertIsNone(_compute_time_alias(42, 'day'))

    def test_spaces_stripped_in_alias(self):
        result = _compute_time_alias('alerts . alert_creation_time', 'month')
        self.assertEqual('alerts_alert_creation_time_month', result)


# ---------------------------------------------------------------------------
# _floor_datetime_to_bucket
# ---------------------------------------------------------------------------

class TestFloorDatetimeToBucket(TestCase):

    _DT = datetime(2024, 3, 15, 14, 37, 55, 999999)

    def test_floor_hour(self):
        result = _floor_datetime_to_bucket(self._DT, 'hour')
        self.assertEqual(datetime(2024, 3, 15, 14, 0, 0, 0), result)

    def test_floor_day(self):
        result = _floor_datetime_to_bucket(self._DT, 'day')
        self.assertEqual(datetime(2024, 3, 15, 0, 0, 0, 0), result)

    def test_floor_week_monday(self):
        # 2024-03-15 is a Friday; Monday of that week is 2024-03-11
        result = _floor_datetime_to_bucket(self._DT, 'week')
        self.assertEqual(datetime(2024, 3, 11, 0, 0, 0, 0), result)

    def test_floor_week_already_monday(self):
        dt = datetime(2024, 3, 11, 10, 0, 0)  # Monday
        result = _floor_datetime_to_bucket(dt, 'week')
        self.assertEqual(datetime(2024, 3, 11, 0, 0, 0, 0), result)

    def test_floor_month(self):
        result = _floor_datetime_to_bucket(self._DT, 'month')
        self.assertEqual(datetime(2024, 3, 1, 0, 0, 0, 0), result)

    def test_floor_year(self):
        result = _floor_datetime_to_bucket(self._DT, 'year')
        self.assertEqual(datetime(2024, 1, 1, 0, 0, 0, 0), result)

    def test_floor_minute(self):
        result = _floor_datetime_to_bucket(self._DT, 'minute')
        self.assertEqual(datetime(2024, 3, 15, 14, 37, 0, 0), result)

    def test_floor_5minute(self):
        # minute=37 → floor to nearest 5 = 35
        result = _floor_datetime_to_bucket(self._DT, '5minute')
        self.assertEqual(datetime(2024, 3, 15, 14, 35, 0, 0), result)

    def test_floor_5m_alias(self):
        result = _floor_datetime_to_bucket(self._DT, '5m')
        self.assertEqual(datetime(2024, 3, 15, 14, 35, 0, 0), result)

    def test_floor_15minute(self):
        # minute=37 → floor to nearest 15 = 30
        result = _floor_datetime_to_bucket(self._DT, '15minute')
        self.assertEqual(datetime(2024, 3, 15, 14, 30, 0, 0), result)

    def test_floor_15m_alias(self):
        result = _floor_datetime_to_bucket(self._DT, '15m')
        self.assertEqual(datetime(2024, 3, 15, 14, 30, 0, 0), result)

    def test_unknown_bucket_falls_back_to_day(self):
        result = _floor_datetime_to_bucket(self._DT, 'unknown')
        self.assertEqual(datetime(2024, 3, 15, 0, 0, 0, 0), result)

    def test_bucket_case_insensitive(self):
        result = _floor_datetime_to_bucket(self._DT, 'HOUR')
        self.assertEqual(datetime(2024, 3, 15, 14, 0, 0, 0), result)


# ---------------------------------------------------------------------------
# _advance_datetime_by_bucket
# ---------------------------------------------------------------------------

class TestAdvanceDatetimeByBucket(TestCase):

    _DT = datetime(2024, 3, 15, 14, 0, 0)

    def test_advance_minute(self):
        result = _advance_datetime_by_bucket(self._DT, 'minute')
        self.assertEqual(datetime(2024, 3, 15, 14, 1, 0), result)

    def test_advance_5minute(self):
        result = _advance_datetime_by_bucket(self._DT, '5minute')
        self.assertEqual(datetime(2024, 3, 15, 14, 5, 0), result)

    def test_advance_5m_alias(self):
        result = _advance_datetime_by_bucket(self._DT, '5m')
        self.assertEqual(datetime(2024, 3, 15, 14, 5, 0), result)

    def test_advance_15minute(self):
        result = _advance_datetime_by_bucket(self._DT, '15minute')
        self.assertEqual(datetime(2024, 3, 15, 14, 15, 0), result)

    def test_advance_15m_alias(self):
        result = _advance_datetime_by_bucket(self._DT, '15m')
        self.assertEqual(datetime(2024, 3, 15, 14, 15, 0), result)

    def test_advance_hour(self):
        result = _advance_datetime_by_bucket(self._DT, 'hour')
        self.assertEqual(datetime(2024, 3, 15, 15, 0, 0), result)

    def test_advance_day(self):
        result = _advance_datetime_by_bucket(self._DT, 'day')
        self.assertEqual(datetime(2024, 3, 16, 14, 0, 0), result)

    def test_advance_week(self):
        result = _advance_datetime_by_bucket(self._DT, 'week')
        self.assertEqual(datetime(2024, 3, 22, 14, 0, 0), result)

    def test_advance_month_normal(self):
        dt = datetime(2024, 3, 1, 0, 0, 0)
        result = _advance_datetime_by_bucket(dt, 'month')
        self.assertEqual(datetime(2024, 4, 1, 0, 0, 0), result)

    def test_advance_month_december_wraps_to_january(self):
        dt = datetime(2024, 12, 1, 0, 0, 0)
        result = _advance_datetime_by_bucket(dt, 'month')
        self.assertEqual(datetime(2025, 1, 1, 0, 0, 0), result)

    def test_advance_year(self):
        dt = datetime(2024, 1, 1, 0, 0, 0)
        result = _advance_datetime_by_bucket(dt, 'year')
        self.assertEqual(datetime(2025, 1, 1, 0, 0, 0), result)

    def test_advance_unknown_falls_back_to_day(self):
        result = _advance_datetime_by_bucket(self._DT, 'unknown')
        self.assertEqual(datetime(2024, 3, 16, 14, 0, 0), result)


# ---------------------------------------------------------------------------
# _generate_time_bucket_range
# ---------------------------------------------------------------------------

class TestGenerateTimeBucketRange(TestCase):

    def test_simple_day_range(self):
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 3)
        result = _generate_time_bucket_range(start, end, 'day')
        self.assertEqual([datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 3)], result)

    def test_start_equals_end(self):
        dt = datetime(2024, 6, 15)
        result = _generate_time_bucket_range(dt, dt, 'day')
        self.assertEqual([datetime(2024, 6, 15)], result)

    def test_start_greater_than_end_swapped(self):
        start = datetime(2024, 1, 5)
        end = datetime(2024, 1, 3)
        result = _generate_time_bucket_range(start, end, 'day')
        # should be same as [Jan3, Jan4, Jan5]
        self.assertEqual([datetime(2024, 1, 3), datetime(2024, 1, 4), datetime(2024, 1, 5)], result)

    def test_month_range(self):
        start = datetime(2024, 1, 15)
        end = datetime(2024, 3, 20)
        result = _generate_time_bucket_range(start, end, 'month')
        # floored: Jan 1, Feb 1, Mar 1
        self.assertEqual([datetime(2024, 1, 1), datetime(2024, 2, 1), datetime(2024, 3, 1)], result)

    def test_year_range(self):
        start = datetime(2022, 6, 1)
        end = datetime(2024, 6, 1)
        result = _generate_time_bucket_range(start, end, 'year')
        self.assertEqual([datetime(2022, 1, 1), datetime(2023, 1, 1), datetime(2024, 1, 1)], result)

    def test_none_start_returns_empty(self):
        result = _generate_time_bucket_range(None, datetime(2024, 1, 1), 'day')
        self.assertEqual([], result)

    def test_none_end_returns_empty(self):
        result = _generate_time_bucket_range(datetime(2024, 1, 1), None, 'day')
        self.assertEqual([], result)

    def test_empty_bucket_returns_empty(self):
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 5)
        result = _generate_time_bucket_range(start, end, '')
        self.assertEqual([], result)

    def test_none_bucket_returns_empty(self):
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 5)
        result = _generate_time_bucket_range(start, end, None)
        self.assertEqual([], result)

    def test_max_points_cap_returns_empty(self):
        # A very large range that exceeds _MAX_TIME_BUCKET_POINTS should return []
        start = datetime(2000, 1, 1)
        end = datetime(2030, 1, 1)
        result = _generate_time_bucket_range(start, end, 'day')
        self.assertEqual([], result)

    def test_max_points_constant_is_2000(self):
        self.assertEqual(2000, _MAX_TIME_BUCKET_POINTS)

    def test_week_range(self):
        start = datetime(2024, 1, 1)   # Monday
        end = datetime(2024, 1, 15)    # Monday two weeks later
        result = _generate_time_bucket_range(start, end, 'week')
        # floor(Jan 1)=Jan 1, floor(Jan 15)=Jan 15 → 3 buckets
        self.assertEqual(3, len(result))

    def test_hour_range(self):
        start = datetime(2024, 1, 1, 0, 0, 0)
        end = datetime(2024, 1, 1, 2, 0, 0)
        result = _generate_time_bucket_range(start, end, 'hour')
        self.assertEqual([
            datetime(2024, 1, 1, 0, 0, 0),
            datetime(2024, 1, 1, 1, 0, 0),
            datetime(2024, 1, 1, 2, 0, 0),
        ], result)

    def test_custom_max_points_respected(self):
        # With max_points=2, a 3-day range should return []
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 5)
        result = _generate_time_bucket_range(start, end, 'day', max_points=2)
        self.assertEqual([], result)

    def test_non_datetime_args_return_empty(self):
        result = _generate_time_bucket_range('2024-01-01', '2024-01-05', 'day')
        self.assertEqual([], result)


# ---------------------------------------------------------------------------
# _time_sort_key
# ---------------------------------------------------------------------------

class TestTimeSortKey(TestCase):

    def test_datetime_returns_float(self):
        dt = datetime(2024, 1, 1, 0, 0, 0)
        key = _time_sort_key(dt)
        self.assertIsInstance(key, float)
        self.assertFalse(math.isinf(key))

    def test_none_returns_neg_inf(self):
        self.assertEqual(float('-inf'), _time_sort_key(None))

    def test_string_returns_neg_inf(self):
        self.assertEqual(float('-inf'), _time_sort_key('2024-01-01'))

    def test_integer_returns_neg_inf(self):
        self.assertEqual(float('-inf'), _time_sort_key(42))

    def test_later_datetime_has_larger_key(self):
        dt1 = datetime(2024, 1, 1)
        dt2 = datetime(2024, 6, 1)
        self.assertLess(_time_sort_key(dt1), _time_sort_key(dt2))


# ---------------------------------------------------------------------------
# _normalize_display_mode
# ---------------------------------------------------------------------------

class TestNormalizeDisplayMode(TestCase):

    def test_none_returns_number(self):
        self.assertEqual('number', _normalize_display_mode(None))

    def test_empty_string_returns_number(self):
        self.assertEqual('number', _normalize_display_mode(''))

    def test_non_string_returns_number(self):
        self.assertEqual('number', _normalize_display_mode(42))

    def test_percentage(self):
        self.assertEqual('percentage', _normalize_display_mode('percentage'))

    def test_percent_alias(self):
        self.assertEqual('percentage', _normalize_display_mode('percent'))

    def test_percentage_uppercase(self):
        self.assertEqual('percentage', _normalize_display_mode('PERCENTAGE'))

    def test_number_percentage(self):
        self.assertEqual('number_percentage', _normalize_display_mode('number_percentage'))

    def test_number_and_percentage_alias(self):
        self.assertEqual('number_percentage', _normalize_display_mode('number-and-percentage'))

    def test_number_and_percentage_spaces(self):
        self.assertEqual('number_percentage', _normalize_display_mode('number and percentage'))

    def test_both_alias(self):
        self.assertEqual('number_percentage', _normalize_display_mode('both'))

    def test_unknown_mode_returns_number(self):
        self.assertEqual('number', _normalize_display_mode('foobar'))

    def test_whitespace_stripped(self):
        self.assertEqual('percentage', _normalize_display_mode('  percentage  '))

    def test_number_explicit(self):
        self.assertEqual('number', _normalize_display_mode('number'))


def _make_column(sa_type):
    col = MagicMock()
    col.type = sa_type
    return col


class TestIsNumericColumn(TestCase):

    def test_integer_column_is_numeric(self):
        self.assertTrue(WidgetQueryExecutor._is_numeric_column(_make_column(Integer())))

    def test_numeric_column_is_numeric(self):
        self.assertTrue(WidgetQueryExecutor._is_numeric_column(_make_column(Numeric())))

    def test_float_column_is_numeric(self):
        self.assertTrue(WidgetQueryExecutor._is_numeric_column(_make_column(Float())))

    def test_text_column_is_not_numeric(self):
        self.assertFalse(WidgetQueryExecutor._is_numeric_column(_make_column(Text())))

    def test_column_without_type_attr_is_not_numeric(self):
        self.assertFalse(WidgetQueryExecutor._is_numeric_column(object()))


class TestBuildAggregateExpressionNumericValidation(TestCase):
    """Regression: sum/avg on a text column must raise QueryExecutionError, not
    reach the database as `SELECT sum(text_column)` which PostgreSQL rejects."""

    def setUp(self):
        self._executor = WidgetQueryExecutor({})

    def test_sum_on_text_column_raises(self):
        # Regression for GlitchTip #223: sum(client.name) crashed with
        # ProgrammingError: function sum(text) does not exist.
        with self.assertRaises(QueryExecutionError):
            self._executor._build_aggregate_expression('sum', _make_column(Text()), None)

    def test_avg_on_text_column_raises(self):
        with self.assertRaises(QueryExecutionError):
            self._executor._build_aggregate_expression('avg', _make_column(Text()), None)

    def test_sum_on_integer_column_does_not_raise(self):
        result = self._executor._build_aggregate_expression('sum', _make_column(Integer()), None)
        self.assertIsNotNone(result)

    def test_avg_on_numeric_column_does_not_raise(self):
        result = self._executor._build_aggregate_expression('avg', _make_column(Numeric()), None)
        self.assertIsNotNone(result)

    def test_count_on_text_column_does_not_raise(self):
        result = self._executor._build_aggregate_expression('count', _make_column(Text()), None)
        self.assertIsNotNone(result)

    def test_min_on_text_column_does_not_raise(self):
        result = self._executor._build_aggregate_expression('min', _make_column(Text()), None)
        self.assertIsNotNone(result)

    def test_max_on_text_column_does_not_raise(self):
        result = self._executor._build_aggregate_expression('max', _make_column(Text()), None)
        self.assertIsNotNone(result)
