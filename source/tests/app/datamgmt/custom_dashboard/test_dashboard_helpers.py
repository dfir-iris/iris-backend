#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in custom_dashboard modules.

_capitalize_label from query_engine and _prepare_dashboard_payload
from custom_dashboard_db are tested without DB or Flask context.
"""

from unittest import TestCase

from app.datamgmt.custom_dashboard.query_engine import _capitalize_label
from app.datamgmt.custom_dashboard.custom_dashboard_db import _prepare_dashboard_payload


# ---------------------------------------------------------------------------
# _capitalize_label
# ---------------------------------------------------------------------------

class TestCapitalizeLabel(TestCase):

    def test_empty_string_returned_unchanged(self):
        self.assertEqual('', _capitalize_label(''))

    def test_simple_label_capitalized(self):
        self.assertEqual('Count', _capitalize_label('count'))

    def test_underscore_replaced_with_space(self):
        result = _capitalize_label('alert_count')
        self.assertEqual('Alert count', result)

    def test_multiple_underscores(self):
        result = _capitalize_label('alert_source_ref')
        self.assertEqual('Alert source ref', result)

    def test_already_capitalized_unchanged(self):
        result = _capitalize_label('Total')
        self.assertEqual('Total', result)

    def test_leading_trailing_underscores_stripped(self):
        # strip() is called before capitalize
        result = _capitalize_label('  count  ')
        self.assertIn('Count', result)

    def test_all_lowercase_label(self):
        result = _capitalize_label('severity_name')
        self.assertEqual('Severity name', result)


# ---------------------------------------------------------------------------
# _prepare_dashboard_payload
# ---------------------------------------------------------------------------

class TestPrepareDashboardPayload(TestCase):

    def test_flat_widgets_returned(self):
        data = {
            'name': 'My Dashboard',
            'widgets': [
                {'name': 'Widget A'},
                {'name': 'Widget B'},
            ]
        }
        result = _prepare_dashboard_payload(data)
        self.assertEqual(2, len(result))

    def test_widget_gets_layout_section_id(self):
        data = {'name': 'Dash', 'widgets': [{'name': 'W'}]}
        result = _prepare_dashboard_payload(data)
        self.assertIn('section_id', result[0]['layout'])

    def test_widget_index_assigned(self):
        data = {'name': 'Dash', 'widgets': [{'name': 'A'}, {'name': 'B'}]}
        result = _prepare_dashboard_payload(data)
        self.assertEqual(0, result[0]['layout']['widget_index'])
        self.assertEqual(1, result[1]['layout']['widget_index'])

    def test_empty_widgets_returns_empty(self):
        data = {'name': 'Dash', 'widgets': []}
        result = _prepare_dashboard_payload(data)
        self.assertEqual([], result)

    def test_sections_flatten_widgets(self):
        data = {
            'name': 'Dash',
            'sections': [
                {'id': 's1', 'title': 'Sec1', 'widgets': [{'name': 'W1'}, {'name': 'W2'}]},
            ]
        }
        result = _prepare_dashboard_payload(data)
        self.assertEqual(2, len(result))

    def test_section_id_used_in_layout(self):
        data = {
            'sections': [{'id': 'my-section', 'widgets': [{'name': 'W'}]}]
        }
        result = _prepare_dashboard_payload(data)
        self.assertEqual('my-section', result[0]['layout']['section_id'])

    def test_section_index_zero_for_first_section(self):
        data = {
            'sections': [{'id': 's1', 'widgets': [{'name': 'W'}]}]
        }
        result = _prepare_dashboard_payload(data)
        self.assertEqual(0, result[0]['layout']['section_index'])

    def test_widget_size_from_options(self):
        data = {
            'widgets': [{'name': 'W', 'options': {'widget_size': 'large'}}]
        }
        result = _prepare_dashboard_payload(data)
        self.assertEqual('large', result[0]['layout']['widget_size'])

    def test_data_widgets_key_updated_in_place(self):
        data = {'name': 'D', 'widgets': [{'name': 'W'}]}
        _prepare_dashboard_payload(data)
        self.assertEqual(1, len(data['widgets']))

    def test_data_sections_key_updated(self):
        data = {'name': 'D', 'widgets': [{'name': 'W'}]}
        _prepare_dashboard_payload(data)
        self.assertIn('sections', data)
        self.assertEqual(1, len(data['sections']))
