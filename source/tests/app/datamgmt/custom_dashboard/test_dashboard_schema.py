#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the custom dashboard marshmallow schemas.

All schemas are pure marshmallow — no DB, no Flask context needed.
"""

from unittest import TestCase

from marshmallow import ValidationError

from app.datamgmt.custom_dashboard.schema import (
    CustomDashboardSchema,
    DashboardFilterSchema,
    DashboardWidgetFieldSchema,
    DashboardWidgetSchema,
)
from app.datamgmt.custom_dashboard.named_aggregations import list_named_aggregations


# Minimal valid building blocks reused across test classes.
_FILTER = {'table': 'alerts', 'column': 'status', 'operator': 'eq', 'value': 'open'}
_FIELD = {'table': 'alerts', 'column': 'id', 'aggregation': 'count'}
_WIDGET = {'name': 'My Widget', 'chart_type': 'bar', 'fields': [_FIELD]}
_DASHBOARD = {'name': 'Test', 'widgets': [_WIDGET]}


class TestDashboardFilterSchema(TestCase):

    def _load(self, data):
        return DashboardFilterSchema().load(data)

    def test_valid_filter_loads(self):
        result = self._load(_FILTER)
        self.assertEqual('alerts', result['table'])

    def test_missing_table_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'column': 'status', 'operator': 'eq', 'value': 'open'})

    def test_missing_column_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'table': 't', 'operator': 'eq', 'value': 'open'})

    def test_missing_operator_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'table': 't', 'column': 'c', 'value': 'v'})

    def test_missing_value_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'table': 't', 'column': 'c', 'operator': 'eq'})

    def test_invalid_operator_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'table': 't', 'column': 'c', 'operator': 'LIKE', 'value': 'x'})

    def test_all_valid_operators_accepted(self):
        valid_ops = ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'in', 'nin', 'between', 'contains']
        for op in valid_ops:
            with self.subTest(op=op):
                result = self._load({'table': 't', 'column': 'c', 'operator': op, 'value': 'x'})
                self.assertEqual(op, result['operator'])

    def test_value_can_be_list(self):
        result = self._load({'table': 't', 'column': 'c', 'operator': 'in', 'value': [1, 2]})
        self.assertEqual([1, 2], result['value'])

    def test_value_can_be_int(self):
        result = self._load({'table': 't', 'column': 'c', 'operator': 'eq', 'value': 42})
        self.assertEqual(42, result['value'])


class TestDashboardWidgetFieldSchema(TestCase):

    def _load(self, data):
        return DashboardWidgetFieldSchema().load(data)

    def test_minimal_valid_field_loads(self):
        result = self._load({'table': 't', 'column': 'c'})
        self.assertEqual('t', result['table'])

    def test_with_aggregation(self):
        result = self._load({'table': 't', 'column': 'c', 'aggregation': 'sum'})
        self.assertEqual('sum', result['aggregation'])

    def test_invalid_aggregation_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'table': 't', 'column': 'c', 'aggregation': 'median'})

    def test_all_valid_aggregations_accepted(self):
        valid = ['count', 'sum', 'avg', 'min', 'max', 'ratio']
        for agg in valid:
            with self.subTest(agg=agg):
                result = self._load({'table': 't', 'column': 'c', 'aggregation': agg})
                self.assertEqual(agg, result['aggregation'])

    def test_aggregation_allows_none(self):
        result = self._load({'table': 't', 'column': 'c', 'aggregation': None})
        self.assertIsNone(result.get('aggregation'))

    def test_alias_optional(self):
        result = self._load({'table': 't', 'column': 'c', 'alias': 'my_alias'})
        self.assertEqual('my_alias', result['alias'])

    def test_nested_filter_accepted(self):
        result = self._load({'table': 't', 'column': 'c', 'filter': _FILTER})
        self.assertEqual('eq', result['filter']['operator'])

    def test_missing_table_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'column': 'c'})

    def test_missing_column_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'table': 't'})


class TestDashboardWidgetSchema(TestCase):

    def _load(self, data):
        return DashboardWidgetSchema().load(data)

    def test_minimal_valid_widget(self):
        result = self._load(_WIDGET)
        self.assertEqual('My Widget', result['name'])

    def test_missing_name_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'chart_type': 'bar', 'fields': [_FIELD]})

    def test_missing_chart_type_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'name': 'W', 'fields': [_FIELD]})

    def test_invalid_chart_type_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'name': 'W', 'chart_type': 'scatter', 'fields': [_FIELD]})

    def test_all_valid_chart_types_accepted(self):
        valid = ['line', 'bar', 'pie', 'number', 'percentage', 'table', 'timechart']
        for ct in valid:
            with self.subTest(ct=ct):
                result = self._load({'name': 'W', 'chart_type': ct, 'fields': [_FIELD]})
                self.assertEqual(ct, result['chart_type'])

    def test_empty_fields_list_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'name': 'W', 'chart_type': 'bar', 'fields': []})

    def test_missing_fields_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'name': 'W', 'chart_type': 'bar'})

    def test_optional_filters_accepted(self):
        data = dict(_WIDGET, filters=[_FILTER])
        result = self._load(data)
        self.assertEqual(1, len(result['filters']))

    def test_optional_group_by_accepted(self):
        data = dict(_WIDGET, group_by=['status'])
        result = self._load(data)
        self.assertEqual(['status'], result['group_by'])

    def test_unknown_keys_excluded(self):
        data = dict(_WIDGET, unknown_key='ignored')
        result = self._load(data)
        self.assertNotIn('unknown_key', result)


class TestCustomDashboardSchema(TestCase):

    def _load(self, data):
        return CustomDashboardSchema().load(data)

    def test_minimal_valid_dashboard(self):
        result = self._load(_DASHBOARD)
        self.assertEqual('Test', result['name'])

    def test_missing_name_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'widgets': [_WIDGET]})

    def test_no_widgets_and_no_sections_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'name': 'X'})

    def test_empty_widgets_and_no_sections_raises(self):
        with self.assertRaises(ValidationError):
            self._load({'name': 'X', 'widgets': []})

    def test_section_with_widget_is_sufficient(self):
        data = {
            'name': 'X',
            'sections': [{'widgets': [_WIDGET]}],
        }
        result = self._load(data)
        self.assertEqual(1, len(result['sections']))

    def test_description_optional(self):
        data = dict(_DASHBOARD, description='A description')
        result = self._load(data)
        self.assertEqual('A description', result['description'])

    def test_is_shared_optional(self):
        data = dict(_DASHBOARD, is_shared=True)
        result = self._load(data)
        self.assertTrue(result['is_shared'])

    def test_unknown_keys_excluded(self):
        data = dict(_DASHBOARD, surprise='value')
        result = self._load(data)
        self.assertNotIn('surprise', result)


class TestListNamedAggregations(TestCase):

    def test_returns_list(self):
        result = list_named_aggregations()
        self.assertIsInstance(result, list)

    def test_each_entry_has_name_label_value_format(self):
        for entry in list_named_aggregations():
            with self.subTest(entry=entry):
                self.assertIn('name', entry)
                self.assertIn('label', entry)
                self.assertIn('value_format', entry)

    def test_known_aggregation_mttd_present(self):
        names = [e['name'] for e in list_named_aggregations()]
        self.assertIn('mttd_seconds', names)

    def test_known_aggregation_mttr_present(self):
        names = [e['name'] for e in list_named_aggregations()]
        self.assertIn('mttr_seconds', names)

    def test_known_aggregation_false_positive_rate_present(self):
        names = [e['name'] for e in list_named_aggregations()]
        self.assertIn('false_positive_rate', names)

    def test_known_aggregation_escalation_rate_present(self):
        names = [e['name'] for e in list_named_aggregations()]
        self.assertIn('escalation_rate', names)

    def test_names_are_unique(self):
        names = [e['name'] for e in list_named_aggregations()]
        self.assertEqual(len(names), len(set(names)))
